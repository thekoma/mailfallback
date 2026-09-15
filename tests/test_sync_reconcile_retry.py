from unittest.mock import patch

from test_sync_worker import (
    DONE,
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


def test_a_budget_stop_never_reconciles(tmp_path):
    """A budget stop is a deliberate halt, not a failure to diagnose: it must
    never LIST a provider MFB just throttled itself against, and never
    relaunch mbsync past the budget that stopped it.

    Patches `_run_invocations` directly rather than `subprocess.Popen`: going
    through the real invocation loop would ALSO set `_killed_signals` as a
    side effect of its own budget-stop re-arm logic (SIGTERM bookkeeping in
    `stop_sync_job`), which would leave this test passing even if the
    `_budget_stops` guard clause were deleted — see
    test_a_killed_job_never_reconciles for that clause in isolation."""
    session = make_session()
    _account, job = _mk_maildir_account_and_job(session, tmp_path, initial_sync_completed_at=DONE)
    session.commit()

    def fake_run_invocations(job_id, invocations, account, log_file):
        sync_worker._budget_stops.add(job_id)
        sync_worker._running_logs[job_id].append("killed mid-fetch")
        return -15

    try:
        with (
            patch.object(sync_worker, "_run_invocations", side_effect=fake_run_invocations),
            PATCH_RC,
            patch.object(sync_worker, "_reconcile_removed_folders") as reconcile,
        ):
            sync_worker.execute_sync_job(session, job.id)
        reconcile.assert_not_called()
    finally:
        sync_worker._budget_stops.discard(job.id)


def test_a_killed_job_never_reconciles(tmp_path):
    """A cancelled sync (user Stop, or the runtime-cap watchdog) leaves an
    EMPTY log tail — classify_failure reads that as a real error by explicit
    contract, so the killed-job guard is the only thing standing between a
    cancelled job and mbsync being relaunched against the provider."""
    session = make_session()
    _account, job = _mk_maildir_account_and_job(session, tmp_path, initial_sync_completed_at=DONE)
    session.commit()

    def fake_run_invocations(job_id, invocations, account, log_file):
        sync_worker._killed_signals[job_id] = "SIGTERM"
        return -15  # log stays empty, as a killed proc's read loop produces

    try:
        with (
            patch.object(sync_worker, "_run_invocations", side_effect=fake_run_invocations),
            PATCH_RC,
            patch.object(sync_worker, "_reconcile_removed_folders") as reconcile,
        ):
            sync_worker.execute_sync_job(session, job.id)
        reconcile.assert_not_called()
    finally:
        sync_worker._killed_signals.pop(job.id, None)


def test_a_provider_anomaly_quarantines_nothing(tmp_path, caplog):
    """Twelve folders local, two upstream: far likelier a broken LIST or a
    provider outage than twelve deliberate deletions."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    maildir = tmp_path / "maildir"
    for i in range(12):
        _mk_folder(maildir, f"f{i}")
    session.commit()

    try:
        with patch.object(sync_worker, "_list_upstream_folders", return_value={"f0", "f1"}):
            quarantined = sync_worker._reconcile_removed_folders(
                job.id, session, account, "p", None
            )
        logged = "\n".join(sync_worker._running_logs.get(job.id, []))
    finally:
        sync_worker._running_logs.pop(job.id, None)

    assert quarantined == []
    assert not (maildir / "Removed from Source").exists()
    # The refusal is a deliberate decision, not a silent no-op: it must say so
    # somewhere the administrator actually reads.
    assert "10 of 12 folders missing from the Source" in logged


def test_a_refused_mass_quarantine_reaches_the_job_and_the_account(tmp_path):
    """The whole point of the guard is that MFB decided not to act. Without
    this, the account goes red with a raw mbsync error and nothing anywhere
    says a decision was made — the operator has no reason to look."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    maildir = tmp_path / "maildir"
    for i in range(12):
        _mk_folder(maildir, f"f{i}")
    session.commit()

    with (
        patch(
            "mailfallback.services.sync_worker.subprocess.Popen",
            side_effect=lambda cmd, **kw: _proc([DEAD_BOX], code=1),
        ),
        PATCH_RC,
        patch.object(sync_worker, "_list_upstream_folders", return_value={"f0", "f1"}),
    ):
        sync_worker.execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.failure_kind == "error"
    assert "No folder was moved to" in job.log
    assert "10 of 12 folders missing from the Source" in job.log
    assert "Check the Source, then sync again" in job.log
    # The account page reads last_error, so the reason has to travel there too.
    assert "10 of 12 folders missing from the Source" in account.last_error
    assert not (maildir / "Removed from Source").exists()


def test_a_failed_list_quarantines_nothing(tmp_path):
    session = make_session()
    account, _job = _mk_maildir_account_and_job(session, tmp_path)
    maildir = tmp_path / "maildir"
    _mk_folder(maildir, "INBOX")
    session.commit()

    with patch.object(sync_worker, "_list_upstream_folders", side_effect=OSError("no route")):
        assert sync_worker._reconcile_removed_folders("job-x", session, account, "p", None) == []


def test_dot_delimiter_nested_folder_is_not_quarantined(tmp_path):
    """The actual #244-adjacent bug, not just a string-translation check: a
    "."-delimiter Source (self-hosted Dovecot/Courier both commonly use it)
    reports a nested folder as "Parent.Child", while SubFolders Verbatim
    still writes it on disk as real nested directories, Parent/Child.
    Without delimiter normalisation in _list_upstream_folders, this nested
    folder never matches its local counterpart and gets quarantined while
    it still exists upstream — exercised here through the real LIST parsing
    (mocking imap_check.connect_imap, not _list_upstream_folders itself)."""
    session = make_session()
    account, _job = _mk_maildir_account_and_job(session, tmp_path)
    maildir = tmp_path / "maildir"
    _mk_folder(maildir, "INBOX")
    _mk_folder(maildir, "Parent/Child")
    session.commit()

    lines = [
        b'(\\HasNoChildren) "." "INBOX"',
        b'(\\HasNoChildren) "." "Parent.Child"',
    ]

    class _Conn:
        def list(self):
            return "OK", lines

        def logout(self):
            pass

    with patch("mailfallback.services.imap_check.connect_imap", return_value=_Conn()):
        quarantined = sync_worker._reconcile_removed_folders("job-x", session, account, "p", None)

    assert quarantined == []
    assert (maildir / "Parent" / "Child").exists()
    assert not (maildir / "Removed from Source").exists()


def test_a_stop_during_the_reconciliation_cancels_the_retry(tmp_path):
    """The retry gate reads `_killed_signals` BEFORE reconciling, and the
    reconciliation is slow — an IMAP connect + LIST, an os.walk of the whole
    account maildir, N directory renames. A Stop pressed inside that window
    found the first run's already-reaped proc, so `stop_sync_job` terminated
    nothing and returned True (the UI said "stopped") while the gate, long
    since evaluated, launched a full mbsync pass anyway: the machine did the
    exact work the user cancelled, booking bytes into the daily budget the
    whole time."""
    session = make_session()
    _account, job = _mk_maildir_account_and_job(session, tmp_path)
    _mk_folder(tmp_path / "maildir", "INBOX")
    session.commit()

    calls = []

    def fake_popen(cmd, **kw):
        calls.append(cmd)
        return _proc([DEAD_BOX], code=1)

    def stop_mid_reconcile(job_id, db, account, password, access_token):
        sync_worker._killed_signals[job_id] = "SIGTERM"
        return ["push-dixie"]  # folders WERE quarantined: only the stop holds the retry

    try:
        with (
            patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
            PATCH_RC,
            patch.object(sync_worker, "_reconcile_removed_folders", side_effect=stop_mid_reconcile),
        ):
            sync_worker.execute_sync_job(session, job.id)
    finally:
        sync_worker._killed_signals.pop(job.id, None)

    assert len(calls) == 1  # no second mbsync pass
    session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.status != JobStatus.completed
    assert job.signal == "SIGTERM"


def test_run_invocations_re_arms_a_stop_that_lands_before_the_new_proc(tmp_path):
    """The narrower window the gate re-check cannot close: the marker is set
    AFTER the retry is cleared to run but BEFORE the new proc registers, so
    the stop hit the previous, already-reaped proc. Mirrors the `_budget_stops`
    re-arm (review F1) for the user-Stop path, which that fix did not cover."""
    job_id = "job-killed-rearm"
    sync_worker._running_logs[job_id] = []
    stops = []
    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(cmd)
        sync_worker._killed_signals[job_id] = "SIGTERM"
        return _proc(["running"], code=0)

    try:
        with (
            patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
            patch.object(sync_worker, "stop_sync_job", side_effect=lambda jid: stops.append(jid)),
        ):
            sync_worker._run_invocations(job_id, [["mbsync", "a"]], None, None)
    finally:
        sync_worker._running_logs.pop(job_id, None)
        sync_worker._running_procs.pop(job_id, None)
        sync_worker._killed_signals.pop(job_id, None)

    assert cmds  # the proc did start
    assert stops == [job_id]  # ...and the re-arm stopped THAT one
