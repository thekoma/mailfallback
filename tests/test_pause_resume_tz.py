from datetime import UTC, datetime, timedelta


def test_time_until_is_relative_and_tz_safe():
    """Pause resume time must render as a RELATIVE delta, not a wall-clock
    time formatted in UTC (which read 2h off for a UTC+2 user — '19:05' shown
    when it was 21:05). Relative time is timezone-independent by construction."""
    from mailfallback.routers.ui import _time_until

    assert _time_until(None) is None
    # ~7 minutes out
    assert _time_until(datetime.now(UTC) + timedelta(minutes=7)) == "in 7m"
    # ~3 hours out
    assert _time_until(datetime.now(UTC) + timedelta(hours=3)) == "in 3h"
    # already due / past → not a negative clock time
    assert _time_until(datetime.now(UTC) - timedelta(minutes=5)) == "shortly"
    # a naive timestamp is treated as UTC — no local-offset skew (the bug)
    naive_future = (datetime.now(UTC) + timedelta(hours=2)).replace(tzinfo=None)
    assert _time_until(naive_future) == "in 2h"


def test_account_live_status_resume_is_relative(db_session, default_store):
    """account_live_status exposes a relative resume label (resume_rel),
    never a UTC wall-clock string."""
    from mailfallback.models import Account, AuthType
    from mailfallback.routers.ui import account_live_status

    account = Account(
        name="tzacct",
        store=default_store,
        maildir_path="/data/mailboxes/tzacct",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        sync_paused_until=datetime.now(UTC) + timedelta(minutes=25),
        pause_reason="transient",
    )
    db_session.add(account)
    db_session.commit()

    ls = account_live_status(account)

    assert "resume_hhmm" not in ls
    assert ls["resume_rel"] == "in 25m"
