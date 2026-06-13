import base64
import contextlib
import imaplib
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import Account, JobStatus, RestoreJob, RestoreMode, User
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
        # OAuth2 upstreams (Gmail/Microsoft) reject access tokens sent via
        # plain LOGIN — they require AUTHENTICATE XOAUTH2.
        if target.auth_type.value == "oauth2":
            tgt_password = _refresh_target_token(target_creds, db, target)
            if not tgt_password:
                _fail_job(db, job, "Failed to refresh OAuth2 token for target")
                return
            tgt_auth_method = "xoauth2"
        else:
            tgt_password = target_creds
            tgt_auth_method = "login"

        # Staging pushes read their messages from the requester's staging
        # Maildir on local disk: no Dovecot source connection, no temp user.
        if job.restore_mode == RestoreMode.staging_push:
            _execute_staging_push(db, job, target, tgt_password, tgt_auth_method)
            return

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
            auth_method=tgt_auth_method,
        )
        logger.info("Restore %s: target connected OK", job_id)

        tgt_separator = _get_hierarchy_separator(tgt_conn)
        logger.info("Restore %s: target hierarchy separator='%s'", job_id, tgt_separator)

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
        src_conn_params = {
            "host": settings.dovecot_imap_host,
            "port": settings.dovecot_imap_port,
            "tls_type": "NONE",
            "username": temp_username,
            "password": temp_password,
        }
        tgt_conn_params = {
            "host": target.imap_host,
            "port": target.imap_port,
            "tls_type": target.tls_type or "IMAPS",
            "username": target.imap_user or target.email_address,
            "password": tgt_password,
            "auth_method": tgt_auth_method,
        }
        _execute_restore(
            db,
            job,
            src_conn,
            tgt_conn,
            folders,
            tgt_separator,
            src_conn_params,
            tgt_conn_params,
            src_namespace_prefix=_get_namespace_prefix(source),
        )

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
        try:
            db.refresh(job)
        except Exception:
            # Fail-safe twin of _fail_job's rollback: a broken session here
            # must not leave the job stuck in `running`. Discard the dead
            # transaction; the attribute access below re-reads from the DB.
            logger.warning(
                "Restore %s: job refresh failed in completion handler", job_id, exc_info=True
            )
            with contextlib.suppress(Exception):
                db.rollback()
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
                status_actions = {
                    JobStatus.completed: "restore.complete",
                    JobStatus.cancelled: "restore.cancelled",
                }
                action = status_actions.get(job.status, "restore.failed")
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
    # When selected_uids was passed (e.g. by the Restore Workspace), trust the
    # keys verbatim — they may include alternative namespaces such as mounted
    # Recovery snapshots ("Recovery — name (snap-X)/INBOX") that don't live
    # under the source account's own namespace prefix. We pass each key as
    # both the full IMAP path and the short folder key so the per-folder
    # uid_filter lookup later in _execute_restore matches by the same key.
    if job.selected_uids:
        return [(folder, folder) for folder in job.selected_uids]

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
    # Delegate to the dovecot router's helper so the publisher and consumers
    # never drift (B5): the worker both LISTs with this prefix and strips it
    # from restore-to-origin selection keys produced by api_resolve_uids — a
    # second literal implementation would let the formats diverge silently.
    # Deferred import: services must not import routers at module load.
    from mailfallback.routers.dovecot import account_namespace_prefix

    return account_namespace_prefix(account)


def _reconnect_target(tgt_conn_params):
    logger.info("Reconnecting to target %s:%s", tgt_conn_params["host"], tgt_conn_params["port"])
    # src_conn_params (Dovecot) carries no auth_method — default to plain login.
    return connect_imap(
        tgt_conn_params["host"],
        tgt_conn_params["port"],
        tgt_conn_params["tls_type"],
        tgt_conn_params["username"],
        tgt_conn_params["password"],
        auth_method=tgt_conn_params.get("auth_method", "login"),
    )


