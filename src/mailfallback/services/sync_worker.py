# src/mailfallback/services/sync_worker.py
import os
import subprocess
import tempfile
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Account, JobStatus, SyncJob, SyncState
from mailfallback.security import decrypt_credentials
from mailfallback.services.mbsync_config import generate_mbsyncrc


def execute_sync_job(db: Session, job_id: str) -> None:
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        return

    account = db.query(Account).filter(Account.id == job.account_id).first()
    if not account:
        job.status = JobStatus.failed
        job.log = "Account not found"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    job.status = JobStatus.running
    job.started_at = datetime.now(timezone.utc)
    account.sync_state = SyncState.syncing
    db.commit()

    password = None
    token_command = None
    if account.credentials:
        creds = decrypt_credentials(account.credentials, settings.secret_key)
        if account.auth_type.value == "oauth2":
            token_command = creds
        else:
            password = creds

    config_content = generate_mbsyncrc(
        account_name=account.name.lower().replace(" ", "_"),
        imap_host=account.imap_host,
        imap_port=account.imap_port,
        username=account.name,
        auth_type=account.auth_type.value,
        maildir_path=account.maildir_path,
        password=password,
        token_command=token_command,
    )

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mbsyncrc", delete=False) as f:
            f.write(config_content)
            config_path = f.name

        os.makedirs(account.maildir_path, exist_ok=True)

        result = subprocess.run(
            [settings.mbsync_binary, "-c", config_path, "-a"],
            capture_output=True,
            text=True,
            timeout=3600,
        )

        job.exit_code = result.returncode
        job.log = (result.stdout + "\n" + result.stderr).strip()
        job.completed_at = datetime.now(timezone.utc)

        if result.returncode == 0:
            job.status = JobStatus.completed
            account.sync_state = SyncState.idle
            account.last_sync_at = datetime.now(timezone.utc)
            account.last_error = None
        else:
            job.status = JobStatus.failed
            account.sync_state = SyncState.error
            account.last_error = job.log

    except subprocess.TimeoutExpired:
        job.status = JobStatus.failed
        job.log = "Sync timed out after 3600 seconds"
        job.completed_at = datetime.now(timezone.utc)
        account.sync_state = SyncState.error
        account.last_error = job.log

    except Exception as e:
        job.status = JobStatus.failed
        job.log = str(e)
        job.completed_at = datetime.now(timezone.utc)
        account.sync_state = SyncState.error
        account.last_error = str(e)

    finally:
        if "config_path" in locals():
            os.unlink(config_path)
        db.commit()
