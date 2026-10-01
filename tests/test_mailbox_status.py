# tests/test_mailbox_status.py
"""The unified per-mailbox status resolver (docs/designs/mailbox-status-resolver.md)."""

import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from mailfallback.constants import TOKEN_REFRESH_FAILED
from mailfallback.models import Account, AuthType, JobStatus, SyncJob, SyncState
from mailfallback.services import sync_worker
from mailfallback.services.mailbox_status import (
    PAUSE_TOOLTIPS,
    STALE_AFTER,
    MailboxState,
    NextAction,
    Tone,
    latest_finished_jobs_by_account,
    resolve_job_outcome,
    resolve_mailbox_status,
    resolve_many,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _acct(db_session, default_store, **kw) -> Account:
    kw.setdefault("created_at", NOW - timedelta(days=1))
    kw.setdefault("sync_schedule", "0 * * * *")
    account = Account(
        name=kw.pop("name", "Box"),
        provider=kw.pop("provider", "other"),
        imap_host="imap.example.com",
        maildir_path=f"/data/mailboxes/{uuid.uuid4()}",
        store_id=default_store.id,
        **kw,
    )
    db_session.add(account)
    db_session.commit()
    return account


def _done(**kw):
    """An account whose initial sync completed and last synced recently."""
    kw.setdefault("initial_sync_completed_at", NOW - timedelta(days=30))
    kw.setdefault("last_sync_at", NOW - timedelta(minutes=5))
    return kw


def _job(db_session, account, **kw) -> SyncJob:
    kw.setdefault("status", JobStatus.failed)
    kw.setdefault("started_at", NOW - timedelta(minutes=10))
    kw.setdefault("completed_at", NOW - timedelta(minutes=9))
    job = SyncJob(account_id=account.id, **kw)
    db_session.add(job)
    db_session.commit()
    return job


def _resolve(account, **kw):
    kw.setdefault("now", NOW)
    return resolve_mailbox_status(account, **kw)


def test_migrating_beats_everything(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        migrating=True,
        suspended=True,
        sync_state=SyncState.error,
        auth_type=AuthType.oauth2,
        credentials=None,
    )
    s = _resolve(a)
    assert s.state == MailboxState.migrating
    assert s.tone == Tone.active
    assert s.badge == "stamp-active" and s.icon == "loader"
    assert not s.needs_attention and s.action is None


def test_suspended_beats_error_and_is_not_attention(db_session, default_store):
    a = _acct(db_session, default_store, suspended=True, sync_state=SyncState.error, **_done())
    s = _resolve(a)
    assert s.state == MailboxState.suspended
    assert s.tone == Tone.muted
    assert s.badge == "stamp-muted" and s.icon == "pause-circle"
    assert not s.needs_attention and s.action is None


def test_needs_reauth_is_sign_in_needed_with_reconnect(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        provider="google",
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.needs_reauth,
        **_done(),
    )
    s = _resolve(a)
    assert s.state == MailboxState.sign_in_needed
    assert s.tone == Tone.attention
    assert s.action == NextAction.reconnect
    assert s.needs_attention
    assert s.badge == "stamp-attention" and s.icon == "key-round"
    assert not s.signed_in
    assert s.detail == "Google sign-in expired. Reconnect to resume syncing."


def test_oauth_without_credentials_is_sign_in_needed(db_session, default_store):
    a = _acct(db_session, default_store, provider="microsoft", auth_type=AuthType.oauth2)
    s = _resolve(a)
    assert s.state == MailboxState.sign_in_needed
    assert s.action == NextAction.reconnect
    assert s.badge == "stamp-attention"
    # Never synced: the consent flow never finished, nothing "expired".
    assert s.detail == "Microsoft sign-in not completed. Connect to start syncing."


def test_sign_in_never_completed_copy_per_viewer(db_session, default_store):
    from mailfallback.services.mailbox_status import sign_in_message

    a = _acct(db_session, default_store, provider="google", auth_type=AuthType.oauth2)
    assert sign_in_message(a, can_modify=True) == (
        "Google sign-in not completed. Connect to start syncing."
    )
    assert sign_in_message(a, can_modify=False) == (
        "Sign-in not completed. Ask the mailbox owner or an admin to connect it."
    )
    generic = _acct(db_session, default_store, provider="other", auth_type=AuthType.oauth2)
    assert sign_in_message(generic, can_modify=True) == (
        "Sign-in not completed. Connect to start syncing."
    )


def test_credentials_lost_after_syncing_reads_expired(db_session, default_store):
    """No credentials but it synced before: that sign-in did expire."""
    from mailfallback.services.mailbox_status import sign_in_message

    a = _acct(
        db_session,
        default_store,
        provider="google",
        auth_type=AuthType.oauth2,
        last_sync_at=datetime.now(UTC) - timedelta(days=1),
    )
    assert _resolve(a).detail == "Google sign-in expired. Reconnect to resume syncing."
    assert sign_in_message(a, can_modify=False) == (
        "Sign-in expired. Ask the mailbox owner or an admin to reconnect it."
    )


def test_token_refresh_failure_error_is_sign_in_needed(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.error,
        last_error=TOKEN_REFRESH_FAILED,
        **_done(),
    )
    s = _resolve(a)
    assert s.state == MailboxState.sign_in_needed
    assert s.tone == Tone.attention
    assert s.action == NextAction.reconnect
    assert s.needs_attention
    assert not s.signed_in


def test_plain_error_is_red_with_retry(db_session, default_store):
    a = _acct(db_session, default_store, sync_state=SyncState.error, last_error="boom", **_done())
    s = _resolve(a)
    assert s.state == MailboxState.error
    assert s.tone == Tone.error
    assert s.action == NextAction.retry
    assert s.needs_attention
    assert s.badge == "stamp-error" and s.icon == "alert-circle"
    # Unclassified: MFB's own sentence, never the raw last_error.
    assert s.detail == "The last sync failed."
    assert "boom" not in s.detail
    assert s.signed_in


def test_app_password_credential_error_suggests_update_password(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        auth_type=AuthType.app_password,
        sync_state=SyncState.error,
        last_error="AUTHENTICATIONFAILED Invalid credentials",
        **_done(),
    )
    s = _resolve(a)
    assert s.state == MailboxState.error
    assert s.action == NextAction.update_password
    # an OAuth account with the same text is a plain retry
    b = _acct(
        db_session,
        default_store,
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.error,
        last_error="AUTHENTICATIONFAILED Invalid credentials",
        **_done(),
    )
    assert _resolve(b).action == NextAction.retry


def test_pause_beats_error(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        sync_state=SyncState.error,
        last_error="OVERQUOTA",
        pause_reason="throttle",
        sync_paused_until=NOW + timedelta(hours=2),
        **_done(),
    )
    s = _resolve(a)
    assert s.state == MailboxState.paused
    assert s.tone == Tone.active
    assert not s.needs_attention
    assert s.badge == "stamp-active" and s.icon == "pause-circle"
    assert s.resumes_at == NOW + timedelta(hours=2)


def test_paused_with_past_until_is_still_paused(db_session, default_store):
    until = NOW - timedelta(hours=3)
    a = _acct(db_session, default_store, pause_reason="budget", sync_paused_until=until, **_done())
    s = _resolve(a)
    assert s.state == MailboxState.paused
    # A pause that should already have lifted has no resume time to report.
    assert s.resumes_at is None


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("budget", PAUSE_TOOLTIPS["budget"]),
        ("throttle", PAUSE_TOOLTIPS["throttle"]),
        ("transient", PAUSE_TOOLTIPS["transient"]),
        ("interrupted", PAUSE_TOOLTIPS["interrupted"]),
        ("something-new", "Paused for now. It resumes on its own."),
    ],
)
def test_pause_reasons_have_tooltips(db_session, default_store, reason, expected):
    a = _acct(db_session, default_store, pause_reason=reason, **_done())
    s = _resolve(a)
    assert s.state == MailboxState.paused
    assert s.tone == Tone.active
    assert s.detail == expected