def _execute_restore(
    db,
    job,
    src_conn,
    tgt_conn,
    folders,
    tgt_separator="/",
    src_conn_params=None,
    tgt_conn_params=None,
    src_namespace_prefix="",
):
    # Selection mode (selected_uids set) works with REAL IMAP UIDs: the
    # resolve-uids endpoint and the workspace search resolve messages via
    # UID SEARCH, so the filter below must compare against UID SEARCH/FETCH
    # results. Sequence numbers diverge from UIDs as soon as a folder has
    # expunge history — a seq-based filter would restore the wrong messages.
    by_uid = bool(job.selected_uids)
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
            _cancel_job(db, job)
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

        if by_uid:
            status, data = src_conn.uid("SEARCH", "ALL")
        else:
            status, data = src_conn.search(None, "ALL")
        if status != "OK" or not data[0]:
            continue
        uids = data[0].split()
        logger.info(
            "Restore: folder %s has %d messages, uid_filter=%s", short_folder, len(uids), uid_filter
        )

        existing_ids = set()
        # mUTF-7 at the IMAP boundary: a typed non-ASCII custom root would
        # otherwise die in imaplib's ASCII command encoding. No-op for the
        # (already wire-encoded) source-derived names.
        target_folder = _imap_utf7_encode(
            _map_folder(
                short_folder, job.folder_mapping, tgt_separator, src_prefix=src_namespace_prefix
            )
        )
        if job.skip_duplicates:
            existing_ids = _get_existing_message_ids(tgt_conn, target_folder)

        for uid_bytes in uids:
            if job.id in _cancel_flags:
                _cancel_job(db, job)
                return

            uid = uid_bytes.decode()
            if uid_filter and int(uid) not in uid_filter:
                continue

            try:
                _restore_single_message(
                    src_conn, tgt_conn, uid, target_folder, job, existing_ids, db, by_uid=by_uid
                )
            except (
                BrokenPipeError,
                ConnectionResetError,
                imaplib.IMAP4.abort,
                OSError,
            ) as e:
                logger.warning("Connection lost (%s), reconnecting...", e)
                try:
                    if src_conn_params:
                        src_conn = _reconnect_target(src_conn_params)
                        src_conn.select(f'"{full_folder}"', readonly=True)
                    if tgt_conn_params:
                        tgt_conn = _reconnect_target(tgt_conn_params)
                    _restore_single_message(
                        src_conn, tgt_conn, uid, target_folder, job, existing_ids, db, by_uid=by_uid
                    )
                except Exception:
                    job.failed_messages += 1
                    logger.warning("Retry failed for UID %s: %s", uid, full_folder, exc_info=True)
            except Exception:
                job.failed_messages += 1
                logger.warning("Failed to restore UID %s from %s", uid, full_folder, exc_info=True)

            db.commit()


def _restore_single_message(
    src_conn,
    tgt_conn,
    uid,
    target_folder,
    job,
    existing_ids,
    db,
    by_uid=False,
):
    if by_uid:
        status, data = src_conn.uid("FETCH", uid, "(RFC822 FLAGS INTERNALDATE)")
    else:
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

    if job.skip_duplicates and _is_duplicate(raw_message, existing_ids):
        job.skipped_messages += 1
        return

    flags_str = ""
    date_str = None
    meta = msg_data[0].decode() if isinstance(msg_data[0], bytes) else str(msg_data[0])
    if "FLAGS" in meta:
        import re

        flags_match = re.search(r"FLAGS \(([^)]*)\)", meta)
        if flags_match:
            raw_flags = flags_match.group(1)
            flags_str = " ".join(f for f in raw_flags.split() if f.upper() != "\\RECENT")
    if "INTERNALDATE" in meta:
        import re

        date_match = re.search(r'INTERNALDATE "([^"]+)"', meta)
        if date_match:
            import imaplib

            date_str = imaplib.Time2Internaldate(
                imaplib.Internaldate2tuple(f'INTERNALDATE "{date_match.group(1)}"'.encode())
            )

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


