# src/mailfallback/routers/sync.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import User
from mailfallback.services import account_service, sync_service

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/{account_id}")
def trigger_sync(
    account_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    job = sync_service.create_sync_job(db, account_id, source="api")
    if not job:
        raise HTTPException(status_code=409, detail="Sync already pending or running")
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