def test_user_stop_is_muted_stopped_not_error(db_session, default_store):
    a = _acct(db_session, default_store, sync_state=SyncState.error, last_error="x", **_done())
    job = _job(db_session, a, signal="SIGTERM", log="x")
    s = _resolve(a, last_job=job)
    assert s.state == MailboxState.stopped
    assert s.tone == Tone.muted
    assert not s.needs_attention and s.action is None
    assert s.badge == "stamp-muted" and s.icon == "circle-slash"
    assert s.detail == "The last sync was stopped. The next scheduled sync runs normally."
    a.sync_schedule = None
    assert _resolve(a, last_job=job).detail == (
        "The last sync was stopped. Start a sync when you're ready."
    )


def test_running_before_initial_complete_is_first_sync_with_spin(db_session, default_store):
    a = _acct(db_session, default_store, sync_state=SyncState.syncing)
    s = _resolve(a)
    assert s.state == MailboxState.first_sync
    assert s.tone == Tone.active
    assert s.badge == "stamp-active" and s.icon == "loader" and s.spin
    assert not s.needs_attention


def test_running_after_initial_complete_is_syncing(db_session, default_store):
    a = _acct(db_session, default_store, sync_state=SyncState.syncing, **_done())
    s = _resolve(a)
    assert s.state == MailboxState.syncing
    assert s.badge == "stamp-active" and s.icon == "loader" and not s.spin
    assert s.label == "Syncing"


