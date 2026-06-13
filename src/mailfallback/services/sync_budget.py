"""Budget resolution + ETA/backoff math for throttle-aware first syncs.

Pure stdlib (datetime/random/math) — consumed by the sampler thread, the
worker, the scheduler and the progress UI (sync-budget spec). All
datetimes are aware UTC; jitter spreads resumes so a fleet of paused
accounts does not stampede the provider at the same second.
"""

import math
import random
from datetime import UTC, datetime, time, timedelta

# Prudent under Gmail's ~2.5 GB/day observed download ceiling (the
# production OVERQUOTA evidence) — leaves headroom for webmail/clients.
PROVIDER_DAILY_BUDGET_MB: dict[str, int] = {"google": 2000}

_MIB = 1024 * 1024


def daily_budget_bytes(account) -> int | None:
    """Resolve the account's daily sync budget in bytes (None = unlimited).

    Duck-typed on ``daily_sync_budget_mb`` and ``provider``:
    override None → provider default (unknown provider → unlimited);
    override 0 → unlimited (explicit); override N>0 → N MiB.
    """
    override = account.daily_sync_budget_mb
    if override is not None:
        if override == 0:
            return None  # explicit unlimited
        return override * _MIB
    default_mb = PROVIDER_DAILY_BUDGET_MB.get(account.provider)
    return default_mb * _MIB if default_mb is not None else None


def compute_progress(done_msgs: int, total_msgs: int | None) -> float | None:
    """0..100, None without a usable total. Clamped: done can exceed total
    when the mailbox shrank between STATUS passes — clamp, never lie >100."""
    if not total_msgs or total_msgs <= 0:
        return None
    return max(0.0, min(100.0, done_msgs / total_msgs * 100.0))


def _round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def _label(seconds: int | None) -> str | None:
    """Compact ETA label: "<1h", "≈ Nh", "≈ Nd" (English UI copy)."""
    if seconds is None:
        return None
    if seconds < 3600:
        return "<1h"
    if seconds < 86400:
        return f"≈ {max(1, _round_half_up(seconds / 3600))}h"
    return f"≈ {max(1, _round_half_up(seconds / 86400))}d"


def estimate_eta(
    *,
    done_msgs: int,
    total_msgs: int | None,
    done_bytes: int,
    bytes_today: int,
    budget_bytes: int | None,
    run_rate_msgs_per_s: float,
) -> dict:
    """ETA for the remaining initial sync: {"seconds", "days", "label"}.

    Degrades gracefully: no total or nothing done yet → all None (there is
    no avg-bytes basis). With a budget, the binding constraint is bytes:
    remaining work beyond today's headroom costs whole budget-days. Without
    one, the observed run rate extrapolates.
    """
    none = {"seconds": None, "days": None, "label": None}
    if not total_msgs or total_msgs <= 0 or done_msgs <= 0:
        return none
    remaining_msgs = max(0, total_msgs - done_msgs)
    if remaining_msgs == 0:
        return {"seconds": 0, "days": 0.0, "label": "<1h"}

    avg_bytes = done_bytes / done_msgs
    remaining_bytes = remaining_msgs * avg_bytes

    if budget_bytes:
        headroom_today = max(0, budget_bytes - bytes_today)
        if remaining_bytes > headroom_today:
            # Budget-bound: the tail beyond today's headroom costs whole
            # budget-days. seconds/days measure BUDGET-DAYS only — they
            # exclude the time to burn today's remaining headroom and the
            # wait to the next UTC midnight; the UI consumes eta_label, the
            # raw numbers are coarse by design. The label rounds UP (ceil,
            # floor 1): even a small overshoot waits for tomorrow's budget,
            # and 1.4 budget-days is closer to two wall-clock days than one.
            full_days = (remaining_bytes - headroom_today) / budget_bytes
            return {
                "seconds": int(full_days * 86400),
                "days": full_days,
                "label": f"≈ {max(1, math.ceil(full_days))}d",
            }
        # Fits in today's headroom — the run rate is the constraint.

    if run_rate_msgs_per_s and run_rate_msgs_per_s > 0:
        seconds = int(remaining_msgs / run_rate_msgs_per_s)
        return {"seconds": seconds, "days": seconds / 86400, "label": _label(seconds)}
    return none


def next_budget_resume(now: datetime) -> datetime:
    """Next UTC midnight after ``now`` (+ 0-30 min jitter) — when the daily
    ledger resets and the budget pause may lift."""
    midnight = datetime.combine(
        now.astimezone(UTC).date() + timedelta(days=1), time(0, 0), tzinfo=UTC
    )
    # Jitter, not crypto: spreads fleet resumes across the half hour.
    return midnight + timedelta(seconds=random.uniform(0, 1800))  # noqa: S311


def next_throttle_resume(now: datetime, attempt: int) -> datetime:
    """Exponential backoff for provider throttles: 4h x 2^(attempt-1),
    capped at 24h, + 0-10 min jitter. attempt < 1 clamps to 1.

    The exponent is capped BEFORE exponentiation (review): timedelta * 2**n
    evaluates the power first, and a day-long outage really does produce
    attempt counts in the 40s — 2**(attempt-1) overflowed timedelta at
    attempt 34. 2**12 already lands far beyond the cap.
    """
    attempt = max(1, attempt)
    delay = min(timedelta(hours=4) * 2 ** min(attempt - 1, 12), timedelta(hours=24))
    return now + delay + timedelta(seconds=random.uniform(0, 600))  # noqa: S311


def next_transient_resume(now: datetime, attempt: int) -> datetime:
    """Short backoff for network blips: 2min x 2^(attempt-1), capped at
    30min, + 0-30 s jitter. attempt < 1 clamps to 1. Exponent capped before
    exponentiation — see next_throttle_resume (overflow at attempt 41)."""
    attempt = max(1, attempt)
    delay = min(timedelta(minutes=2) * 2 ** min(attempt - 1, 12), timedelta(minutes=30))
    return now + delay + timedelta(seconds=random.uniform(0, 30))  # noqa: S311
