import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.models import BackupPolicy, BackupStatus, JobStatus, UserRole
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.security import decrypt_credentials
from mailfallback.services.account_service import get_account, get_accounts_for_user
from mailfallback.services.recovery_service import list_recoveries_for_user_accounts
from mailfallback.services.restore_service import (
    get_restore_job,
    list_all_restore_jobs,
    list_restore_jobs_for_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ui-restore"])

CALENDAR_DAYS = 30


def _compute_health(policies: list[BackupPolicy]) -> str:
    """Derive the overall health verdict for the /restore banner.

    `unprotected` — no policies at all
    `critical`    — at least one protected mailbox has no successful run in 7+ days
    `attention`   — at least one recent failure but everyone has a recent success
    `all_clear`   — every protected mailbox succeeded in the last 24h
    """
    if not policies:
        return "unprotected"
    now = datetime.now(UTC)
    for p in policies:
        last_ok = p.last_successful_run_at
        if last_ok is None or (now - last_ok) > timedelta(days=7):
            return "critical"
    has_recent_failure = any(p.last_status == BackupStatus.failed for p in policies)
    if has_recent_failure:
        return "attention"
    for p in policies:
        last_ok = p.last_successful_run_at
        if last_ok is None or (now - last_ok) > timedelta(hours=24):
            return "attention"
    return "all_clear"


@router.get("/restore", response_class=HTMLResponse)
def restore_page(request: Request, db: Session = Depends(get_db)):
    """The /restore Calendar of Safety: per-mailbox snapshot dot-strip,
    health banner, existing recoveries, and a footer link to the IMAP-move tool.

    Replaces the prior 2-card chooser at this URL.
    """
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")

    accounts = get_accounts_for_user(db, user)
    account_ids = [a.id for a in accounts]
    policies = (
        db.query(BackupPolicy).filter(BackupPolicy.account_id.in_(account_ids)).all()
        if account_ids
        else []
    )
    by_account = {p.account_id: p for p in policies}

    # Build the protected/unprotected lists used by the template.
    protected = [(a, by_account[a.id]) for a in accounts if a.id in by_account]
    unprotected = [a for a in accounts if a.id not in by_account]

    health = _compute_health(policies)
    total_snapshots = sum((p.last_snapshot_count or 0) for p in policies)
    most_recent = max(
        (p.last_snapshot_at for p in policies if p.last_snapshot_at),
        default=None,
    )

    # "Since you last visited this page" diff — session-only, no DB column needed.
    last_visit_iso = request.session.get("last_restore_visit")
    last_visit = datetime.fromisoformat(last_visit_iso) if last_visit_iso else None
    if last_visit is not None:
        new_snapshot_mailboxes = sum(
            1 for p in policies if p.last_snapshot_at and p.last_snapshot_at > last_visit
        )
    else:
        new_snapshot_mailboxes = 0
    request.session["last_restore_visit"] = datetime.now(UTC).isoformat()

    recoveries = list_recoveries_for_user_accounts(db, account_ids)

    return templates.TemplateResponse(
        request=request,
        name="restore.html",
        context={
            "user": user,
            "protected": protected,
            "unprotected": unprotected,
            "health": health,
            "total_snapshots": total_snapshots,
            "most_recent_snapshot": most_recent,
            "new_snapshot_mailboxes": new_snapshot_mailboxes,
            "recoveries": recoveries,
            "calendar_days": CALENDAR_DAYS,
        },
    )


@router.get("/restore/partials/calendar/{account_id}", response_class=HTMLResponse)
def restore_calendar_row(account_id: str, request: Request, db: Session = Depends(get_db)):
    """HTMX-loaded fragment: the 30-day dot strip for a single mailbox.

    Lazy-loaded after the page renders so /restore TTI doesn't block on restic.
    Degraded rendering (one-line "couldn't reach repository") on any restic error.
    """
    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")
    account = get_account(db, account_id, user)
    if not account:
        return HTMLResponse("")
    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == account_id).first()
    if not backup:
        return HTMLResponse("")

    # Bucket snapshots by local date for the last CALENDAR_DAYS window.
    today = datetime.now(UTC).date()
    window_start = today - timedelta(days=CALENDAR_DAYS - 1)
    snapshots_by_day: dict[str, list[dict]] = {}
    degraded = False
    try:
        from mailfallback.services.restic_service import list_snapshots

        snaps = list_snapshots(backup.destination, account.id)
        for s in snaps:
            ts_str = (s.get("time") or "").replace("Z", "+00:00")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
            except ValueError:
                continue
            day = ts.date()
            if day < window_start or day > today:
                continue
            key = day.isoformat()
            snapshots_by_day.setdefault(key, []).append(
                {
                    "ts": ts,
                    "short_id": s.get("short_id", ""),
                    "id": s.get("id", ""),
                }
            )
    except Exception as e:
        logger.warning("restic list_snapshots failed for %s: %s", account_id, e)
        degraded = True

    # Build the per-day rendering data: oldest left, newest right.
    days = []
    for i in range(CALENDAR_DAYS):
        day = window_start + timedelta(days=i)
        key = day.isoformat()
        bucket = snapshots_by_day.get(key, [])
        days.append(
            {
                "date": day,
                "iso": key,
                "is_today": day == today,
                "snapshots": bucket,
                "filled": bool(bucket),
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="partials/restore_calendar_row.html",
        context={
            "account": account,
            "policy": backup,
            "days": days,
            "degraded": degraded,
            "filled_count": sum(1 for d in days if d["filled"]),
        },
    )


@router.get("/restore/move", response_class=HTMLResponse)
def restore_move_page(request: Request, show_all: str = "", db: Session = Depends(get_db)):
    """The IMAP-to-IMAP restore form (formerly served at /restore).

    The page URL changed in Wave 3; the partial endpoints under
    /restore/partials/* are unchanged for backward compatibility.
    """
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    accounts = get_accounts_for_user(db, user)
    is_admin = user.role == UserRole.admin
    show_all_users = is_admin and show_all == "1"
    if show_all_users:
        all_jobs = list_all_restore_jobs(db)
    else:
        all_jobs = list_restore_jobs_for_user(db, user.id)
    running_jobs = [j for j in all_jobs if j.status in (JobStatus.pending, JobStatus.running)]
    jobs = [j for j in all_jobs if j.status not in (JobStatus.pending, JobStatus.running)]
    return templates.TemplateResponse(
        request=request,
        name="restore_move.html",
        context={
            "user": user,
            "accounts": accounts,
            "jobs": jobs,
            "running_jobs": running_jobs,
            "show_all_users": show_all_users,
        },
    )


@router.get("/restore/jump")
def restore_jump(account_id: str, request: Request, db: Session = Depends(get_db)):
    """Form target for the /restore "Pick a snapshot" button — bounces the
    user to the chosen mailbox's off-site admin section, where the snapshot
    picker is live.
    """
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    if not get_account(db, account_id, user):
        return RedirectResponse("/restore")
    return RedirectResponse(f"/accounts/{account_id}#admin-offsite")


@router.get("/recover")
def recover_redirect():
    """Legacy URL — the standalone snapshot list folded into /restore (Calendar
    of Safety). Permanent redirect so old bookmarks and any inbound links from
    elsewhere in the app keep working.
    """
    return RedirectResponse("/restore", status_code=301)


@router.get("/restore/partials/running", response_class=HTMLResponse)
def restore_running_partial(
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")
    all_jobs = list_restore_jobs_for_user(db, user.id)
    running_jobs = [j for j in all_jobs if j.status in (JobStatus.pending, JobStatus.running)]
    return templates.TemplateResponse(
        request=request,
        name="partials/restore_running.html",
        context={"running_jobs": running_jobs},
    )


@router.get("/restore/partials/folders", response_class=HTMLResponse)
def restore_folders_partial(
    request: Request,
    source_account_id: str = "",
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or not source_account_id:
        return HTMLResponse("")

    account = get_account(db, source_account_id, user)
    if not account:
        return HTMLResponse("")

    from mailfallback.config import settings
    from mailfallback.services.dovecot_auth import create_temp_imap_user, delete_temp_imap_user
    from mailfallback.services.imap_check import connect_imap

    temp_username = None
    try:
        temp_username, temp_password = create_temp_imap_user(db, [account.id])
        conn = connect_imap(
            settings.dovecot_imap_host,
            settings.dovecot_imap_port,
            "NONE",
            temp_username,
            temp_password,
        )
        prefix = f"{account.name} ({account.email_address}) [{account.id[-4:]}]/"
        status, folder_data = conn.list(f'"{prefix}"', "*")
        folders = []
        if status == "OK" and folder_data:
            for item in folder_data:
                if not item:
                    continue
                decoded = item.decode() if isinstance(item, bytes) else item
                parts = decoded.rsplit('" "', 1)
                if len(parts) < 2:
                    continue
                full_name = parts[1].rstrip('"')
                short_name = full_name.removeprefix(prefix)
                messages = 0
                try:
                    import imaplib
                    import re

                    st, st_data = conn.status(f'"{full_name}"', "(MESSAGES)")
                    if st == "OK" and st_data:
                        match = re.search(r"MESSAGES (\d+)", st_data[0].decode())
                        if match:
                            messages = int(match.group(1))
                except imaplib.IMAP4.error:
                    pass
                folders.append({"name": short_name, "full_name": full_name, "messages": messages})
        conn.logout()
    except Exception:
        folders = []
    finally:
        if temp_username:
            delete_temp_imap_user(db, temp_username)

    return templates.TemplateResponse(
        request=request,
        name="partials/restore_folders.html",
        context={"folders": folders},
    )


@router.get("/restore/partials/separator-warning", response_class=HTMLResponse)
def restore_separator_warning_partial(
    request: Request,
    target_account_id: str = "",
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or not target_account_id:
        return HTMLResponse('<div id="separator-warning" class="hidden"></div>')

    account = get_account(db, target_account_id, user)
    if not account or not account.credentials:
        return HTMLResponse('<div id="separator-warning" class="hidden"></div>')

    from mailfallback.config import settings
    from mailfallback.services.imap_check import connect_imap

    separator = None
    error = None
    try:
        creds = decrypt_credentials(account.credentials, settings.secret_key)
        if account.auth_type.value == "oauth2":
            import asyncio
            import json

            token_data = json.loads(creds)
            refresh_token = token_data.get("refresh_token", "")
            provider = token_data.get("provider", "google")
            if refresh_token:
                from mailfallback.services.oauth2 import (
                    refresh_google_token,
                    refresh_microsoft_token,
                )

                refresh_fn = {"microsoft": refresh_microsoft_token}.get(
                    provider, refresh_google_token
                )
                password = asyncio.run(refresh_fn(refresh_token))
            else:
                password = token_data.get("access_token", "")
        else:
            password = creds

        conn = connect_imap(
            account.imap_host,
            account.imap_port,
            account.tls_type or "IMAPS",
            account.imap_user or account.email_address,
            password,
            timeout=10,
        )
        try:
            import re

            status, data = conn.list('""', '""')
            if status == "OK" and data and data[0]:
                decoded = data[0].decode() if isinstance(data[0], bytes) else data[0]
                match = re.search(r'"(.)"', decoded)
                if match:
                    separator = match.group(1)
        finally:
            conn.logout()
    except Exception:
        error = "Could not connect to destination server to check folder separator."

    if separator == ".":
        html = (
            '<div id="separator-warning" class="warning-box">'
            '<i data-lucide="triangle-alert" class="icon-sm icon-inline"></i> '
            "<strong>Dot separator detected.</strong> "
            "The destination server uses <code>.</code> as its folder hierarchy separator. "
            "Source folders with dots in their names will be automatically escaped "
            "(e.g. <code>My.Archive</code> becomes <code>My_Archive</code>)."
            "<script>lucide.createIcons()</script>"
            "</div>"
        )
    elif error:
        html = (
            '<div id="separator-warning" class="info-box">'
            '<i data-lucide="info" class="icon-sm icon-inline"></i> '
            f"{error}"
            "<script>lucide.createIcons()</script>"
            "</div>"
        )
    else:
        html = '<div id="separator-warning" class="hidden"></div>'

    return HTMLResponse(html)


@router.get("/restore/partials/messages", response_class=HTMLResponse)
def restore_messages_partial(
    request: Request,
    source_account_id: str = "",
    search_folder: str = "",
    search_query: str = "",
    search_in: str = "text",
    type_filter: str = "all",
    date_since: str = "",
    date_before: str = "",
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or not source_account_id or not search_query:
        return HTMLResponse("")

    account = get_account(db, source_account_id, user)
    if not account:
        return HTMLResponse("")

    from mailfallback.config import settings
    from mailfallback.services.dovecot_auth import create_temp_imap_user, delete_temp_imap_user
    from mailfallback.services.imap_check import connect_imap

    prefix = f"{account.name} ({account.email_address}) [{account.id[-4:]}]/"
    search_all = search_folder == "*"
    temp_username = None
    messages = []
    try:
        temp_username, temp_password = create_temp_imap_user(db, [account.id])
        conn = connect_imap(
            settings.dovecot_imap_host,
            settings.dovecot_imap_port,
            "NONE",
            temp_username,
            temp_password,
        )

        criteria = _build_search_criteria(
            search_query, search_in, type_filter, date_since, date_before
        )

        if search_all:
            folders = _list_account_folders(conn, prefix)
        else:
            folders = [(f"{prefix}{search_folder}", search_folder)]

        for full_folder, short_folder in folders:
            if len(messages) >= 100:
                break
            status, _ = conn.select(f'"{full_folder}"', readonly=True)
            if status != "OK":
                continue
            status, data = conn.search(None, *criteria)
            if status != "OK" or not data[0]:
                continue
            uids = data[0].split()
            remaining = 100 - len(messages)
            for uid in uids[:remaining]:
                msg = _fetch_message_header(conn, uid, short_folder if search_all else None)
                if msg:
                    messages.append(msg)

        conn.logout()
    except Exception:
        messages = []
    finally:
        if temp_username:
            delete_temp_imap_user(db, temp_username)

    return templates.TemplateResponse(
        request=request,
        name="partials/restore_messages.html",
        context={"messages": messages, "show_folder": search_all},
    )


_FIELD_MAP = {
    "subject": "SUBJECT",
    "from": "FROM",
    "reply_to": "HEADER Reply-To",
    "followup_to": "HEADER Followup-To",
    "to": "TO",
    "cc": "CC",
    "bcc": "BCC",
    "body": "BODY",
    "text": "TEXT",
}

_TYPE_MAP = {
    "unseen": ["UNSEEN"],
    "flagged": ["FLAGGED"],
    "unanswered": ["UNANSWERED"],
    "deleted": ["DELETED"],
    "undeleted": ["UNDELETED"],
    "attachment": ["HEADER", "Content-Type", "multipart/mixed"],
}


def _build_search_criteria(query, search_in, type_filter, date_since, date_before):
    fields = [f.strip() for f in search_in.split(",") if f.strip()]
    if "text" in fields:
        fields = ["text"]

    imap_fields = []
    for f in fields:
        if f in _FIELD_MAP:
            imap_fields.append(_FIELD_MAP[f])

    if not imap_fields:
        imap_fields = ["TEXT"]

    criteria = []
    words = query.split()
    for word in words:
        quoted = f'"{word}"'
        if len(imap_fields) == 1:
            field = imap_fields[0]
            if " " in field:
                parts = field.split(" ", 1)
                criteria.extend([parts[0], parts[1], quoted])
            else:
                criteria.extend([field, quoted])
        else:
            group = _build_or_group(imap_fields, quoted)
            criteria.extend(group)

    if type_filter in _TYPE_MAP:
        criteria.extend(_TYPE_MAP[type_filter])

    if date_since:
        criteria.extend(["SINCE", _to_imap_date(date_since)])
    if date_before:
        criteria.extend(["BEFORE", _to_imap_date(date_before)])

    return criteria if criteria else ["ALL"]


def _build_or_group(fields, quoted_word):
    if len(fields) == 1:
        field = fields[0]
        if " " in field:
            parts = field.split(" ", 1)
            return [parts[0], parts[1], quoted_word]
        return [field, quoted_word]

    def _field_criteria(field):
        if " " in field:
            parts = field.split(" ", 1)
            return [parts[0], parts[1], quoted_word]
        return [field, quoted_word]

    if len(fields) == 2:
        return ["OR", *_field_criteria(fields[0]), *_field_criteria(fields[1])]

    result = ["OR", *_field_criteria(fields[0])]
    remaining = fields[1:]
    result.extend(_build_or_group(remaining, quoted_word))
    return result


def _to_imap_date(iso_date):
    from datetime import datetime

    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    return f"{dt.day}-{months[dt.month - 1]}-{dt.year}"


def _list_account_folders(conn, prefix):
    import imaplib

    folders = []
    try:
        status, folder_data = conn.list(f'"{prefix}"', "*")
        if status == "OK" and folder_data:
            for item in folder_data:
                if not item:
                    continue
                decoded = item.decode() if isinstance(item, bytes) else item
                parts = decoded.rsplit('" "', 1)
                if len(parts) < 2:
                    continue
                full_name = parts[1].rstrip('"')
                short_name = full_name.removeprefix(prefix)
                folders.append((full_name, short_name))
    except imaplib.IMAP4.error:
        pass
    return folders


def _fetch_message_header(conn, uid, folder_name=None):
    import email as email_mod

    status, msg_data = conn.fetch(
        uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)] FLAGS)"
    )
    if status != "OK" or not msg_data or not msg_data[0]:
        return None
    if not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2:
        return None

    header_bytes = msg_data[0][1]
    parsed = email_mod.message_from_bytes(header_bytes)

    msg = {
        "uid": int(uid),
        "subject": _decode_mime_header(parsed.get("Subject", "(no subject)")),
        "from": _decode_mime_header(parsed.get("From", "")),
        "to": _decode_mime_header(parsed.get("To", "")),
        "date": parsed.get("Date", ""),
        "message_id": parsed.get("Message-ID", ""),
    }
    if folder_name is not None:
        msg["folder"] = folder_name
    return msg


def _decode_mime_header(value):
    from email.header import decode_header

    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


@router.get("/restore/partials/progress", response_class=HTMLResponse)
def restore_progress_partial(
    request: Request,
    job_id: str = "",
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or not job_id:
        return HTMLResponse("")

    job = get_restore_job(db, job_id)
    finished = job and job.status.value in ("completed", "failed", "cancelled")

    return templates.TemplateResponse(
        request=request,
        name="partials/restore_progress.html",
        context={"job": job, "job_id": job_id, "finished": finished},
    )
