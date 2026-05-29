# src/mailfallback/routers/restore.py
import contextlib
import email
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import BackupPolicy, RecoveryStatus, User
from mailfallback.routers.dovecot import account_namespace_prefix
from mailfallback.services import account_service, mount_service, restic_service, search_service
from mailfallback.services.audit_service import log_action
from mailfallback.services.dovecot_auth import create_temp_imap_user, delete_temp_imap_user
from mailfallback.services.imap_check import connect_imap
from mailfallback.services.recovery_service import namespace_prefix as recovery_namespace_prefix
from mailfallback.services.restore_service import (
    cancel_restore_job,
    create_restore_job,
    get_restore_job,
)
from mailfallback.services.restore_worker import request_cancel, submit_restore_job

logger = logging.getLogger(__name__)

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
    # Delegate to the dovecot router's helper so the publisher and consumer
    # never drift. See B5: any divergence makes IMAP SELECT silently fail.
    return account_namespace_prefix(account)


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


def _list_namespace_folders(conn, namespace: str) -> list[str]:
    """LIST all selectable folders under a namespace prefix.

    Returns folder names WITHOUT the namespace prefix (so they can be
    re-combined later: target = namespace + folder).

    For the live namespace (prefix `Andrea ... [c260]/`) this returns
    ["INBOX", "Sent", "Archive", "[Gmail]/All Mail", ...].
    """
    pattern = f'"{namespace}*"' if namespace else '"*"'
    try:
        typ, data = conn.list('""', pattern)
    except Exception:
        return ["INBOX"]
    if typ != "OK" or not data:
        return ["INBOX"]
    folders: list[str] = []
    for line_bytes in data:
        if not line_bytes:
            continue
        decoded = line_bytes.decode() if isinstance(line_bytes, bytes) else line_bytes
        # Skip non-selectable folders (parent placeholders like "[Gmail]").
        if "\\Noselect" in decoded:
            continue
        # Parse name: response is like `(\HasChildren) "/" "namespace/folder"`
        # Extract the last quoted string.
        parts = decoded.rsplit('"', 2)
        if len(parts) < 3:
            continue
        full_name = parts[-2]
        if namespace and full_name.startswith(namespace):
            folders.append(full_name[len(namespace) :])
        else:
            folders.append(full_name)
    if not folders:
        folders = ["INBOX"]
    return folders


def _search_namespace_for_query(
    conn,
    namespace: str,
    query: str,
    *,
    folders: list[str] | None = None,
    criteria_fields: list[str] | None = None,
    type_filter: str = "all",
    search_body: bool = False,
) -> list[dict]:
    """Run a Dovecot SEARCH on each folder under `namespace` for `query`.

    `folders`: explicit folder names (without namespace prefix). If None,
    enumerate all selectable folders in the namespace via IMAP LIST. Tests
    pass an explicit list to skip LIST mocking.

    `criteria_fields`: subset of {"SUBJECT", "FROM", "TO", "BODY"} to search
    across. The query is OR'd across the selected fields. Defaults to
    ["SUBJECT"] when omitted.

    `type_filter`: one of "all" | "unseen" | "flagged" | "unanswered". When
    not "all", an IMAP type modifier is AND'd in front of the OR chain.

    `search_body`: legacy compat flag. When True and BODY is not already in
    `criteria_fields`, BODY is appended. Without an FTS plugin, BODY search
    grep-scans every message — slow. The user opts in via UI.

    Returns a list of dicts with: uid, subject, from, namespace, folder, message_id.
    Caller is responsible for the connection lifecycle.
    """
    if folders is None:
        folders = _list_namespace_folders(conn, namespace)
    if not criteria_fields:
        criteria_fields = ["SUBJECT"]

    quoted = _sanitize_imap_string(query)
    # Wrap in IMAP quoted-string so multi-word queries are passed as a single
    # token. _sanitize_imap_string strips ", \, and control chars, so the wrap
    # is safe against injection.
    quoted_arg = f'"{quoted}"'
    type_map = {"unseen": "UNSEEN", "flagged": "FLAGGED", "unanswered": "UNANSWERED"}

    # When body search is requested, use IMAP TEXT criterion which matches the
    # whole message text (all headers + body). This is what Roundcube uses for
    # "search in body": broader than `OR SUBJECT FROM TO BODY` (which misses
    # X-* headers, Received, Reply-To, etc.) and FTS-friendly via fts_flatcurve.
    use_text = search_body or "BODY" in criteria_fields

    all_hits: list[dict] = []
    for folder in folders:
        target = f"{namespace}{folder}" if namespace else folder
        typ, _ = conn.select(f'"{target}"', readonly=True)
        if typ != "OK":
            continue
        # Build SEARCH args: [TYPE_MODIFIER?] [criteria...]
        args: list[str] = []
        if type_filter in type_map:
            args.append(type_map[type_filter])
        if use_text:
            # TEXT covers all headers + body in one shot.
            args.extend(["TEXT", quoted_arg])
        else:
            # Header-only search via OR chain on the explicit fields.
            # IMAP4rev1 OR is binary; chain (n-1) OR tokens to OR n criteria.
            for _ in range(len(criteria_fields) - 1):
                args.append("OR")
            for field in criteria_fields:
                args.extend([field, quoted_arg])

        typ, data = conn.uid("SEARCH", *args)
        if typ != "OK" or not data or not data[0]:
            continue
        raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        uids = raw.split()
        # Cap per-folder hits to keep the response sane on huge folders like
        # [Gmail]/All Mail with tens of thousands of matches.
        for uid in uids[:200]:
            env = _fetch_message_header(conn, uid, folder_name=target)
            if env:
                env["namespace"] = namespace
                env["folder"] = folder
                all_hits.append(env)
    return all_hits


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
    search_subject: bool = True
    search_from: bool = False
    search_to: bool = False
    deep: bool = False  # full-folder body search (replaces legacy search_body)
    type_filter: str = "all"  # all|unseen|flagged|unanswered
    ttl_minutes: int | None = None  # override default ephemeral TTL


