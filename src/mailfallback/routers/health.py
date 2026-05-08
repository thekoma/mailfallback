# src/mailfallback/routers/health.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.models import Account, JobStatus, SyncJob

router = APIRouter(tags=["health"])

registry = CollectorRegistry()
SYNC_TOTAL = Counter(
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
        return {"status": "error", "checks": checks}

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

    return generate_latest(registry).decode()