def test_idle_initial_started_is_initial_sync_not_attention(db_session, default_store):
    a = _acct(db_session, default_store, initial_sync_total_messages=1000)
    s = _resolve(a)
    assert s.state == MailboxState.initial_sync
    assert s.tone == Tone.active
    assert s.badge == "stamp-active" and s.icon == "download"
    assert not s.needs_attention
    assert s.detail == "First full sync incomplete"

    b = _acct(db_session, default_store)
    job = _job(db_session, b, status=JobStatus.completed)
    assert _resolve(b, last_job=job).state == MailboxState.initial_sync


def test_initial_sync_without_progress_for_7_days_is_stalled_attention(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        created_at=NOW - timedelta(days=30),
        initial_sync_total_messages=5000,
    )
    job = _job(
        db_session,
        a,
        status=JobStatus.completed,
        started_at=NOW - timedelta(days=10, minutes=5),
        completed_at=NOW - timedelta(days=10),
    )
    s = _resolve(a, last_job=job)
    assert s.state == MailboxState.initial_stalled
    assert s.tone == Tone.attention
    assert s.label == "Initial sync stalled"
    assert s.needs_attention
    assert s.action == NextAction.sync_now
    assert s.badge == "stamp-attention" and s.icon == "alert-triangle"
    assert s.detail == "The initial sync has not progressed for over 7 days."


def test_never_started_is_waiting(db_session, default_store):
    a = _acct(db_session, default_store)
    s = _resolve(a)
    assert s.state == MailboxState.waiting
    assert s.tone == Tone.muted
    assert s.badge == "stamp-muted" and s.icon == "clock"
    assert not s.needs_attention and s.action is None


def test_stale_after_seven_days_needs_attention(db_session, default_store):
    a = _acct(db_session, default_store, **_done(last_sync_at=NOW - timedelta(days=10)))
    s = _resolve(a)
    assert s.state == MailboxState.stale
    assert s.tone == Tone.attention
    assert s.label == "Out of date"
    assert s.needs_attention
    assert s.action == NextAction.sync_now
    assert s.badge == "stamp-attention" and s.icon == "clock"
    assert s.detail == "Last sync was 10 days ago."
    # six days is still current
    a.last_sync_at = NOW - timedelta(days=6)
    assert _resolve(a).state == MailboxState.current


def test_current_is_ok_idle(db_session, default_store):
    a = _acct(db_session, default_store, **_done())
    s = _resolve(a)
    assert s.state == MailboxState.current
    assert s.tone == Tone.ok
    assert s.label == "Up to date"
    assert s.badge == "stamp-ok" and s.icon == "check-circle"
    assert not s.needs_attention and s.action is None
    assert s.signed_in
    assert s.last_success_at == NOW - timedelta(minutes=5)


def test_naive_datetimes_are_treated_as_utc(db_session, default_store):
    naive_now = NOW.replace(tzinfo=None)
    a = _acct(
        db_session,
        default_store,
        created_at=naive_now - timedelta(days=40),
        initial_sync_completed_at=naive_now - timedelta(days=30),
        last_sync_at=naive_now - timedelta(days=10),
        pause_reason="budget",
        sync_paused_until=naive_now + timedelta(hours=1),
    )
    s = resolve_mailbox_status(a, now=naive_now)
    assert s.state == MailboxState.stale
    assert s.last_success_at.tzinfo is not None
    assert s.resumes_at.tzinfo is not None


