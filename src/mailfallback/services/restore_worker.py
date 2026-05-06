import contextlib
import imaplib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import Account, JobStatus, RestoreJob, User
from mailfallback.security import decrypt_credentials
from mailfallback.services.dovecot_auth import create_temp_imap_user, delete_temp_imap_user
from mailfallback.services.imap_check import connect_imap

logger = logging.getLogger(__name__)

_restore_executor: ThreadPoolExecutor | None = None
_cancel_flags: set[str] = set()

RETRY_DELAYS = [1, 3, 10]


def _retry_imap(fn, *args):
    for attempt, delay in enumerate(RETRY_DELAYS):
        try:
            return fn(*args)
        except (imaplib.IMAP4.error, OSError) as e:
            if attempt == len(RETRY_DELAYS) - 1:
                raise
            logger.warning(
                "IMAP error (attempt %d/%d), retrying in %ds: %s",
                attempt + 1,
                len(RETRY_DELAYS),
                delay,
                e,
            )
            import time

            time.sleep(delay)


def get_restore_executor() -> ThreadPoolExecutor:
    global _restore_executor
    if _restore_executor is None:
        _restore_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="restore-worker",
        )
    return _restore_executor


def submit_restore_job(job_id: str) -> None:
    def _run():
        db = SessionLocal()
        try:
            execute_restore_job(db, job_id)
        finally:
            db.close()

    get_restore_executor().submit(_run)


def request_cancel(job_id: str) -> None:
    _cancel_flags.add(job_id)


def execute_restore_job(db: Session, job_id: str) -> None:
    logger.info("Starting restore job %s", job_id)
    job = db.query(RestoreJob).filter(RestoreJob.id == job_id).first()
    if not job:
        logger.warning("Restore job %s not found", job_id)
        return

    source = db.query(Account).filter(Account.id == job.source_account_id).first()
    target = db.query(Account).filter(Account.id == job.target_account_id).first()
    if not source or not target:
        _fail_job(db, job, "Source or target account not found")
        return

    if source.suspended or target.suspended:
        _fail_job(db, job, "Account is suspended")
        return

    target_creds = (
        decrypt_credentials(target.credentials, settings.secret_key) if target.credentials else None
    )
    if not target_creds:
        _fail_job(db, job, "Target account has no credentials")
        return

    job.status = JobStatus.running
    job.started_at = datetime.now(UTC)
    db.commit()

    src_conn = None
    tgt_conn = None
    temp_username = None
    try:
        temp_username, temp_password = create_temp_imap_user(db, [source.id])

        logger.info("Restore %s: connecting to Dovecot as %s", job_id, temp_username)
        src_conn = connect_imap(
            settings.dovecot_imap_host,
            settings.dovecot_imap_port,
            "NONE",
            temp_username,
            temp_password,
        )
        logger.info("Restore %s: Dovecot connected OK", job_id)

        if target.auth_type.value == "oauth2":
            tgt_password = _refresh_target_token(target_creds, db, target)
            if not tgt_password:
                _fail_job(db, job, "Failed to refresh OAuth2 token for target")
                return
        else:
            tgt_password = target_creds

        logger.info(
            "Restore %s: connecting to target %s:%s as %s",
            job_id,
            target.imap_host,
            target.imap_port,
            target.imap_user or target.email_address,
        )
        tgt_conn = connect_imap(
            target.imap_host,
            target.imap_port,
            target.tls_type or "IMAPS",
            target.imap_user or target.email_address,
            tgt_password,
        )
        logger.info("Restore %s: target connected OK", job_id)

        folders = _resolve_folders(src_conn, source, job)
        logger.info(
            "Restore %s: resolved %d folders: %s", job_id, len(folders), [s for _, s in folders]
        )
        if not folders:
            _fail_job(db, job, "No folders found to restore")
            return

        logger.info(
            "Restore %s: mode=%s, selected_uids=%s, selected_folders=%s, folder_mapping=%s",
            job_id,
            job.restore_mode.value,
            job.selected_uids,
            job.selected_folders,
            job.folder_mapping,
        )
        _execute_restore(db, job, src_conn, tgt_conn, folders)

    except (imaplib.IMAP4.error, OSError) as e:
        _fail_job(db, job, f"IMAP error: {e}")
    except Exception as e:
        _fail_job(db, job, str(e))
    finally:
        _cancel_flags.discard(job_id)
        for conn in (src_conn, tgt_conn):
            if conn:
                with contextlib.suppress(Exception):
                    conn.logout()
        if temp_username:
            with contextlib.suppress(Exception):
                delete_temp_imap_user(db, temp_username)
        if job.status == JobStatus.running:
            if job.restored_messages == 0 and job.failed_messages > 0:
                job.status = JobStatus.failed
                job.error = f"All {job.failed_messages} messages failed"
            else:
                job.status = JobStatus.completed
                if job.failed_messages > 0:
                    job.error = f"{job.failed_messages} messages failed"
            job.completed_at = datetime.now(UTC)
        db.commit()

        try:
            from mailfallback.services.audit_service import log_action

            requester = db.query(User).filter(User.id == job.requested_by).first()
            if requester:
                action = (
                    "restore.complete" if job.status == JobStatus.completed else "restore.failed"
                )
                log_action(
                    db,
                    user=requester,
                    action=action,
                    resource_type="restore",
                    resource_id=job.id,
                    resource_name=f"{source.name} → {target.name}",
                    details={
                        "restored": job.restored_messages,
                        "skipped": job.skipped_messages,
                        "failed": job.failed_messages,
                        "total": job.total_messages,
                    },
                )
        except Exception:
            logger.warning("Failed to write audit log for restore %s", job_id)


