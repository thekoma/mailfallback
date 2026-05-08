# src/mailfallback/routers/health.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.models import Account, JobStatus, SyncJob

router = APIRouter(tags=["health"])

registry = CollectorRegistry()
SYNC_TOTAL = Gauge(
    "mailfallback_sync_total",
    "Total syncs by account and status",
    ["account", "status"],
    registry=registry,
)
SYNC_DURATION = Gauge(
    "mailfallback_sync_duration_seconds",
    "Duration of last sync",
    ["account"],
    registry=registry,
)
SYNC_LAST_SUCCESS = Gauge(
    "mailfallback_sync_last_success_timestamp",
    "Timestamp of last successful sync",
    ["account"],
    registry=registry,
)
MAILDIR_SIZE = Gauge(
    "mailfallback_maildir_size_bytes",
    "Maildir size per account",
    ["account"],
    registry=registry,
)
ACCOUNTS_TOTAL = Gauge(
    "mailfallback_accounts_total",
    "Total configured accounts",
    registry=registry,
)
JOBS_PENDING = Gauge(
    "mailfallback_jobs_pending",
    "Pending jobs in queue",
    registry=registry,
)


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = str(e)
        return JSONResponse(status_code=503, content={"status": "error", "checks": checks})

    return {"status": "ok", "checks": checks}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request, db: Session = Depends(get_db)):
    from mailfallback.config import settings

    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not settings.metrics_api_key or not token or token != settings.metrics_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    accounts = db.query(Account).all()
    ACCOUNTS_TOTAL.set(len(accounts))

    pending = db.query(SyncJob).filter(SyncJob.status == JobStatus.pending).count()
    JOBS_PENDING.set(pending)

    for account in accounts:
        if account.last_sync_at:
            SYNC_LAST_SUCCESS.labels(account=account.name).set(account.last_sync_at.timestamp())

        if account.maildir_size_bytes is not None:
            MAILDIR_SIZE.labels(account=account.name).set(account.maildir_size_bytes)

        for status in JobStatus:
            count = (
                db.query(SyncJob)
                .filter(SyncJob.account_id == account.id, SyncJob.status == status)
                .count()
            )
            SYNC_TOTAL.labels(account=account.name, status=status.value).set(count)

        latest_completed = (
            db.query(SyncJob)
            .filter(
                SyncJob.account_id == account.id,
                SyncJob.status == JobStatus.completed,
                SyncJob.started_at.isnot(None),
                SyncJob.completed_at.isnot(None),
            )
            .order_by(SyncJob.completed_at.desc())
            .first()
        )
        if latest_completed:
            duration = (latest_completed.completed_at - latest_completed.started_at).total_seconds()
            SYNC_DURATION.labels(account=account.name).set(duration)

    return generate_latest(registry).decode()
