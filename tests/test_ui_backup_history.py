"""duration_human filter and the backup history partial."""

from datetime import UTC, datetime, timedelta

from mailfallback.routers.ui import _duration_human


def test_duration_seconds():
    start = datetime(2026, 8, 1, 2, 0, 0, tzinfo=UTC)
    assert _duration_human(start, start + timedelta(seconds=42)) == "42s"


def test_duration_minutes_and_seconds():
    start = datetime(2026, 8, 1, 2, 0, 0, tzinfo=UTC)
    assert _duration_human(start, start + timedelta(seconds=311)) == "5m 11s"


def test_duration_hours_and_minutes():
    start = datetime(2026, 8, 1, 2, 0, 0, tzinfo=UTC)
    assert _duration_human(start, start + timedelta(seconds=7860)) == "2h 11m"


def test_duration_without_start_is_a_dash():
    assert _duration_human(None, datetime.now(UTC)) == "—"


def test_duration_still_running_measures_against_now():
    """A running job has no completed_at; the column must still show elapsed."""
    start = datetime.now(UTC) - timedelta(seconds=90)
    assert _duration_human(start, None) == "1m 30s"


def test_naive_datetimes_are_treated_as_utc():
    """SQLite hands back naive datetimes; a naive/aware mix would raise."""
    start = datetime(2026, 8, 1, 2, 0, 0)
    assert _duration_human(start, start + timedelta(seconds=30)) == "30s"


def test_naive_start_against_a_running_job_does_not_raise():
    start = (datetime.now(UTC) - timedelta(seconds=45)).replace(tzinfo=None)
    assert _duration_human(start, None).endswith("s")


def test_clock_skew_does_not_render_a_negative_duration():
    start = datetime(2026, 8, 1, 2, 0, 0, tzinfo=UTC)
    assert _duration_human(start, start - timedelta(seconds=5)) == "—"


def test_filter_is_registered_on_the_jinja_env():
    from mailfallback.routers.ui import templates

    assert templates.env.filters["duration_human"] is _duration_human


class _Policy:
    """Minimal stand-in for BackupPolicy as the pill template sees it."""

    class _Status:
        def __init__(self, value):
            self.value = value

    class _Dest:
        name = "Repo01"

    def __init__(self, status, last_successful_run_at=None):
        self.last_status = self._Status(status)
        self.last_successful_run_at = last_successful_run_at
        self.last_error = None
        self.destination = self._Dest()
        self.schedule = "0 2 * * *"

        class _Preset:
            value = "standard"

        self.retention_preset = _Preset()


def _render_pill(policy) -> str:
    """Render just the status-pill block of the account backup partial."""
    from mailfallback.routers.ui import templates

    source = templates.env.loader.get_source(templates.env, "partials/account_backup.html")[0]
    start = source.index('<div class="flex gap-05 flex-wrap items-center mb-05"')
    end = source.index("</div>", start) + len("</div>")
    return templates.env.from_string(source[start:end]).render(backup_config=policy)


def test_pill_shows_running_even_when_a_past_success_exists():
    """The 2026-08-01 dead-branch bug: current state must win.

    Ordering last_successful_run_at first made the running and failed branches
    unreachable for any mailbox that had ever backed up successfully.
    """
    past_success = datetime.now(UTC) - timedelta(days=1)
    html = _render_pill(_Policy("running", last_successful_run_at=past_success))
    assert "Back-up running" in html
    assert "Last back-up" not in html


def test_pill_shows_failed_even_when_a_past_success_exists():
    past_success = datetime.now(UTC) - timedelta(days=1)
    html = _render_pill(_Policy("failed", last_successful_run_at=past_success))
    assert "Last back-up failed" in html


def test_pill_falls_back_to_the_last_success_when_idle():
    past_success = datetime.now(UTC) - timedelta(hours=3)
    html = _render_pill(_Policy("completed", last_successful_run_at=past_success))
    assert "Last back-up" in html
    assert "failed" not in html


def test_pill_reports_a_policy_that_never_succeeded():
    html = _render_pill(_Policy("idle"))
    assert "no successful back-up yet" in html