def _resolve_folders(src_conn, source, job):
    namespace_prefix = _get_namespace_prefix(source)
    status, folder_data = src_conn.list(f'"{namespace_prefix}"', "*")
    if status != "OK" or not folder_data:
        return []

    all_folders = []
    for item in folder_data:
        if not item or item == b"":
            continue
        decoded = item.decode() if isinstance(item, bytes) else item
        parts = decoded.rsplit('" "', 1)
        if len(parts) == 2:
            folder_name = parts[1].rstrip('"')
            if namespace_prefix:
                folder_name_short = folder_name.removeprefix(namespace_prefix)
            else:
                folder_name_short = folder_name
            all_folders.append((folder_name, folder_name_short))

    if job.selected_folders:
        return [(full, short) for full, short in all_folders if short in job.selected_folders]
    return all_folders


def _get_namespace_prefix(account):
    short_id = account.id[-4:]
    return f"{account.name} ({account.email_address}) [{short_id}]/"


def _execute_restore(db, job, src_conn, tgt_conn, folders):
    total = 0
    for full_folder, _ in folders:
        status, data = src_conn.select(f'"{full_folder}"', readonly=True)
        if status != "OK":
            continue
        count = int(data[0].decode())
        total += count
    if job.selected_uids:
        filtered = sum(len(v) for v in job.selected_uids.values())
        job.total_messages = filtered
    else:
        job.total_messages = total
    db.commit()
    logger.info("Restore: total_messages set to %d", job.total_messages)

    for full_folder, short_folder in folders:
        if job.id in _cancel_flags:
            _fail_job(db, job, "Cancelled by user")
            return

        status, _ = src_conn.select(f'"{full_folder}"', readonly=True)
        if status != "OK":
            continue

        uid_filter = None
        if job.selected_uids and short_folder in job.selected_uids:
            uid_filter = {int(u) for u in job.selected_uids[short_folder]}
            logger.info("Restore: folder %s uid_filter=%s", short_folder, uid_filter)
        elif job.selected_uids:
            logger.info(
                "Restore: folder %s not in selected_uids keys %s, skipping uid filter",
                short_folder,
                list(job.selected_uids.keys()),
            )

        status, data = src_conn.search(None, "ALL")
        if status != "OK" or not data[0]:
            continue
        uids = data[0].split()
        logger.info(
            "Restore: folder %s has %d messages, uid_filter=%s", short_folder, len(uids), uid_filter
        )

        existing_ids = set()
        if job.skip_duplicates:
            target_folder = _map_folder(short_folder, job.folder_mapping)
            existing_ids = _get_existing_message_ids(tgt_conn, target_folder)

        for uid_bytes in uids:
            if job.id in _cancel_flags:
                _fail_job(db, job, "Cancelled by user")
                return

            uid = uid_bytes.decode()
            if uid_filter and int(uid) not in uid_filter:
                continue

            try:
                _restore_single_message(
                    src_conn,
                    tgt_conn,
                    uid,
                    full_folder,
                    short_folder,
                    job,
                    existing_ids,
                    db,
                )
            except Exception:
                job.failed_messages += 1
                logger.warning("Failed to restore UID %s from %s", uid, full_folder, exc_info=True)

            db.commit()