def _is_duplicate(raw_message, existing_ids):
    """Shared skip-duplicates check: Message-Id membership against
    _get_existing_message_ids() output for the destination folder."""
    if not existing_ids:
        return False
    import email

    with contextlib.suppress(Exception):
        parsed = email.message_from_bytes(raw_message)
        msg_id = parsed.get("Message-ID", "")
        return bool(msg_id and msg_id in existing_ids)
    return False


def _locate_staged_file(sdir, filename):
    """Find a staged file by manifest name, tolerating Dovecot flag renames
    (same stable-prefix rule as staging reconcile). Returns a path or None."""
    from mailfallback.services.index_service import maildir_filename_prefix

    # basename(): the manifest is user-influencable JSON — never let a
    # crafted "filename" traverse out of the staging Maildir.
    filename = os.path.basename(filename)
    for sub in ("cur", "new"):
        cand = os.path.join(sdir, sub, filename)
        if os.path.exists(cand):
            return cand
    want = maildir_filename_prefix(filename)
    for sub in ("cur", "new"):
        d = os.path.join(sdir, sub)
        if not os.path.isdir(d):
            continue
        for actual in os.listdir(d):
            if maildir_filename_prefix(actual) == want:
                return os.path.join(d, actual)
    return None


def _staged_files_remain(sdir):
    return any(
        os.path.isdir(d) and os.listdir(d)
        for d in (os.path.join(sdir, sub) for sub in ("cur", "new"))
    )


def _execute_staging_push(db, job, target, tgt_password, tgt_auth_method):
    """Append staged files to the target upstream and clean up what landed.

    selected_uids is reused as the push manifest {destination_folder:
    [staged_filename, ...]} — keys are already destination folders (mapped
    by staging_service.push per folder_mode), so neither _map_folder nor
    namespace stripping applies here. Known limitation (deferred): the
    separator conversion below does NOT escape folder names that themselves
    contain the target separator (unlike _map_folder's escape_char) — such
    a folder lands as an extra hierarchy level. Files are read from the
    REQUESTER's staging Maildir; there is no source IMAP connection at all.

    Delivered-or-duplicate files count as pushed: rows + files are removed
    afterwards. Failed files stay staged for a retry; files deleted in
    webmail meanwhile are skipped (deletions win). Cancellation is honored
    between files: what already landed upstream is cleaned up, the rest
    stays staged. The final job status is settled by execute_restore_job's
    shared completion logic.
    """
    # Deferred: staging_service pulls in search/index machinery — keep the
    # worker import-light and cycle-free at module load.
    from mailfallback.services import staging_service

    requester = db.query(User).filter(User.id == job.requested_by).first()
    if not requester:
        _fail_job(db, job, "Requesting user no longer exists")
        return
    sdir = staging_service.staging_dir(requester)
    manifest: dict[str, list[str]] = job.selected_uids or {}
    job.total_messages = sum(len(v) for v in manifest.values())
    db.commit()

    logger.info(
        "Staging push %s: %d file(s) to %s:%s",
        job.id,
        job.total_messages,
        target.imap_host,
        target.imap_port,
    )
    tgt_conn = connect_imap(
        target.imap_host,
        target.imap_port,
        target.tls_type or "IMAPS",
        target.imap_user or target.email_address,
        tgt_password,
        auth_method=tgt_auth_method,
    )
    try:
        tgt_separator = _get_hierarchy_separator(tgt_conn)
        pushed: dict[str, str] = {}  # manifest filename -> actual on-disk path
        for folder, filenames in manifest.items():
            tgt_folder = folder.replace("/", tgt_separator) if tgt_separator != "/" else folder
            # mUTF-7 at the IMAP boundary — typed custom folders may carry
            # non-ASCII; original-folder keys are wire-encoded already (no-op).
            tgt_folder = _imap_utf7_encode(tgt_folder)
            _ensure_folder(tgt_conn, tgt_folder)
            existing_ids = (
                _get_existing_message_ids(tgt_conn, tgt_folder) if job.skip_duplicates else set()
            )
            for fn in filenames:
                if job.id in _cancel_flags:
                    # Already-APPENDed files are delivered — clean those up
                    # (delivered-or-duplicate rule), leave the rest staged.
                    _cleanup_pushed(db, requester, sdir, pushed)
                    _cancel_job(db, job)
                    return
                path = _locate_staged_file(sdir, fn)
                if path is None:
                    # Deleted in webmail between push click and job run:
                    # deletions win — nothing to deliver, nothing to clean.
                    job.skipped_messages += 1
                    db.commit()
                    continue
                try:
                    with open(path, "rb") as f:
                        raw = f.read()
                    if _is_duplicate(raw, existing_ids):
                        job.skipped_messages += 1
                        pushed[fn] = path  # already upstream == done
                    else:
                        result, resp = _retry_imap(
                            tgt_conn.append, f'"{tgt_folder}"', None, None, raw
                        )
                        if result == "OK":
                            job.restored_messages += 1
                            pushed[fn] = path
                        else:
                            job.failed_messages += 1
                            logger.warning(
                                "Staging push: APPEND failed for %s: %s %s", fn, result, resp
                            )
                except (imaplib.IMAP4.error, OSError) as e:
                    job.failed_messages += 1
                    logger.warning("Staging push: APPEND error for %s: %s", fn, e)
                db.commit()
        _cleanup_pushed(db, requester, sdir, pushed)
    finally:
        with contextlib.suppress(Exception):
            tgt_conn.logout()