@router.post("/workspace/search")
def workspace_search(
    req: WorkspaceSearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """DEPRECATED — use POST /api/restore/search.

    Translates the legacy single-account request to the new search_service
    call and returns the legacy {results, mounted_snapshots} shape so the
    pre-cycle-2 UI keeps working. Setting MAILFALLBACK_USE_INDEX_SEARCH=false
    falls back to the legacy mount-based path.
    """
    if not settings.use_index_search:
        return _legacy_mount_workspace_search(req, request, user, db)

    new_result = search_service.search_messages(
        db,
        user=user,
        query=req.query,
        account_ids=[req.account_id],
        range_start=req.range_start,
        range_end=req.range_end,
        include_deleted=req.include_snapshots,
        deep=req.deep,
        page=1,
        page_size=200,
    )

    # Resolve IMAP UIDs by Message-Id so cycle-1 UI's Restore Selected works.
    # The pre-cycle-2 UI passes location.uid back to /api/restore — without
    # this lookup the wrapper would return null UIDs and the restore fails.
    # Group by (account, folder) so we share the SELECT across messages in
    # the same folder. Cap is page_size=200 above.
    msgids_by_account_folder: dict[tuple[str, str], list[str]] = {}
    for r in new_result["results"]:
        if not r["alive_in_live"]:
            continue
        key = (r["account_id"], r["folder_path"])
        msgids_by_account_folder.setdefault(key, []).append(r["message_id"])

    uid_by_msgid: dict[str, str | None] = {}
    if msgids_by_account_folder:
        accounts_seen = {acct_id for acct_id, _ in msgids_by_account_folder}
        for acct_id in accounts_seen:
            account = account_service.get_account(db, acct_id, user)
            if not account:
                continue
            try:
                conn, temp_user = _connect_dovecot_for_account(db, account)
            except Exception:
                logger.warning(
                    "UID resolution: Dovecot connect failed for %s",
                    acct_id,
                    exc_info=True,
                )
                continue
            try:
                ns = account_namespace_prefix(account)
                folders_for_account = {
                    folder for (a, folder), _ in msgids_by_account_folder.items() if a == acct_id
                }
                for folder in folders_for_account:
                    target = f'"{ns}{_sanitize_imap_string(folder)}"'
                    typ, _ = conn.select(target, readonly=True)
                    if typ != "OK":
                        continue
                    msgids = msgids_by_account_folder[(acct_id, folder)]
                    for msgid in msgids:
                        quoted = _sanitize_imap_string(msgid)
                        typ, data = conn.uid("SEARCH", "HEADER", "Message-Id", f'"{quoted}"')
                        if typ == "OK" and data and data[0]:
                            uids = data[0].decode().split()
                            if uids:
                                uid_by_msgid[msgid] = uids[0]
            finally:
                with contextlib.suppress(Exception):
                    conn.logout()
                with contextlib.suppress(Exception):
                    delete_temp_imap_user(db, temp_user)

    legacy_results = []
    for r in new_result["results"]:
        if r["alive_in_live"]:
            primary_source = "live"
        elif r["snapshots"]:
            primary_source = r["snapshots"][0]
        else:
            primary_source = "?"
        legacy_results.append(
            {
                "message_id": r["message_id"],
                "subject": r["subject"],
                "from": r["from_addr"] or "",
                "folder": r["folder_path"],
                "sources": (["live"] if r["alive_in_live"] else []) + r["snapshots"],
                "locations": [
                    {
                        "source": primary_source,
                        "namespace": "",
                        "folder": r["folder_path"],
                        "uid": uid_by_msgid.get(r["message_id"]),
                    }
                ],
            }
        )
    return {
        "results": legacy_results,
        "mounted_snapshots": [],
        "partial": new_result.get("partial", False),
    }


def _legacy_mount_workspace_search(req, request, user, db):
    """The pre-index-search implementation, kept behind the use_index_search=False
    feature flag for fallback during rollout. Cycle-1 UI continues to work via
    the dispatcher above.
    """
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
        # the temp IMAP user. Both call recovery_namespace_prefix as the
        # single source of truth.
        ns_label = recovery_namespace_prefix(rec, account.name)
        mounted.append((snap_id, ns_label))

    # Resolve which IMAP fields to search across. UI defaults to Subject only;
    # if no flag is set we fall back to SUBJECT so the contract degrades safely.
    criteria: list[str] = []
    if req.search_subject:
        criteria.append("SUBJECT")
    if req.search_from:
        criteria.append("FROM")
    if req.search_to:
        criteria.append("TO")
    if req.deep:
        criteria.append("BODY")
    if not criteria:
        criteria = ["SUBJECT"]

    # Search live first, then each mounted snapshot.
    conn, temp_username = _connect_dovecot_for_account(db, account)
    try:
        if req.include_live:
            # B5: Dovecot publishes the live mailbox under the account's
            # full namespace prefix (e.g. "Andrea (andrea@x) [c260]/"); the
            # temp restore IMAP user inherits the same namespaces. Selecting
            # bare "INBOX" returns NO and the search silently yields 0 hits.
            live_prefix = account_namespace_prefix(account)
            for hit in _search_namespace_for_query(
                conn,
                namespace=live_prefix,
                query=req.query,
                criteria_fields=criteria,
                type_filter=req.type_filter,
            ):
                _merge_hit(results_by_msgid, hit, source_label="live")
        for snap_id, ns in mounted:
            for hit in _search_namespace_for_query(
                conn,
                namespace=ns,
                query=req.query,
                criteria_fields=criteria,
                type_filter=req.type_filter,
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


class RestoreSearchRequest(BaseModel):
    query: str = ""
    account_ids: list[str] | None = None
    range_start: datetime | None = None
    range_end: datetime | None = None
    include_deleted: bool = True
    snapshot_id: str | None = None
    deep: bool = False
    page: int = 1
    page_size: int = 50


@router.post("/search")
def api_restore_search(
    req: RestoreSearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return search_service.search_messages(
        db,
        user=user,
        query=req.query,
        account_ids=req.account_ids,
        range_start=req.range_start,
        range_end=req.range_end,
        include_deleted=req.include_deleted,
        snapshot_id=req.snapshot_id,
        deep=req.deep,
        page=req.page,
        page_size=req.page_size,
    )


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


class WorkspaceSnapshotDatesRequest(BaseModel):
    account_id: str


@router.post("/workspace/snapshot-dates")
def workspace_snapshot_dates(
    req: WorkspaceSnapshotDatesRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return distinct YYYY-MM-DD strings of days that have at least one snapshot.

    Used by the workspace calendar to highlight days that contain restorable data.
    """
    account = account_service.get_account(db, req.account_id, user)
    if not account:
        raise HTTPException(404, "account not found")

    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == req.account_id).first()
    if not backup:
        return {"dates": []}

    try:
        snaps = restic_service.list_snapshots(backup.destination, account.id)
    except Exception:
        return {"dates": []}

    dates = set()
    for s in snaps:
        ts_raw = s.get("time", "").replace("Z", "+00:00")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        dates.add(ts.strftime("%Y-%m-%d"))

    return {"dates": sorted(dates)}


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