@pytest.mark.parametrize(
    "sync_state, paused, initial_done, last_sync, job_kind",
    list(
        itertools.product(
            list(SyncState),
            [False, True],
            [False, True],
            [None, "recent", "old"],
            [None, "stopped", "failed"],
        )
    ),
)
def test_resolver_is_total(
    db_session, default_store, sync_state, paused, initial_done, last_sync, job_kind
):
    kw = {"sync_state": sync_state}
    if paused:
        kw.update(pause_reason="budget", sync_paused_until=NOW + timedelta(hours=1))
    if initial_done:
        kw["initial_sync_completed_at"] = NOW - timedelta(days=30)
    if last_sync == "recent":
        kw["last_sync_at"] = NOW - timedelta(minutes=5)
    elif last_sync == "old":
        kw["last_sync_at"] = NOW - timedelta(days=10)
    a = _acct(db_session, default_store, **kw)
    job = None
    if job_kind == "stopped":
        job = _job(db_session, a, signal="SIGTERM")
    elif job_kind == "failed":
        job = _job(db_session, a, failure_kind="error")
    s = _resolve(a, last_job=job)
    assert s.state in set(MailboxState)
    assert s.tone in set(Tone)
    assert s.needs_attention == (
        s.state
        in (
            MailboxState.sign_in_needed,
            MailboxState.error,
            MailboxState.stale,
            MailboxState.initial_stalled,
        )
    )
    # Only error is red.
    assert (s.tone == Tone.error) == (s.state == MailboxState.error)


def test_latest_finished_jobs_by_account_ignores_pending_and_running_and_handles_empty_input(
    db_session, default_store
):
    assert latest_finished_jobs_by_account(db_session, []) == {}
    a = _acct(db_session, default_store)
    b = _acct(db_session, default_store)
    older = _job(
        db_session,
        a,
        status=JobStatus.completed,
        completed_at=NOW - timedelta(hours=2),
    )
    newest_finished = _job(
        db_session, a, status=JobStatus.failed, completed_at=NOW - timedelta(hours=1)
    )
    _job(db_session, a, status=JobStatus.pending, started_at=None, completed_at=None)
    _job(db_session, a, status=JobStatus.running, completed_at=None)
    _job(db_session, b, status=JobStatus.pending, started_at=None, completed_at=None)

    out = latest_finished_jobs_by_account(db_session, [a.id, b.id])
    assert out[a.id].id == newest_finished.id
    assert out[a.id].id != older.id
    assert b.id not in out


_JOB_ROWS = [
    ({"status": JobStatus.completed}, Tone.ok, "synced", "stamp-ok", "check-circle", False),
    ({"status": JobStatus.running}, Tone.active, "syncing", "stamp-active", "loader", True),
    ({"status": JobStatus.pending}, Tone.muted, "queued", "stamp-muted", "clock", False),
    ({"status": JobStatus.cancelled}, Tone.muted, "queued", "stamp-muted", "clock", False),
    (
        {"status": JobStatus.failed, "signal": "SIGTERM"},
        Tone.muted,
        "stopped",
        "stamp-muted",
        "circle-slash",
        False,
    ),
    (
        {"status": JobStatus.failed, "log": "Sync blocked: account is suspended"},
        Tone.muted,
        "skipped",
        "stamp-muted",
        "circle-slash",
        False,
    ),
    (
        {"status": JobStatus.failed, "log": TOKEN_REFRESH_FAILED},
        Tone.attention,
        "sign-in failed",
        "stamp-attention",
        "key-round",
        False,
    ),
    (
        {"status": JobStatus.failed, "failure_kind": "budget_paused"},
        Tone.active,
        "paused (daily limit)",
        "stamp-active",
        "pause-circle",
        False,
    ),
    (
        {"status": JobStatus.failed, "failure_kind": "throttled"},
        Tone.active,
        "paused (provider throttling)",
        "stamp-active",
        "pause-circle",
        False,
    ),
    (
        {"status": JobStatus.failed, "failure_kind": "transient"},
        Tone.active,
        "paused (temporary error)",
        "stamp-active",
        "pause-circle",
        False,
    ),
    (
        {"status": JobStatus.failed, "failure_kind": "interrupted"},
        Tone.active,
        "interrupted",
        "stamp-active",
        "pause-circle",
        False,
    ),
    (
        {"status": JobStatus.failed, "failure_kind": "error"},
        Tone.error,
        "failed",
        "stamp-error",
        "x-circle",
        False,
    ),
    ({"status": JobStatus.failed}, Tone.error, "failed", "stamp-error", "x-circle", False),
    (
        {"status": JobStatus.failed, "failure_kind": "brand-new-kind"},
        Tone.error,
        "failed",
        "stamp-error",
        "x-circle",
        False,
    ),
]