def _restore_single_message(
    src_conn,
    tgt_conn,
    uid,
    full_folder,
    short_folder,
    job,
    existing_ids,
    db,
):
    status, data = src_conn.fetch(uid, "(RFC822 FLAGS INTERNALDATE)")
    if status != "OK" or not data or not data[0]:
        job.failed_messages += 1
        return

    msg_data = data[0]
    if isinstance(msg_data, tuple) and len(msg_data) >= 2:
        raw_message = msg_data[1]
    else:
        job.failed_messages += 1
        return

    if job.skip_duplicates and existing_ids:
        import email

        with contextlib.suppress(Exception):
            parsed = email.message_from_bytes(raw_message)
            msg_id = parsed.get("Message-ID", "")
            if msg_id and msg_id in existing_ids:
                job.skipped_messages += 1
                return

    flags_str = ""
    date_str = None
    meta = msg_data[0].decode() if isinstance(msg_data[0], bytes) else str(msg_data[0])
    if "FLAGS" in meta:
        import re

        flags_match = re.search(r"FLAGS \(([^)]*)\)", meta)
        if flags_match:
            flags_str = flags_match.group(1)
    if "INTERNALDATE" in meta:
        import re

        date_match = re.search(r'INTERNALDATE "([^"]+)"', meta)
        if date_match:
            import imaplib

            date_str = imaplib.Time2Internaldate(
                imaplib.Internaldate2tuple(f'INTERNALDATE "{date_match.group(1)}"'.encode())
            )

    target_folder = _map_folder(short_folder, job.folder_mapping)
    logger.debug(
        "Restore: APPEND to '%s', flags='%s', date='%s', msg_size=%d",
        target_folder,
        flags_str,
        date_str,
        len(raw_message),
    )
    _ensure_folder(tgt_conn, target_folder)

    try:
        result, resp = _retry_imap(
            tgt_conn.append,
            f'"{target_folder}"',
            flags_str if flags_str else None,
            date_str,
            raw_message,
        )
        if result == "OK":
            job.restored_messages += 1
        else:
            job.failed_messages += 1
            logger.warning("Restore: APPEND failed for UID %s: %s %s", uid, result, resp)
    except (imaplib.IMAP4.error, OSError) as e:
        job.failed_messages += 1
        logger.warning("Restore: APPEND exception for UID %s: %s", uid, e)


def _map_folder(folder_name, folder_mapping):
    if folder_mapping == "original":
        return folder_name
    return f"{folder_mapping}/{folder_name}"


def _ensure_folder(conn, folder_name):
    try:
        status, _ = conn.select(f'"{folder_name}"')
        if status == "OK":
            conn.close()
            return
    except (imaplib.IMAP4.error, ValueError, TypeError, OSError):
        pass
    with contextlib.suppress(imaplib.IMAP4.error, OSError):
        conn.create(f'"{folder_name}"')


def _get_existing_message_ids(conn, folder_name):
    ids = set()
    try:
        status, _ = conn.select(f'"{folder_name}"', readonly=True)
        if status != "OK":
            return ids
        status, data = conn.search(None, "ALL")
        if status != "OK" or not data[0]:
            conn.close()
            return ids
        for uid in data[0].split():
            status, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            if status == "OK" and msg_data and msg_data[0]:
                header = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                if isinstance(header, bytes):
                    header = header.decode(errors="replace")
                for line in header.splitlines():
                    if line.lower().startswith("message-id:"):
                        ids.add(line.split(":", 1)[1].strip())
        conn.close()
    except imaplib.IMAP4.error:
        pass
    return ids


def _refresh_target_token(creds_json, db, account):
    import asyncio
    import json

    try:
        token_data = json.loads(creds_json)
    except json.JSONDecodeError:
        return None
    refresh_token = token_data.get("refresh_token", "")
    if not refresh_token:
        return None
    provider = token_data.get("provider", "google")
    try:
        from mailfallback.services.oauth2 import refresh_google_token, refresh_microsoft_token

        refresh_fn = {"microsoft": refresh_microsoft_token}.get(provider, refresh_google_token)
        access_token = asyncio.run(refresh_fn(refresh_token))
        token_data["access_token"] = access_token
        from mailfallback.security import encrypt_credentials

        account.credentials = encrypt_credentials(json.dumps(token_data), settings.secret_key)
        db.commit()
        return access_token
    except Exception:
        logger.exception("Failed to refresh OAuth2 token for %s", account.name)
        return None


def _fail_job(db, job, error_msg):
    job.status = JobStatus.failed
    job.error = error_msg
    job.completed_at = datetime.now(UTC)
    db.commit()
