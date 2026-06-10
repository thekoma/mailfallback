"""Config-backup scheduler job reconciliation."""

from unittest.mock import MagicMock, patch

from mailfallback.config import settings
from mailfallback.models import BackendType, Repository
from mailfallback.security import encrypt_credentials
from mailfallback.services.scheduler import config_backup_scheduler_jobs


def _repo(db_session, enabled=True):
    r = Repository(
        name="cfg-repo",
        backend_type=BackendType.local,
        local_path=encrypt_credentials("/backups", settings.secret_key),
        restic_password=encrypt_credentials("rp", settings.secret_key),  # pragma: allowlist secret
        config_backup_enabled=enabled,
        config_backup_passphrase=encrypt_credentials(
            "longpassphrase", settings.secret_key
        ),  # pragma: allowlist secret
    )
    db_session.add(r)
    db_session.commit()
    return r


@patch("mailfallback.services.scheduler.scheduler")
def test_adds_job_for_enabled_repo(mock_sched, db_session):
    mock_sched.get_jobs.return_value = []
    repo = _repo(db_session, enabled=True)

    config_backup_scheduler_jobs(db_session)

    add_call = mock_sched.add_job.call_args
    assert add_call.kwargs["id"] == f"config-backup-{repo.id}"


@patch("mailfallback.services.scheduler.scheduler")
def test_removes_job_for_disabled_repo(mock_sched, db_session):
    repo = _repo(db_session, enabled=False)
    job = MagicMock()
    job.id = f"config-backup-{repo.id}"
    mock_sched.get_jobs.return_value = [job]

    config_backup_scheduler_jobs(db_session)

    mock_sched.remove_job.assert_called_once_with(f"config-backup-{repo.id}")


@patch("mailfallback.services.scheduler.scheduler")
def test_reschedules_existing_job(mock_sched, db_session):
    repo = _repo(db_session, enabled=True)
    job = MagicMock()
    job.id = f"config-backup-{repo.id}"
    mock_sched.get_jobs.return_value = [job]

    config_backup_scheduler_jobs(db_session)

    mock_sched.reschedule_job.assert_called_once()
    mock_sched.add_job.assert_not_called()
