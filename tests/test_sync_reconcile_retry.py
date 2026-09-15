from unittest.mock import patch

from test_sync_worker import (
    PATCH_RC,
    _isolated_sampler_sessions,  # noqa: F401 -- autouse fixture, offline DB + connect_imap
    _mk_maildir_account_and_job,
    _proc,
    _public_dns,  # noqa: F401 -- autouse fixture, SSRF guard treats test hosts as public
    make_session,
)

from mailfallback.models import JobStatus
from mailfallback.services import sync_worker


def _mk_folder(root, name, state=True):
    (root / name / "cur").mkdir(parents=True)
    if state:
        (root / name / ".mbsyncstate").write_text("FarUidValidity 1\n")


DEAD_BOX = "Maildir error: far side box push-dixie cannot be opened anymore."


def test_a_throttled_failure_never_lists_the_provider(tmp_path):
    """LISTing a provider that has just throttled us adds load at exactly the
    wrong moment, and says nothing about which folders exist."""
    session = make_session()
    _account, job = _mk_maildir_account_and_job(session, tmp_path)
    session.commit()

    with (
        patch(
            "mailfallback.services.sync_worker.subprocess.Popen",
            side_effect=lambda cmd, **kw: _proc(["[OVERQUOTA] quota exceeded"], code=1),
        ),
        PATCH_RC,
        patch.object(sync_worker, "_reconcile_removed_folders") as reconcile,
    ):
        sync_worker.execute_sync_job(session, job.id)

    reconcile.assert_not_called()


def test_a_dead_box_is_quarantined_and_mbsync_runs_again(tmp_path):
    session = make_session()
    _account, job = _mk_maildir_account_and_job(session, tmp_path)
    maildir = tmp_path / "maildir"
    _mk_folder(maildir, "INBOX")
    _mk_folder(maildir, "push-dixie")
    session.commit()

    # inbox pass fails (full pass skipped by the stop-at-first-failure rule),
    # then the retry re-runs both passes and both succeed.
    codes = iter([1, 0, 0])

    def fake_popen(cmd, **kw):
        code = next(codes)
        return _proc([DEAD_BOX] if code else ["OK"], code=code)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
        patch.object(sync_worker, "_list_upstream_folders", return_value={"INBOX"}),
    ):
        sync_worker.execute_sync_job(session, job.id)

    session.refresh(job)
    assert job.status == JobStatus.completed
    assert not (maildir / "push-dixie").exists()
    assert list((maildir / "Removed from Source").glob("push-dixie (*)"))


def test_a_retry_that_fails_again_is_a_real_error(tmp_path):
    session = make_session()
    _account, job = _mk_maildir_account_and_job(session, tmp_path)
    maildir = tmp_path / "maildir"
    _mk_folder(maildir, "INBOX")
    _mk_folder(maildir, "push-dixie")
    session.commit()

    calls = []

    def fake_popen(cmd, **kw):
        calls.append(cmd)
        return _proc([DEAD_BOX], code=1)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
        patch.object(sync_worker, "_list_upstream_folders", return_value={"INBOX"}),
    ):
        sync_worker.execute_sync_job(session, job.id)

    session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.failure_kind == "error"
    # One inbox pass, then one retry. Never a loop: a retry that can retry is
    # a way to hammer a provider that is already unhappy.
    assert len(calls) == 2


def test_nothing_quarantined_means_no_retry(tmp_path):
    session = make_session()
    _account, job = _mk_maildir_account_and_job(session, tmp_path)
    _mk_folder(tmp_path / "maildir", "INBOX")
    session.commit()

    calls = []

    def fake_popen(cmd, **kw):
        calls.append(cmd)
        return _proc(["Error: some other failure"], code=1)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
        patch.object(sync_worker, "_list_upstream_folders", return_value={"INBOX"}),
    ):
        sync_worker.execute_sync_job(session, job.id)

    assert len(calls) == 1


def test_a_provider_anomaly_quarantines_nothing(tmp_path, caplog):
    """Twelve folders local, two upstream: far likelier a broken LIST or a
    provider outage than twelve deliberate deletions."""
    session = make_session()
    account, _job = _mk_maildir_account_and_job(session, tmp_path)
    maildir = tmp_path / "maildir"
    for i in range(12):
        _mk_folder(maildir, f"f{i}")
    session.commit()

    with patch.object(sync_worker, "_list_upstream_folders", return_value={"f0", "f1"}):
        quarantined = sync_worker._reconcile_removed_folders(session, account, "p", None)

    assert quarantined == []
    assert not (maildir / "Removed from Source").exists()


def test_a_failed_list_quarantines_nothing(tmp_path):
    session = make_session()
    account, _job = _mk_maildir_account_and_job(session, tmp_path)
    maildir = tmp_path / "maildir"
    _mk_folder(maildir, "INBOX")
    session.commit()

    with patch.object(sync_worker, "_list_upstream_folders", side_effect=OSError("no route")):
        assert sync_worker._reconcile_removed_folders(session, account, "p", None) == []
