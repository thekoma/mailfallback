# src/mailfallback/routers/restore.py
import contextlib
import email
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import BackupPolicy, RecoveryStatus, User
from mailfallback.services import account_service, mount_service, restic_service
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


class RestoreCreate(BaseModel):
    source_account_id: str
    target_account_id: str
    restore_mode: str = "full"
    folder_mapping: str = "original"
    skip_duplicates: bool = True
    selected_folders: list[str] | None = None
    selected_uids: dict | None = None


def _sanitize_imap_string(value: str) -> str:
    return re.sub(r'["\\\x00-\x1f]', "", value)


# ---------------------------------------------------------------------------
# Restore job endpoints
# ---------------------------------------------------------------------------


@router.post("")
def create_restore(
    body: RestoreCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = account_service.get_account(db, body.source_account_id, user)
    if not source:
        raise HTTPException(status_code=404, detail="Source account not found")

    target = account_service.get_account(db, body.target_account_id, user)
    if not target:
        raise HTTPException(status_code=404, detail="Target account not found")

    job = create_restore_job(
        db,
        source_account_id=body.source_account_id,
        target_account_id=body.target_account_id,
        restore_mode=body.restore_mode,
        requested_by=user.id,
        folder_mapping=body.folder_mapping,
        skip_duplicates=body.skip_duplicates,
        selected_folders=body.selected_folders,
        selected_uids=body.selected_uids,
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

    if job.requested_by != user.id and user.role.value != "admin":
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

    if job.requested_by != user.id and user.role.value != "admin":
        source = account_service.get_account(db, job.source_account_id, user)
        if not source:
            raise HTTPException(status_code=404, detail="Restore job not found")

    request_cancel(job_id)
    ok = cancel_restore_job(db, job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Job cannot be cancelled")

    log_action(
        db,
        user=user,
        action="restore.cancel",
        resource_type="restore",
        resource_id=job_id,
        ip_address=request.client.host if request.client else None,
    )

    return {"ok": True, "job_id": job_id}


# ---------------------------------------------------------------------------
# Mailbox browse & search endpoints
# ---------------------------------------------------------------------------


def _get_namespace_prefix(account):
    short_id = account.id[-4:]
    return f"{account.name} ({account.email_address}) [{short_id}]/"


def _connect_dovecot_for_account(db, account):
    temp_username, temp_password = create_temp_imap_user(db, [account.id])
    try:
        conn = connect_imap(
            settings.dovecot_imap_host,
            settings.dovecot_imap_port,
            "NONE",
            temp_username,
            temp_password,
        )
    except Exception:
        delete_temp_imap_user(db, temp_username)
        raise
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
    page = max(1, min(page, 10000))
    page_size = max(1, min(page_size, 200))

    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    namespace_prefix = _get_namespace_prefix(account)
    imap_folder = f"{namespace_prefix}{_sanitize_imap_string(folder)}"
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


def _fetch_message_header(conn, uid, folder_name: str = "") -> dict | None:
    """Fetch RFC822 headers for one UID and return a small envelope dict.

    Used by `_search_namespace_for_query`. Returns None on failure.
    """
    try:
        typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
    except Exception:
        return None
    if typ != "OK" or not data:
        return None
    raw_header = b""
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            raw_header = bytes(item[1])
            break
    if not raw_header:
        return None
    msg = email.message_from_bytes(raw_header)
    env = {
        "uid": str(uid) if not isinstance(uid, str) else uid,
        "subject": (msg.get("Subject") or "").strip(),
        "from": (msg.get("From") or "").strip(),
        "folder": folder_name,
        "message_id": (msg.get("Message-Id") or "").strip("<>").strip(),
    }
    return env


def _search_namespace_for_query(
    conn,
    namespace: str,
    query: str,
    folder: str = "INBOX",
    search_body: bool = False,
) -> list[dict]:
    """Run a Dovecot SEARCH on `namespace + folder` for `query` (subject OR from).

    When `search_body=True`, the SEARCH also covers BODY. Without an FTS
    plugin, BODY search grep-scans every message — slow. We accept the cost
    when the user explicitly opts in via the workspace Advanced controls.

    Returns a list of dicts with: uid, subject, from, namespace, folder, message_id.
    Caller is responsible for the connection lifecycle.
    """
    target = f"{namespace}{folder}" if namespace else folder
    typ, _ = conn.select(f'"{target}"', readonly=True)
    if typ != "OK":
        return []
    quoted = _sanitize_imap_string(query)
    # Wrap in IMAP quoted-string so multi-word queries are passed as a single
    # token. _sanitize_imap_string strips ", \, and control chars, so the wrap
    # is safe against injection.
    quoted_arg = f'"{quoted}"'
    # IMAP4rev1:
    #   without body: `OR SUBJECT "x" FROM "x"` (2-way OR)
    #   with body:    `OR OR SUBJECT "x" FROM "x" BODY "x"` (3-way OR)
    if search_body:
        typ, data = conn.uid(
            "SEARCH",
            "OR",
            "OR",
            "SUBJECT",
            quoted_arg,
            "FROM",
            quoted_arg,
            "BODY",
            quoted_arg,
        )
    else:
        typ, data = conn.uid("SEARCH", "OR", "SUBJECT", quoted_arg, "FROM", quoted_arg)
    if typ != "OK" or not data or not data[0]:
        return []
    raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    uids = raw.split()
    hits: list[dict] = []
    for uid in uids:
        env = _fetch_message_header(conn, uid, folder_name=target)
        if env:
            env["namespace"] = namespace
            env["folder"] = folder
            hits.append(env)
    return hits


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
    imap_folder = f"{namespace_prefix}{_sanitize_imap_string(folder)}"
    conn, temp_username = _connect_dovecot_for_account(db, account)
    try:
        status, data = conn.select(f'"{imap_folder}"', readonly=True)
        if status != "OK":
            raise HTTPException(status_code=404, detail="Folder not found")

        words = q.split()
        search_criteria = []
        for word in words:
            safe_word = _sanitize_imap_string(word)
            search_criteria.extend(["TEXT", f'"{safe_word}"'])
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


# ---------------------------------------------------------------------------
# Workspace search endpoint — live + mounted snapshots, deduped by Message-Id
# ---------------------------------------------------------------------------


class WorkspaceSearchRequest(BaseModel):
    account_id: str
    query: str
    range_start: datetime
    range_end: datetime
    include_live: bool = True
    include_snapshots: bool = True
    search_body: bool = False
    ttl_minutes: int | None = None  # override default ephemeral TTL


@router.post("/workspace/search")
def workspace_search(
    req: WorkspaceSearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, req.account_id, user)
    if not account:
        raise HTTPException(404, "account not found")

    results_by_msgid: dict[str, dict] = {}

    # Find in-range snapshots (by snapshot time).
    snapshot_ids: list[str] = []
    if req.include_snapshots:
        backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == req.account_id).first()
        if backup:
            try:
                snaps = restic_service.list_snapshots(backup.destination, account.id)
            except Exception:
                snaps = []
            for s in snaps:
                ts_raw = s.get("time", "").replace("Z", "+00:00")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except ValueError:
                    continue
                if req.range_start <= ts <= req.range_end:
                    snapshot_ids.append(s.get("short_id") or s.get("id", "")[:8])

    # Mount each snapshot ephemeral (idempotent, capped).
    mounted: list[tuple[str, str]] = []  # (snapshot_id, namespace_prefix)
    mount_kwargs: dict = {}
    if req.ttl_minutes is not None:
        mount_kwargs["ttl_minutes"] = req.ttl_minutes
    for snap_id in snapshot_ids[: settings.recovery_max_parallel_mounts]:
        rec = mount_service.ensure_mounted(db, req.account_id, snap_id, **mount_kwargs)
        if rec.status != RecoveryStatus.ready:
            continue
        # Namespace label MUST match the prefix Dovecot publishes in
        # routers/dovecot.py for this Recovery — otherwise SELECT fails on
        # the temp IMAP user. Keep the two formatters in sync.
        short = (rec.snapshot_id or rec.id)[:8]
        label = account.name
        ts = rec.restored_at.strftime("%Y-%m-%d") if rec.restored_at else "snapshot"
        ns_label = f"Recovery — {label} ({ts}) [{short}]/"
        mounted.append((snap_id, ns_label))

    # Search live first, then each mounted snapshot.
    conn, temp_username = _connect_dovecot_for_account(db, account)
    try:
        if req.include_live:
            for hit in _search_namespace_for_query(
                conn, namespace="", query=req.query, search_body=req.search_body
            ):
                _merge_hit(results_by_msgid, hit, source_label="live")
        for snap_id, ns in mounted:
            for hit in _search_namespace_for_query(
                conn, namespace=ns, query=req.query, search_body=req.search_body
            ):
                _merge_hit(results_by_msgid, hit, source_label=snap_id)
    finally:
        with contextlib.suppress(Exception):
            conn.logout()
        delete_temp_imap_user(db, temp_username)

    return {
        "results": list(results_by_msgid.values()),
        "mounted_snapshots": [s for s, _ in mounted],
    }


class WorkspaceSnapshotCountRequest(BaseModel):
    account_id: str
    range_start: datetime
    range_end: datetime


@router.post("/workspace/snapshot-count")
def workspace_snapshot_count(
    req: WorkspaceSnapshotCountRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Count snapshots in the requested range and sum their sizes.

    Cheap operation: just lists snapshots from restic and filters by time;
    no mount happens. Used by the workspace UI to show the cost of widening
    the time range.
    """
    account = account_service.get_account(db, req.account_id, user)
    if not account:
        raise HTTPException(404, "account not found")

    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == req.account_id).first()
    if not backup:
        return {"count": 0, "size_bytes": 0}

    try:
        snaps = restic_service.list_snapshots(backup.destination, account.id)
    except Exception:
        return {"count": 0, "size_bytes": 0}

    count = 0
    size_bytes = 0
    for s in snaps:
        ts_raw = s.get("time", "").replace("Z", "+00:00")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if req.range_start <= ts <= req.range_end:
            count += 1
            # restic snapshot dicts may have "summary" with "total_bytes_processed"
            summary = s.get("summary") or {}
            size_bytes += summary.get("total_bytes_processed") or 0

    return {"count": count, "size_bytes": size_bytes}


def _merge_hit(dedup: dict[str, dict], hit: dict, source_label: str) -> None:
    """Merge a hit into the dedup map keyed by Message-Id, preserving per-source location.

    Each entry has:
      message_id, subject, from, folder (top-level for display),
      sources: [labels...],
      locations: [ {source, namespace, folder, uid}, ... ]
    """
    msgid = hit.get("message_id") or f"_no_msgid_{source_label}_{hit.get('uid')}"
    location = {
        "source": source_label,
        "namespace": hit.get("namespace", ""),
        "folder": hit.get("folder", ""),
        "uid": hit.get("uid"),
    }
    if msgid in dedup:
        if source_label not in dedup[msgid]["sources"]:
            dedup[msgid]["sources"].append(source_label)
            dedup[msgid]["locations"].append(location)
    else:
        # Top-level subject/from/folder are kept for display; locations holds
        # the per-source (namespace, folder, uid) used by Restore Selected.
        entry = {
            "message_id": msgid,
            "subject": hit.get("subject"),
            "from": hit.get("from"),
            "folder": hit.get("folder", ""),
            "sources": [source_label],
            "locations": [location],
        }
        dedup[msgid] = entry