@pytest.mark.parametrize("fields, tone, label, badge, icon, spin", _JOB_ROWS)
def test_job_outcome_table(fields, tone, label, badge, icon, spin):
    o = resolve_job_outcome(SyncJob(account_id="a", **fields))
    assert (o.tone, o.label, o.badge, o.icon, o.spin) == (tone, label, badge, icon, spin)


# --- additions ---------------------------------------------------------------


def test_stopped_requires_finished_failed_job_with_signal(db_session, default_store):
    a = _acct(db_session, default_store, sync_state=SyncState.error, last_error="x", **_done())
    # no job → plain error
    assert _resolve(a).state == MailboxState.error
    # failed without a signal → error
    assert _resolve(a, last_job=_job(db_session, a)).state == MailboxState.error
    # signal but classified failure → error
    classified = _job(db_session, a, signal="SIGTERM", log="x", failure_kind="error")
    assert _resolve(a, last_job=classified).state == MailboxState.error
    # completed job carrying a signal is not a stop
    completed = _job(db_session, a, status=JobStatus.completed, signal="SIGTERM", log="x")
    assert _resolve(a, last_job=completed).state == MailboxState.error
    # a stop that happened long ago falls to out-of-date, never red
    a.last_sync_at = NOW - timedelta(days=10)
    stopped = _job(db_session, a, signal="SIGTERM", log="x")
    s = _resolve(a, last_job=stopped)
    assert s.state == MailboxState.stale
    assert s.tone == Tone.attention


def test_long_paused_account_becomes_stale_attention(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        pause_reason="budget",
        **_done(last_sync_at=NOW - timedelta(days=10)),
    )
    s = _resolve(a)
    assert s.state == MailboxState.stale
    assert s.needs_attention


def test_never_synced_after_7_days_is_initial_stalled_never_synced(db_session, default_store):
    a = _acct(db_session, default_store, created_at=NOW - timedelta(days=9))
    s = _resolve(a)
    assert s.state == MailboxState.initial_stalled
    assert s.label == "Never synced"
    assert s.detail == "This mailbox has never synced (added 9 days ago)."
    assert s.action == NextAction.sync_now


def test_waiting_within_7_days_is_muted(db_session, default_store):
    a = _acct(db_session, default_store, created_at=NOW - timedelta(days=6))
    s = _resolve(a)
    assert s.state == MailboxState.waiting
    assert s.tone == Tone.muted


def test_live_progress_counts_as_started(db_session, default_store):
    a = _acct(db_session, default_store)
    assert _resolve(a).state == MailboxState.waiting
    assert _resolve(a, live_progress=True).state == MailboxState.initial_sync

    # resolve_many feeds the sampler's progress in
    sync_worker._live_progress["mbs-job"] = {"account_id": a.id, "pct": 12.0}
    try:
        statuses = resolve_many(db_session, [a], now=NOW)
    finally:
        sync_worker._live_progress.pop("mbs-job", None)
    assert statuses[a.id].state == MailboxState.initial_sync


def test_blocked_job_does_not_count_as_started_or_reset_stall_clock(db_session, default_store):
    a = _acct(db_session, default_store, created_at=NOW - timedelta(days=9))
    blocked = _job(
        db_session,
        a,
        started_at=None,
        completed_at=NOW - timedelta(minutes=1),
        log="Sync blocked: account not authenticated",
    )
    s = _resolve(a, last_job=blocked)
    assert s.state == MailboxState.initial_stalled
    assert s.label == "Never synced"


