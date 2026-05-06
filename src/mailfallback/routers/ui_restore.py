from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.services.account_service import get_account, get_accounts_for_user
from mailfallback.services.restore_service import get_restore_job, list_restore_jobs_for_user

router = APIRouter(tags=["ui-restore"])


@router.get("/restore", response_class=HTMLResponse)
def restore_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    accounts = get_accounts_for_user(db, user)
    jobs = list_restore_jobs_for_user(db, user.id)
    return templates.TemplateResponse(
        request=request,
        name="restore.html",
        context={"user": user, "accounts": accounts, "jobs": jobs},
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
    finished = job and job.status.value in ("completed", "failed")

    return templates.TemplateResponse(
        request=request,
        name="partials/restore_progress.html",
        context={"job": job, "job_id": job_id, "finished": finished},
    )