def _cleanup_pushed(db, requester, sdir, pushed):
    """Drop rows + files for pushed (delivered-or-duplicate) messages; the
    area and its dir die once neither rows nor message files remain.
    Row matching is flag-rename tolerant (same prefix rule as reconcile);
    orphan files without rows are left for empty()/cleanup_expired().

    Residual races with a concurrent status-poll reconcile are benign by
    construction: a double rmtree is ignore_errors, a double area DELETE
    surfaces as a SAWarning on flush at worst, and a bytes_used lost-update
    self-heals at the next reconcile (it recomputes from disk). A flush/
    commit error here still fails the job safely — _fail_job rolls the
    session back before marking it failed."""
    from mailfallback.models import StagingArea, StagingMessage
    from mailfallback.services import staging_service
    from mailfallback.services.index_service import maildir_filename_prefix

    area = db.query(StagingArea).filter(StagingArea.user_id == requester.id).first()
    if not area:
        return
    if pushed:
        by_prefix = {maildir_filename_prefix(fn): path for fn, path in pushed.items()}
        for m in db.query(StagingMessage).filter(StagingMessage.staging_id == area.id).all():
            path = by_prefix.get(maildir_filename_prefix(m.staged_filename))
            if path is None:
                continue
            with contextlib.suppress(OSError):
                os.remove(path)
            area.bytes_used = max(0, area.bytes_used - m.size_bytes)
            db.delete(m)
        db.flush()
    remaining = db.query(StagingMessage).filter(StagingMessage.staging_id == area.id).count()
    if remaining == 0 and not _staged_files_remain(sdir):
        staging_service._remove_staging_dir(requester)
        db.delete(area)
    db.commit()


def _get_hierarchy_separator(conn):
    import re

    try:
        status, data = conn.list('""', '""')
        if status == "OK" and data and data[0]:
            decoded = data[0].decode() if isinstance(data[0], bytes) else data[0]
            match = re.search(r'"(.)"', decoded)
            if match:
                return match.group(1)
    except Exception:
        logger.debug("Failed to detect hierarchy separator, defaulting to /")
    return "/"


