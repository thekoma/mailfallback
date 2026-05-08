# src/mailfallback/routers/sync.py
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import User
from mailfallback.services import account_service, sync_service
from mailfallback.services.audit_service import log_action
from mailfallback.services.imap_check import check_imap_credentials, validate_host_not_internal
from mailfallback.services.provider_discovery import discover_provider
from mailfallback.services.sync_worker import get_live_log, stop_sync_job, submit_sync_job

router = APIRouter(prefix="/api/sync", tags=["sync"])


class TestConnectionRequest(BaseModel):
    imap_host: str
    imap_port: int = 993
    tls_type: str = "IMAPS"
    username: str | None = None
    password: str | None = None


@router.post("/test-connection")
def test_connection(
    body: TestConnectionRequest,
    user: User = Depends(get_current_user),
):
    try:
        validate_host_not_internal(body.imap_host)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return check_imap_credentials(
        host=body.imap_host,
        port=body.imap_port,
        tls_type=body.tls_type,
        username=body.username,
        password=body.password,
    )


@router.get("/discover/{domain}")
def discover(domain: str, user: User = Depends(get_current_user)):
    result = discover_provider(domain)
    if not result:
        return {"ok": False, "message": f"No IMAP configuration found for {domain}"}
    return {"ok": True, **result}


@router.post("/all")
def trigger_sync_all(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    accounts = account_service.get_accounts_for_user(db, user)
    triggered = 0
    for account in accounts:
        if account.suspended or account.migrating or not account.is_authenticated:
            continue
        if account.sync_state.value == "syncing":
            continue
        job = sync_service.create_sync_job(db, account.id, source="manual")
        if job:
            submit_sync_job(job.id)
            triggered += 1
    return {"ok": True, "triggered": triggered}


@router.post("/{account_id}")
def trigger_sync(
    account_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if account.suspended:
        raise HTTPException(status_code=409, detail="Sync blocked: account is suspended")

    if account.migrating:
        raise HTTPException(status_code=409, detail="Sync blocked: account migration in progress")

    for owner in account.owners:
        if owner.migrating:
            raise HTTPException(status_code=409, detail="Sync blocked: user migration in progress")

    job = sync_service.create_sync_job(db, account_id, source="manual")
    if not job:
        raise HTTPException(status_code=409, detail="Sync already pending or running")

    submit_sync_job(job.id)
    log_action(
        db,
        user=user,
        action="account.sync",
        resource_type="account",
        resource_id=account_id,
        resource_name=account.email_address,
        ip_address=request.client.host if request.client else None,
    )
    return {"job_id": job.id, "status": job.status.value}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = sync_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    account = account_service.get_account(db, job.account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "account_id": job.account_id,
        "status": job.status.value,
        "source": job.source,
        "requested_at": job.requested_at.isoformat() if job.requested_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "exit_code": job.exit_code,
        "log": job.log,
    }


@router.get("/jobs/{job_id}/live-log")
def job_live_log(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = sync_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    account = account_service.get_account(db, job.account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Job not found")

    live = get_live_log(job_id)
    if live is not None:
        return {"status": "running", "log": live}
    return {"status": job.status.value, "log": job.log or ""}


@router.get("/jobs/{job_id}/log/download")
def job_log_download(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi.responses import PlainTextResponse

    job = sync_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    account = account_service.get_account(db, job.account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Job not found")

    log_text = job.log or ""
    return PlainTextResponse(
        content=log_text,
        headers={
            "Content-Disposition": f'attachment; filename="sync-{job_id[:8]}.log"',
        },
    )


@router.post("/{account_id}/stop")
def stop_sync(
    account_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from mailfallback.models import JobStatus, SyncJob

    running_job = (
        db.query(SyncJob)
        .filter(SyncJob.account_id == account_id, SyncJob.status == JobStatus.running)
        .first()
    )
    if not running_job:
        raise HTTPException(status_code=404, detail="No running sync job")

    stopped = stop_sync_job(running_job.id)
    if not stopped:
        raise HTTPException(status_code=404, detail="Process not found")
    return {"ok": True, "job_id": running_job.id}


@router.get("/jobs")
def list_jobs(
    account_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    jobs = sync_service.list_jobs_for_account(db, account_id)
    return [
        {
            "id": j.id,
            "status": j.status.value,
            "source": j.source,
            "requested_at": j.requested_at.isoformat() if j.requested_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            "exit_code": j.exit_code,
        }
        for j in jobs
    ]
