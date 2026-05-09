# System Status Panel + Async Background Tasks

## Problem

Admin has no real-time visibility into system health from the dashboard. FTS reindex and force-resync run synchronously, blocking the HTTP request. No progress tracking, no singleton enforcement, no audit trail for long-running operations.

## Solution

1. **System Status Panel** — live strip of badges in the dashboard (admin-only) showing Dovecot health, FTS status, active syncs, and active restores. Badges expand on click to show details with progress bars.

2. **Async Background Task system** — `BackgroundTask` DB model + in-memory progress tracking for long-running operations (FTS reindex, force-resync). Singleton per task type, progress per-user, audit logged.

## Architecture

### System Status Panel

**Location:** Dashboard, between stat cards and "Needs Attention", admin-only.

**Layout:** "Strip + Expandable" — horizontal row of colored badges. Clicking a badge toggles an expanded detail panel below with progress bars, per-item status, timestamps.

**Polling:** The panel is an HTMX partial (`GET /partials/system-status`) that auto-refreshes every 5 seconds. The backend aggregates all 4 signals in one endpoint.

**4 Signals:**

| Signal | Data Source | Badge States |
|--------|------------|-------------|
| **Dovecot** | `check_dovecot_health()` cached 30s | 🟢 UP / 🔴 DOWN |
| **FTS** | `BackgroundTask` query | idle / running (N/M users) / error |
| **Sync** | `SyncJob` count + `Account` errors | N syncing · M errors |
| **Restore** | `RestoreJob` query | N active with progress |

**Badge color scheme:**
- Neutral/idle: `#1a3050` (dark blue)
- Active/warning: `#3a2a00` border `#c59000` (amber)
- Success: `#1b4332` (dark green)
- Error: `#4a1520` border `#e53e3e` (red)

**Expanded details show:**
- FTS: progress bar + per-user status (✓ done / ⏳ running / pending / ✗ error)
- Restore: progress bar + message counts + source → target
- Sync: list of syncing accounts + error accounts with last_error
- Dovecot: last check timestamp + error message if down

### BackgroundTask Model

```
BackgroundTask:
  id: UUID PK
  task_type: str  ("fts_reindex", "force_resync")
  status: enum (pending, running, completed, failed)
  progress_current: int (0)
  progress_total: int (0)
  details: JSON (per-user results, errors)
  started_at: datetime nullable
  completed_at: datetime nullable
  requested_by: str FK User.id
  created_at: datetime server_default=now
```

### Background Task Service (`services/background_tasks.py`)

```python
# In-memory progress for live polling (like sync_worker pattern)
_task_progress: dict[str, dict] = {}

def start_background_task(db, task_type, requested_by) -> BackgroundTask | None:
    """Create task if no running task of same type exists. Returns None if blocked."""

def get_task_status(db, task_type) -> dict:
    """Get latest task for type, merge with in-memory progress."""

def _run_fts_reindex(task_id):
    """Thread target: iterate users, call fts_rescan, update progress."""

def _run_force_resync(task_id):
    """Thread target: iterate users, call force_resync, update progress."""
```

**Singleton enforcement:** `start_background_task()` checks for existing `status in (pending, running)` before creating. Returns `None` if one is already running.

**Progress tracking:** In-memory dict `_task_progress[task_id]` updated after each user. The status endpoint merges DB state with in-memory progress for live updates. On completion, final state written to DB.

### Routes

**New endpoint for panel:**
```
GET /partials/system-status  (ui.py)
  → Returns system_status.html partial
  → Aggregates: dovecot health, latest BackgroundTask per type,
    SyncJob running count, RestoreJob active count
```

**Modified endpoints (ui_admin.py):**
```
POST /admin/dovecot/fts-reindex  → starts async task, redirects immediately
POST /admin/dovecot/force-resync → starts async task, redirects immediately
```

### Dovecot Health Cache

Simple module-level cache in `dovecot_manager.py`:

```python
_health_cache = {"ok": None, "error": None, "checked_at": 0}

def get_cached_health() -> dict:
    if time.monotonic() - _health_cache["checked_at"] > 30:
        result = check_dovecot_health()
        _health_cache.update(result, checked_at=time.monotonic())
    return _health_cache
```

### Template Structure

```
templates/partials/system_status.html
  - Strip of badges (always rendered)
  - Expanded detail divs (hidden by default, toggled by JS)
  - Each detail div has its own content based on signal type
```

**JS interaction:** Click handler on badges toggles a sibling detail div. No external JS file needed — inline `onclick="toggleStatusDetail('fts')"` following existing patterns.

### Data Flow

```
Dashboard load (admin)
  └─ Renders system_status.html inline (first load from server context)
  └─ hx-get="/partials/system-status" hx-trigger="every 5s" hx-swap="innerHTML"
       └─ Backend aggregates:
            ├─ get_cached_health()           → dovecot badge
            ├─ get_task_status(db, "fts_reindex")  → fts badge + detail
            ├─ get_task_status(db, "force_resync") → (merged into fts section)
            ├─ SyncJob.status==running count  → sync badge
            ├─ Account.sync_state==error count → sync badge
            ├─ RestoreJob active query         → restore badge + detail
            └─ Returns HTML partial
```

## Migration

Alembic migration to add `background_tasks` table. Fields:
- `id`, `task_type`, `status`, `progress_current`, `progress_total`, `details` (JSON), `started_at`, `completed_at`, `requested_by` (FK users.id nullable), `created_at`

## Testing

- **Unit tests for background_tasks service:** start task, singleton enforcement, progress tracking, completion
- **Route tests:** panel endpoint returns correct badge states, FTS route starts async task
- **No doveadm integration tests** (require running Dovecot)

## Files to Create/Modify

**New files:**
- `src/mailfallback/services/background_tasks.py` — task service
- `src/mailfallback/templates/partials/system_status.html` — panel template
- `tests/test_background_tasks.py` — tests
- Alembic migration for `background_tasks` table

**Modified files:**
- `src/mailfallback/models.py` — add `BackgroundTask` model
- `src/mailfallback/routers/ui.py` — add `/partials/system-status` endpoint, pass data to dashboard
- `src/mailfallback/routers/ui_admin.py` — make FTS/resync routes async via background_tasks
- `src/mailfallback/services/dovecot_manager.py` — add `get_cached_health()`
- `src/mailfallback/templates/dashboard.html` — add system status panel
- `src/mailfallback/static/css/style.css` — badge styles for status panel

## Visual Reference

Mockup saved at `docs/mockup-system-status-reference.html` — compare after implementation.
