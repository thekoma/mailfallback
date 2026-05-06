# src/mailfallback/routers/restore.py
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import User
from mailfallback.services import account_service
from mailfallback.services.audit_service import log_action
from mailfallback.services.dovecot_auth import create_temp_imap_user, delete_temp_imap_user
from mailfallback.services.imap_check import connect_imap
from mailfallback.services.restore_service import (
    cancel_restore_job,
    create_restore_job,
    get_restore_job,
)
from mailfallback.services.restore_worker import request_cancel, submit_restore_job

router = APIRouter(prefix="/api/restore", tags=["restore"])
browse_router = APIRouter(prefix="/api", tags=["browse"])


# ---------------------------------------------------------------------------
# Restore job endpoints
# ---------------------------------------------------------------------------


@router.post("")
def create_restore(
    body: dict,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source_account_id = body.get("source_account_id")
    target_account_id = body.get("target_account_id")
    restore_mode = body.get("restore_mode", "full")

    if not source_account_id or not target_account_id:
        raise HTTPException(
            status_code=400,
            detail="source_account_id and target_account_id required",
        )

    source = account_service.get_account(db, source_account_id, user)
    if not source:
        raise HTTPException(status_code=404, detail="Source account not found")

    target = account_service.get_account(db, target_account_id, user)
    if not target:
        raise HTTPException(status_code=404, detail="Target account not found")

    job = create_restore_job(
        db,
        source_account_id=source_account_id,
        target_account_id=target_account_id,
        restore_mode=restore_mode,
        requested_by=user.id,
        folder_mapping=body.get("folder_mapping", "original"),
        skip_duplicates=body.get("skip_duplicates", True),
        selected_folders=body.get("selected_folders"),
        selected_uids=body.get("selected_uids"),
    )
    if not job:
        raise HTTPException(status_code=409, detail="Cannot create restore job")

    submit_restore_job(job.id)

    log_action(
        db,
        user=user,
        action="restore.start",
        resource_type="restore",
        resource_id=job.id,
        resource_name=f"{source.name} → {target.name}",
        ip_address=request.client.host if request.client else None,
    )

    return {
        "job_id": job.id,
        "status": job.status.value,
        "source_account_id": job.source_account_id,
        "target_account_id": job.target_account_id,
        "restore_mode": job.restore_mode.value,
    }


@router.get("/{job_id}")
def get_restore(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = get_restore_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Restore job not found")

    source = account_service.get_account(db, job.source_account_id, user)
    if not source:
        raise HTTPException(status_code=404, detail="Restore job not found")

    return {
        "job_id": job.id,
        "status": job.status.value,
        "restore_mode": job.restore_mode.value,
        "source_account_id": job.source_account_id,
        "target_account_id": job.target_account_id,
        "total_messages": job.total_messages,
        "restored_messages": job.restored_messages,
        "skipped_messages": job.skipped_messages,
        "failed_messages": job.failed_messages,
        "error": job.error,
        "requested_at": job.requested_at.isoformat() if job.requested_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.post("/{job_id}/cancel")
def cancel_restore(
    job_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = get_restore_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Restore job not found")

    source = account_service.get_account(db, job.source_account_id, user)
    if not source:
        raise HTTPException(status_code=404, detail="Restore job not found")

    request_cancel(job_id)
    ok = cancel_restore_job(db, job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Job cannot be cancelled")

    return {"ok": True, "job_id": job_id}


# ---------------------------------------------------------------------------
# Mailbox browse & search endpoints
# ---------------------------------------------------------------------------


def _get_namespace_prefix(account):
    short_id = account.id[-4:]
    return f"{account.name} ({account.email_address}) [{short_id}]/"


def _connect_dovecot_for_account(db, account):
    temp_username, temp_password = create_temp_imap_user(db, [account.id])
    conn = connect_imap(
        settings.dovecot_imap_host,
        settings.dovecot_imap_port,
        "NONE",
        temp_username,
        temp_password,
    )
    return conn, temp_username


def _parse_folder_name(line_bytes, namespace_prefix):
    decoded = line_bytes.decode() if isinstance(line_bytes, bytes) else line_bytes
    parts = decoded.rsplit('" "', 1)
    if len(parts) != 2:
        return None
    full_name = parts[1].rstrip('"')
    if namespace_prefix and full_name.startswith(namespace_prefix):
        short_name = full_name[len(namespace_prefix) :]
    else:
        short_name = full_name
    return full_name, short_name


def _parse_message_count(status_data):
    if not status_data:
        return 0
    raw = status_data[0].decode() if isinstance(status_data[0], bytes) else status_data[0]
    match = re.search(r"MESSAGES\s+(\d+)", raw)
    return int(match.group(1)) if match else 0


def _parse_envelope(fetch_data):
    """Parse IMAP FETCH ENVELOPE responses into message dicts."""
    messages = []
    for item in fetch_data:
        if not isinstance(item, tuple) or len(item) < 1:
            continue
        raw = item[0].decode() if isinstance(item[0], bytes) else str(item[0])

        # Extract sequence number
        seq_match = re.match(r"(\d+)\s+\(", raw)
        if not seq_match:
            continue
        seq = int(seq_match.group(1))

        # Extract subject from ENVELOPE
        env_match = re.search(r'ENVELOPE\s+\("([^"]*)"(?:\s+NIL|\s+"([^"]*)")', raw)
        if not env_match:
            # Try simpler pattern: date then subject
            env_match = re.search(r'ENVELOPE\s+\("[^"]*"\s+"([^"]*)"', raw)
            if env_match:
                subject = env_match.group(1)
                date = ""
            else:
                continue
        else:
            date = env_match.group(1)
            subject = env_match.group(2) if env_match.group(2) else ""

        # Extract flags
        flags = []
        flags_match = re.search(r"FLAGS\s+\(([^)]*)\)", raw)
        if flags_match:
            flags = [f.strip() for f in flags_match.group(1).split() if f.strip()]

        # Extract Message-ID
        msgid_match = re.search(r"<([^>]+)>", raw)
        message_id = f"<{msgid_match.group(1)}>" if msgid_match else ""

        # Extract from
        from_match = re.search(r'\(\("([^"]*)"', raw)
        from_name = from_match.group(1) if from_match else ""

        messages.append(
            {
                "seq": seq,
                "subject": subject,
                "date": date,
                "from": from_name,
                "message_id": message_id,
                "flags": flags,
            }
        )
    return messages


@browse_router.get("/accounts/{account_id}/mailboxes")
def list_mailboxes(
    account_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    namespace_prefix = _get_namespace_prefix(account)
    conn, temp_username = _connect_dovecot_for_account(db, account)
    try:
        status, folder_data = conn.list(f'"{namespace_prefix}"', "*")
        if status != "OK" or not folder_data:
            return []

        mailboxes = []
        for item in folder_data:
            if not item or item == b"":
                continue
            parsed = _parse_folder_name(item, namespace_prefix)
            if not parsed:
                continue
            full_name, short_name = parsed
            msg_count = 0
            st, st_data = conn.status(f'"{full_name}"', "(MESSAGES)")
            if st == "OK":
                msg_count = _parse_message_count(st_data)
            mailboxes.append({"name": short_name, "full_name": full_name, "messages": msg_count})
        return mailboxes
    finally:
        conn.logout()
        delete_temp_imap_user(db, temp_username)


@browse_router.get("/accounts/{account_id}/mailboxes/{folder:path}/messages")
def list_messages(
    account_id: str,
    folder: str,
    page: int = 1,
    page_size: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    namespace_prefix = _get_namespace_prefix(account)
    imap_folder = f"{namespace_prefix}{folder}"
    conn, temp_username = _connect_dovecot_for_account(db, account)
    try:
        status, data = conn.select(f'"{imap_folder}"', readonly=True)
        if status != "OK":
            raise HTTPException(status_code=404, detail="Folder not found")

        total = int(data[0].decode()) if data[0] else 0
        if total == 0:
            return []

        status, data = conn.search(None, "ALL")
        if status != "OK" or not data[0]:
            return []

        uids = data[0].split()
        uids.reverse()
        start = (page - 1) * page_size
        end = start + page_size
        page_uids = uids[start:end]

        if not page_uids:
            return []

        uid_set = b",".join(page_uids)
        status, fetch_data = conn.fetch(uid_set.decode(), "(ENVELOPE FLAGS)")
        if status != "OK":
            return []

        return _parse_envelope(fetch_data)
    finally:
        conn.logout()
        delete_temp_imap_user(db, temp_username)


@browse_router.get("/accounts/{account_id}/mailboxes/{folder:path}/search")
def search_messages(
    account_id: str,
    folder: str,
    q: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    namespace_prefix = _get_namespace_prefix(account)
    imap_folder = f"{namespace_prefix}{folder}"
    conn, temp_username = _connect_dovecot_for_account(db, account)
    try:
        status, data = conn.select(f'"{imap_folder}"', readonly=True)
        if status != "OK":
            raise HTTPException(status_code=404, detail="Folder not found")

        words = q.split()
        search_criteria = []
        for word in words:
            search_criteria.extend(["TEXT", f'"{word}"'])
        status, data = conn.search(None, *search_criteria)
        if status != "OK" or not data[0]:
            return []

        uids = data[0].split()
        if not uids:
            return []

        uids = uids[:100]
        uid_set = b",".join(uids)
        status, fetch_data = conn.fetch(uid_set.decode(), "(ENVELOPE FLAGS)")
        if status != "OK":
            return []

        return _parse_envelope(fetch_data)
    finally:
        conn.logout()
        delete_temp_imap_user(db, temp_username)