def test_manual_only_mailbox_is_never_out_of_date(db_session, default_store):
    for schedule in (None, "", "  "):
        a = _acct(db_session, default_store, **_done(last_sync_at=NOW - timedelta(days=100)))
        a.sync_schedule = schedule
        assert _resolve(a).state == MailboxState.current
        b = _acct(db_session, default_store, created_at=NOW - timedelta(days=50))
        b.sync_schedule = schedule
        assert _resolve(b).state == MailboxState.waiting


def test_monthly_schedule_uses_twice_the_interval(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        sync_schedule="0 0 1 * *",
        **_done(last_sync_at=NOW - timedelta(days=30)),
    )
    assert _resolve(a).state == MailboxState.current
    a.last_sync_at = NOW - timedelta(days=70)
    s = _resolve(a)
    assert s.state == MailboxState.stale
    assert s.detail == "Last sync was 70 days ago."
    # initial_stalled detail names the dynamic threshold
    b = _acct(
        db_session,
        default_store,
        sync_schedule="0 0 1 * *",
        created_at=NOW - timedelta(days=100),
        initial_sync_total_messages=10,
    )
    sb = _resolve(b)
    assert sb.state == MailboxState.initial_stalled
    assert "for over 7 days" not in sb.detail
    assert sb.detail.startswith("The initial sync has not progressed for over ")


def test_invalid_cron_falls_back_to_seven_days(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        sync_schedule="not a cron",
        **_done(last_sync_at=NOW - timedelta(days=8)),
    )
    assert _resolve(a).state == MailboxState.stale
    a.last_sync_at = NOW - STALE_AFTER + timedelta(hours=1)
    assert _resolve(a).state == MailboxState.current


def test_paused_and_stale_has_no_action_and_resumes_at(db_session, default_store):
    until = NOW + timedelta(hours=5)
    a = _acct(
        db_session,
        default_store,
        pause_reason="budget",
        sync_paused_until=until,
        **_done(last_sync_at=NOW - timedelta(days=10)),
    )
    s = _resolve(a)
    assert s.state == MailboxState.stale
    assert s.action is None
    assert s.resumes_at == until
    # a future pause date without a reason also withholds sync_now
    a.pause_reason = None
    s2 = _resolve(a)
    assert s2.state == MailboxState.stale
    assert s2.action is None
    assert s2.resumes_at is None


def test_non_terminal_token_refresh_failure_detail(db_session, default_store):
    a = _acct(
        db_session,
        default_store,
        provider="google",
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.error,
        last_error=TOKEN_REFRESH_FAILED,
        **_done(),
    )
    assert _resolve(a).detail == (
        "Couldn't refresh the sign-in. It retries on the next sync; "
        "reconnect if this keeps happening."
    )
    b = _acct(
        db_session,
        default_store,
        provider="yahoo",
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.needs_reauth,
        **_done(),
    )
    assert _resolve(b).detail == "Sign-in expired. Reconnect to resume syncing."


def test_signed_in_is_false_for_suspended_oauth_without_credentials(db_session, default_store):
    a = _acct(db_session, default_store, auth_type=AuthType.oauth2, suspended=True)
    s = _resolve(a)
    assert s.state == MailboxState.suspended
    assert not s.signed_in


def test_job_outcome_blocked_is_skipped_and_token_failure_is_amber():
    for log in (
        "Sync blocked: account is suspended",
        "Sync blocked: account not authenticated",
        "Sync blocked: account migration in progress",
        "Sync blocked: user migration in progress",
    ):
        o = resolve_job_outcome(SyncJob(account_id="a", status=JobStatus.failed, log=log))
        assert o.label == "skipped" and o.tone == Tone.muted
    # the host re-validation failure is a real failure
    o = resolve_job_outcome(
        SyncJob(
            account_id="a",
            status=JobStatus.failed,
            log="Sync blocked: host resolves to an internal address",
        )
    )
    assert o.label == "failed" and o.tone == Tone.error
    o = resolve_job_outcome(
        SyncJob(account_id="a", status=JobStatus.failed, log=TOKEN_REFRESH_FAILED)
    )
    assert o.tone == Tone.attention and o.badge == "stamp-attention"


# --- review round 1 ----------------------------------------------------------