def _map_folder(folder_name, folder_mapping, separator="/", escape_char="_", src_prefix=""):
    # Strip the source account's live namespace prefix. Restore-to-origin
    # selection keys are full IMAP paths as the temp Dovecot user sees them
    # (e.g. "Name (email) [abcd]/INBOX", see api_resolve_uids); the
    # destination expects the bare folder name.
    if src_prefix and folder_name.startswith(src_prefix):
        folder_name = folder_name[len(src_prefix) :]
    # Strip the Dovecot Recovery namespace prefix when restoring from a
    # snapshot mount. The destination user expects the message in their
    # native folder (e.g., INBOX), not in a synthetic Recovery-prefixed
    # one. Dovecot publishes these as "Recovery — <label> (<ts>) [<id>]/".
    if folder_name.startswith("Recovery - ") and "/" in folder_name:
        folder_name = folder_name.split("/", 1)[1]
    if separator != "/" and separator in folder_name:
        original = folder_name
        folder_name = folder_name.replace(separator, escape_char)
        logger.info("Folder renamed: '%s' → '%s' (separator collision)", original, folder_name)
    converted = folder_name.replace("/", separator)
    if folder_mapping == "original":
        return converted
    # The mapping root is a "/"-hierarchy path too ("Restored/<date>", typed
    # Custom roots) — give it the same separator treatment as the folder
    # name: escape literal target-separator characters first, then convert
    # its "/" hierarchy. Composing an unconverted root on a dot-separator
    # server would create a literal "A/B" mailbox name (or fail) instead of
    # nesting A → B.
    root = folder_mapping
    if separator != "/" and separator in root:
        root = root.replace(separator, escape_char)
        logger.info("Mapping root renamed: '%s' → '%s' (separator collision)", folder_mapping, root)
    root = root.replace("/", separator)
    return f"{root}{separator}{converted}"


def _imap_utf7_encode(name: str) -> str:
    """Encode a mailbox name as RFC 3501 §5.1.3 modified UTF-7 for the wire.

    Pure-ASCII names pass through UNTOUCHED: every name the worker receives
    from LIST or selected_uids keys is already wire-encoded ASCII (a literal
    "&-"/"&AOA-" in it IS the encoding), so re-encoding would double-encode
    them. Only decoded Unicode — user-typed custom roots — actually shifts:
    printable ASCII represents itself, "&" becomes "&-", and everything else
    rides in "&…-" runs of base64'd UTF-16BE with "," for "/" and the
    padding stripped. Encode-only: nothing in the worker needs the decode
    direction.
    """
    if name.isascii():
        return name
    out: list[str] = []
    run: list[str] = []  # pending non-printable run, shifted as one block

    def _flush():
        if run:
            b64 = base64.b64encode("".join(run).encode("utf-16-be")).decode("ascii")
            out.append("&" + b64.rstrip("=").replace("/", ",") + "-")
            run.clear()

    for ch in name:
        if " " <= ch <= "~":  # printable US-ASCII represents itself...
            _flush()
            out.append("&-" if ch == "&" else ch)  # ...except the shift char
        else:
            run.append(ch)
    _flush()
    return "".join(out)


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
    # Fail-safe: a failure may arrive with the session already poisoned (e.g.
    # a flush that died mid-cleanup leaves it PendingRollback). Roll back
    # FIRST, or the commit below raises, the executor swallows it, and the
    # job sticks in `running` forever — blocking every future job to the
    # same target via the create_restore_job busy check.
    with contextlib.suppress(Exception):
        db.rollback()
    job.status = JobStatus.failed
    job.error = error_msg
    job.completed_at = datetime.now(UTC)
    db.commit()


def _cancel_job(db, job):
    job.status = JobStatus.cancelled
    job.error = "Cancelled by user"
    job.completed_at = datetime.now(UTC)
    db.commit()
