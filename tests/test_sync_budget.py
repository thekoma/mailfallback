# tests/test_sync_budget.py
"""Budget resolution + ETA/backoff math — pure functions, exact asserts.

Jitter determinism: random.uniform is monkeypatched to its lower bound
where exact datetimes are asserted; one unpatched test pins the bounds.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from mailfallback.services import sync_budget
from mailfallback.services.sync_budget import (
    PROVIDER_DAILY_BUDGET_MB,
    compute_progress,
    daily_budget_bytes,
    estimate_eta,
    next_budget_resume,
    next_throttle_resume,
    next_transient_resume,
)

MIB = 1024 * 1024


def _account(budget_mb=None, provider="google"):
    return SimpleNamespace(daily_sync_budget_mb=budget_mb, provider=provider)


# ---------------------------------------------------------------------------
# daily_budget_bytes
# ---------------------------------------------------------------------------


def test_budget_override_none_uses_provider_default():
    assert daily_budget_bytes(_account(None, "google")) == 2000 * MIB
    assert PROVIDER_DAILY_BUDGET_MB["google"] == 2000


def test_budget_override_none_unknown_provider_is_unlimited():
    assert daily_budget_bytes(_account(None, "other")) is None
    assert daily_budget_bytes(_account(None, "microsoft")) is None


def test_budget_override_zero_is_explicit_unlimited():
    assert daily_budget_bytes(_account(0, "google")) is None


def test_budget_override_positive_is_mb_to_bytes():
    assert daily_budget_bytes(_account(512, "google")) == 512 * MIB


# ---------------------------------------------------------------------------
# compute_progress
# ---------------------------------------------------------------------------


def test_progress_none_without_total():
    assert compute_progress(10, None) is None
    assert compute_progress(10, 0) is None
    assert compute_progress(10, -5) is None


def test_progress_fraction():
    assert compute_progress(50, 200) == 25.0
    assert compute_progress(0, 200) == 0.0


def test_progress_clamps_when_mailbox_shrank():
    # done can exceed total when the mailbox shrank between STATUS passes —
    # clamp, never report >100.
    assert compute_progress(300, 200) == 100.0


# ---------------------------------------------------------------------------
# estimate_eta
# ---------------------------------------------------------------------------


def _eta(**kw):
    defaults = {
        "done_msgs": 0,
        "total_msgs": None,
        "done_bytes": 0,
        "bytes_today": 0,
        "budget_bytes": None,
        "run_rate_msgs_per_s": 0.0,
    }
    defaults.update(kw)
    return estimate_eta(**defaults)


def test_eta_no_total_is_all_none():
    out = _eta(done_msgs=10, total_msgs=None, done_bytes=1000)
    assert out == {"seconds": None, "days": None, "label": None}


def test_eta_zero_done_is_all_none():
    # No avg-bytes basis yet.
    out = _eta(done_msgs=0, total_msgs=1000)
    assert out == {"seconds": None, "days": None, "label": None}


def test_eta_nothing_remaining():
    out = _eta(done_msgs=1000, total_msgs=1000, done_bytes=1000)
    assert out == {"seconds": 0, "days": 0.0, "label": "<1h"}


def test_eta_budget_bound_multi_day():
    """The mandated case: 10k/40k done, avg 100 KiB/msg, 2000 MiB budget,
    1.9 GiB already used today. Exact expectation derived here:
    remaining_bytes = 30_000 * 102_400 = 3_072_000_000
    headroom_today  = 2000*MIB - 1900*MIB = 100*MIB = 104_857_600
    full_days       = (3_072_000_000 - 104_857_600) / (2000*MIB)
                    = 2_967_142_400 / 2_097_152_000 ≈ 1.414846
    seconds         = int(full_days * 86400) = 122_242  (budget-days only)
    label           = "≈ 2d"  (ceil of 1.41 budget-days — wall-clock honest:
                      the tail also waits out today's headroom + midnight)
    """
    out = _eta(
        done_msgs=10_000,
        total_msgs=40_000,
        done_bytes=10_000 * 100 * 1024,
        bytes_today=1900 * MIB,
        budget_bytes=2000 * MIB,
        run_rate_msgs_per_s=50.0,
    )
    full_days = (3_072_000_000 - 100 * MIB) / (2000 * MIB)
    assert out["days"] == full_days
    assert out["seconds"] == int(full_days * 86400) == 122_242
    assert out["label"] == "≈ 2d"


def test_eta_within_headroom_uses_run_rate():
    # remaining 100 msgs * 1024 B = 102_400 B <= headroom -> rate-based.
    out = _eta(
        done_msgs=1000,
        total_msgs=1100,
        done_bytes=1000 * 1024,
        bytes_today=0,
        budget_bytes=2000 * MIB,
        run_rate_msgs_per_s=10.0,
    )
    assert out["seconds"] == 10
    assert out["days"] == 10 / 86400
    assert out["label"] == "<1h"


def test_eta_within_headroom_hours_label():
    # 100 msgs at 0.01 msg/s -> 10_000 s ≈ 2.78 h -> "≈ 3h" (round half up).
    out = _eta(
        done_msgs=1000,
        total_msgs=1100,
        done_bytes=1000 * 1024,
        bytes_today=0,
        budget_bytes=2000 * MIB,
        run_rate_msgs_per_s=0.01,
    )
    assert out["seconds"] == 10_000
    assert out["label"] == "≈ 3h"


def test_eta_no_budget_rate_based():
    # 7200 msgs at 2/s -> exactly 3600 s -> hours branch boundary -> "≈ 1h".
    out = _eta(
        done_msgs=100,
        total_msgs=7300,
        done_bytes=100 * 1024,
        budget_bytes=None,
        run_rate_msgs_per_s=2.0,
    )
    assert out["seconds"] == 3600
    assert out["label"] == "≈ 1h"


def test_eta_no_budget_no_rate_is_none():
    out = _eta(
        done_msgs=100,
        total_msgs=200,
        done_bytes=100 * 1024,
        budget_bytes=None,
        run_rate_msgs_per_s=0.0,
    )
    assert out == {"seconds": None, "days": None, "label": None}


def test_eta_within_headroom_no_rate_is_none():
    out = _eta(
        done_msgs=100,
        total_msgs=200,
        done_bytes=100 * 1024,
        bytes_today=0,
        budget_bytes=2000 * MIB,
        run_rate_msgs_per_s=0.0,
    )
    assert out == {"seconds": None, "days": None, "label": None}


# ---------------------------------------------------------------------------
# resume helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 6, 13, 15, 30, 0, tzinfo=UTC)


def _zero_jitter(monkeypatch):
    monkeypatch.setattr(sync_budget.random, "uniform", lambda a, b: 0.0)


def test_next_budget_resume_is_next_utc_midnight(monkeypatch):
    _zero_jitter(monkeypatch)
    assert next_budget_resume(NOW) == datetime(2026, 6, 14, 0, 0, 0, tzinfo=UTC)


def test_next_budget_resume_jitter_bounds():
    # Unpatched: strictly after now, at most midnight + 30 min.
    midnight = datetime(2026, 6, 14, 0, 0, 0, tzinfo=UTC)
    for _ in range(20):
        resume = next_budget_resume(NOW)
        assert NOW < resume <= midnight + timedelta(minutes=30)
        assert resume >= midnight


def test_next_throttle_resume_doubles_and_caps(monkeypatch):
    _zero_jitter(monkeypatch)
    assert next_throttle_resume(NOW, 1) == NOW + timedelta(hours=4)
    assert next_throttle_resume(NOW, 2) == NOW + timedelta(hours=8)
    assert next_throttle_resume(NOW, 3) == NOW + timedelta(hours=16)
    assert next_throttle_resume(NOW, 4) == NOW + timedelta(hours=24)  # cap
    assert next_throttle_resume(NOW, 10) == NOW + timedelta(hours=24)
    # attempt < 1 clamps to 1.
    assert next_throttle_resume(NOW, 0) == NOW + timedelta(hours=4)


def test_next_transient_resume_doubles_and_caps(monkeypatch):
    _zero_jitter(monkeypatch)
    assert next_transient_resume(NOW, 1) == NOW + timedelta(minutes=2)
    assert next_transient_resume(NOW, 2) == NOW + timedelta(minutes=4)
    assert next_transient_resume(NOW, 4) == NOW + timedelta(minutes=16)
    assert next_transient_resume(NOW, 5) == NOW + timedelta(minutes=30)  # 32 -> cap
    assert next_transient_resume(NOW, 0) == NOW + timedelta(minutes=2)


def test_eta_budget_bound_small_overshoot_says_one_day():
    """The N >= 1 floor binds: even a small tail beyond today's headroom
    (full_days ≈ 0.3 -> ceil 1) reads "≈ 1d" — it waits for tomorrow's
    budget, never "<1h". Derivation: done 500 msgs / 500 MiB (avg 1 MiB),
    total 1000 -> remaining 500 MiB; budget 1000 MiB, used 800 MiB ->
    headroom 200 MiB; tail 300 MiB -> full_days 0.3."""
    out = _eta(
        done_msgs=500,
        total_msgs=1000,
        done_bytes=500 * MIB,
        bytes_today=800 * MIB,
        budget_bytes=1000 * MIB,
        run_rate_msgs_per_s=50.0,
    )
    assert out["days"] == 0.3
    assert out["seconds"] == int(0.3 * 86400)
    assert out["label"] == "≈ 1d"


def test_backoff_huge_attempt_does_not_overflow(monkeypatch):
    """Review regression: 2**(attempt-1) evaluated BEFORE min() and
    overflowed timedelta at attempt 34 (throttle) / 41 (transient) — and a
    day-long outage really produces attempt counts in the 40s. The exponent
    is now capped before exponentiation; the result stays at the cap."""
    _zero_jitter(monkeypatch)
    assert next_throttle_resume(NOW, 50) == NOW + timedelta(hours=24)
    assert next_transient_resume(NOW, 50) == NOW + timedelta(minutes=30)
    assert next_throttle_resume(NOW, 10_000) == NOW + timedelta(hours=24)