def test_owner_migrating_is_migrating(db_session, default_store):
    from mailfallback.models import User, UserRole, account_owners

    a = _acct(db_session, default_store, sync_state=SyncState.error, last_error="x", **_done())
    assert _resolve(a, owner_migrating=True).state == MailboxState.migrating

    other = _acct(db_session, default_store, **_done())
    owner = User(username="mover", password_hash="x", role=UserRole.user, store_id=default_store.id)
    bystander = User(
        username="stays", password_hash="x", role=UserRole.user, store_id=default_store.id
    )
    db_session.add_all([owner, bystander])
    db_session.flush()
    db_session.execute(account_owners.insert().values(account_id=a.id, user_id=owner.id))
    db_session.execute(account_owners.insert().values(account_id=a.id, user_id=bystander.id))
    db_session.execute(account_owners.insert().values(account_id=other.id, user_id=bystander.id))
    owner.migrating = True
    db_session.commit()

    statuses = resolve_many(db_session, [a, other], now=NOW)
    assert statuses[a.id].state == MailboxState.migrating
    assert statuses[a.id].tone == Tone.active
    assert statuses[other.id].state == MailboxState.current


def test_expired_pause_without_reason_withholds_action(db_session, default_store):
    """`action` mirrors the agent trigger, which refuses whenever
    sync_paused_until is set — expired or not."""
    stale = _acct(
        db_session,
        default_store,
        sync_paused_until=NOW - timedelta(hours=1),
        **_done(last_sync_at=NOW - timedelta(days=10)),
    )
    s = _resolve(stale)
    assert s.state == MailboxState.stale
    assert s.action is None
    assert s.resumes_at is None

    broken = _acct(
        db_session,
        default_store,
        sync_state=SyncState.error,
        last_error="boom",
        sync_paused_until=NOW - timedelta(hours=1),
        **_done(),
    )
    s = _resolve(broken)
    assert s.state == MailboxState.error
    assert s.tone == Tone.error
    assert s.action is None  # retry would be refused by the agent trigger


def test_stopped_survives_newer_blocked_job(db_session, default_store):
    a = _acct(db_session, default_store, sync_state=SyncState.error, last_error="x", **_done())
    stopped = _job(
        db_session,
        a,
        signal="SIGTERM",
        log="x",
        started_at=NOW - timedelta(minutes=30),
        completed_at=NOW - timedelta(minutes=29),
    )
    _job(
        db_session,
        a,
        started_at=None,
        completed_at=NOW - timedelta(minutes=1),
        log="Sync blocked: account migration in progress",
    )
    jobs = latest_finished_jobs_by_account(db_session, [a.id])
    assert jobs[a.id].id == stopped.id
    assert resolve_many(db_session, [a], now=NOW)[a.id].state == MailboxState.stopped


# --- review round 2 ----------------------------------------------------------


def test_error_after_unstarted_host_guard_failure_is_not_stopped(db_session, default_store):
    """The host re-validation guard fails a job before started_at and sets
    sync_state=error + last_error. That job is not last_job, so the older
    user stop is — but the account's error is the guard's, not the stop's."""
    host_log = "Sync blocked: host resolves to an internal address"
    a = _acct(
        db_session,
        default_store,
        sync_state=SyncState.error,
        last_error="stopped",
        **_done(),
    )
    _job(
        db_session,
        a,
        signal="SIGTERM",
        log="stopped",
        started_at=NOW - timedelta(minutes=30),
        completed_at=NOW - timedelta(minutes=29),
    )
    assert resolve_many(db_session, [a], now=NOW)[a.id].state == MailboxState.stopped

    _job(db_session, a, started_at=None, completed_at=NOW - timedelta(minutes=1), log=host_log)
    a.last_error = host_log
    db_session.commit()
    s = resolve_many(db_session, [a], now=NOW)[a.id]
    assert s.state == MailboxState.error
    assert s.tone == Tone.error
    assert s.action == NextAction.retry


def test_runtime_cap_kill_resolves_red_not_stopped(db_session, default_store):
    """The worker marks a cap kill failure_kind="error" (signal still set)."""
    cap = "Sync exceeded the 21600s runtime cap"
    a = _acct(db_session, default_store, sync_state=SyncState.error, last_error=cap, **_done())
    job = _job(db_session, a, signal="SIGTERM", failure_kind="error", log=f"...\n{cap}")
    s = _resolve(a, last_job=job)
    assert s.state == MailboxState.error and s.tone == Tone.error
    o = resolve_job_outcome(job)
    assert (o.label, o.badge) == ("failed", "stamp-error")
