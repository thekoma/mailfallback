# Account Detail Page Redesign — Complete Specification

> **Purpose**: This document captures the full output of a 6-agent UX/engineering debate (~125 message exchanges) that produced a comprehensive redesign specification for the MailFallBack account detail page. It is designed to be self-contained — feed it to a fresh Claude session alongside CLAUDE.md to resume implementation without context loss.

## Table of Contents

1. [Why This Redesign](#1-why-this-redesign)
2. [Design Philosophy](#2-design-philosophy)
3. [Page Architecture](#3-page-architecture)
4. [Hero Panel — 10 States](#4-hero-panel--10-states)
5. [Sync Progress System](#5-sync-progress-system)
6. [mbsync Output Parser](#6-mbsync-output-parser)
7. [Progress Data Model](#7-progress-data-model)
8. [Error Handling](#8-error-handling)
9. [Color System](#9-color-system)
10. [Below-Hero Sections](#10-below-hero-sections)
11. [Backend Changes](#11-backend-changes)
12. [Frontend Changes](#12-frontend-changes)
13. [CSS Specification](#13-css-specification)
14. [Implementation Phases](#14-implementation-phases)
15. [Current State of Codebase](#15-current-state-of-codebase)
16. [Design Decisions Log](#16-design-decisions-log)

---

## 1. Why This Redesign

The current account detail page (`templates/account_detail.html`, 362 lines) is a dumping ground of 14+ sections. Key problems:

- **No real-time sync progress** — just a "syncing" badge that spins until done
- **Live log exists but broken** — polls every 2s, re-renders entire `<pre>` block, no progress parsing
- **No percentage or ETA** — first sync of 5000+ messages shows zero progress indication
- **Error handling is raw** — shows verbatim mbsync output, no translation or action buttons
- **Page is cluttered** — info table, stats, error box, ownership, edit form, migrate, delete, history all stacked without hierarchy
- **No state-driven UI** — page looks the same whether idle, syncing, errored, or suspended

## 2. Design Philosophy

**The hero panel IS the status.** Every page state has a distinct hero with:
- Color-coded left border (4px) + tinted background
- State-specific icon, headline, and actions
- Progressive disclosure: summary always visible, detail one click away

**One bar = one number.** No fake aggregate percentages. The progress bar shows the current folder's message pull progress. Folder position shown via chip dots.

**Sysadmin data accessible, not default.** Raw logs, exit codes, connection details are always reachable (one click in a `<details>`) but don't overwhelm the default view.

**Diagnostic mode.** When the last sync failed or account is unauthenticated, Connection + History + inline log auto-expand. Healthy accounts show a calm default.

## 3. Page Architecture

Single page with scroll-spy navigation. No tabs. Top to bottom:

```
┌─ Sticky Page Header (40px) ──────────────────────────────────┐
│  ← Accounts   alice@example.com                    ● Status  │
├──────────────────────────────────────────────────────────────┤
│  [Conditional banners: Suspended / Migrating / Diagnostics]  │
├──────────────────────────────────────────────────────────────┤
│  ┌─ HERO PANEL (120-380px, sticky on desktop) ─────────────┐ │
│  │  State-specific content (see Section 4)                  │ │
│  │  Action buttons: [Sync now] [Stop] [Test] [⋯]           │ │
│  └──────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────┤
│  Stats Strip (dense inline pills, dot-separated)             │
│  ● Status · 12,453 msgs · 1.2 GB · 7 folders · 3m ago (✓)   │
│  Every 6h · XOAUTH2 (14d)                                    │
├──────────────────────────────────────────────────────────────┤
│  Scroll-spy nav:                                              │
│  Overview · Mailbox · History · Connection · Settings · Manage│
├──────────────────────────────────────────────────────────────┤
│  ▸ Overview (info table)                                     │
│  ▸ Mailbox (folder stats from doveadm)                       │
│  ▸ Sync History (last 5 always visible + reveal older)       │
│  ▸ Connection (diagnostic, read-only)                        │
│  ▸ Settings (one form, fieldset-grouped)                     │
│  ▸ Manage (admin: migrate, force resync, delete)             │
└──────────────────────────────────────────────────────────────┘
```

### Sticky behavior
- Page header: always sticky (40px)
- Hero panel: sticky on desktop (≥768px), top: 40px
- Scroll-spy nav: sticky below hero
- On mobile (<768px): nothing sticky except page header

### Section state persistence
- Per-account `localStorage` key stores which `<details>` are open/closed
- Restored on `DOMContentLoaded`
- Diagnostic mode overrides: auto-opens Connection + History + log when `last_exit_code != 0`

## 4. Hero Panel — 10 States

### State 1: IDLE (sync OK)
```
┌─ ✓ Up to date ─────────────── [↻ Sync now] [Test connection] [⋯] ─┐
│  GREEN 4px left-border, pale green bg                               │
│                                                                     │
│  Backed up 3 minutes ago (2026-05-02 14:37:12 UTC)                  │
│  12,453 messages · 1.2 GB · 7 folders                               │
│  Next backup: every 6h · in 47 minutes                              │
└─────────────────────────────────────────────────────────────────────┘
```

### State 2: EMPTY (never synced)
```
┌─ ○ Not yet backed up ──────────── [↻ Start first backup] [⋯] ─┐
│  GRAY left-border, white bg                                     │
│                                                                 │
│  No messages downloaded yet                                     │
│  This account is connected and ready                            │
└─────────────────────────────────────────────────────────────────┘
```

### State 3: SYNCING (determinate — message pulling in progress)
```
┌─ ◉ Backing up… ───────── job 4d2e1f… [📋] ─[Stop] [Test] [⋯] ─┐
│  BLUE left-border, pale blue bg, pulsing dot (2s cycle)          │
│                                                                  │
│  Downloading All Mail                                            │
│  ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░  142 / 5,302 messages               │
│                                                                  │
│  ● ● ● ◉ ○ ○ ○                                                   │
│  Folder 4 of 7                                                   │
│                                                                  │
│  elapsed 47s · about 5 minutes left                              │
│                                                                  │
│  ▾ Folder breakdown                                              │
│     Inbox          ████████████████  679 / 679   ✓               │
│     Sent           ████████████████  412 / 412   ✓               │
│     All Mail       ███▓░░░░░░░░░░░░  142 / 5,302 ⟳              │
│     Drafts         ░░░░░░░░░░░░░░░░    0 / ?     …              │
│                                                                  │
│  ▸ Live log (tail · 200 lines)                                   │
│  Started: 19:55:00 UTC, +47s elapsed                             │
└──────────────────────────────────────────────────────────────────┘
```

### State 3b: SYNCING-INDETERMINATE (early phase)
```
┌─ ◉ Looking at your mailbox… ──── job 4d2e1f… [📋] ─[Stop] [⋯] ─┐
│  BLUE, pulsing dot                                                │
│                                                                   │
│  Counting messages — this can take a minute for large accounts.   │
│  ▓▓▓▒░░▒▓░░▓▒░▓▒░▓░▒▓░▒▓▒░▓░░▒▓░▓▒░░▒▓  (indeterminate bar)     │
│                                                                   │
│  elapsed 18s                                                      │
│  ▸ Live log (tail · 200 lines)                                    │
└───────────────────────────────────────────────────────────────────┘
```
Switches to State 3 when `phase==syncing AND messages_pull_total > 0`.

### State 4: FIRST-SYNC (account.last_sync_at is NULL + syncing)
Same as State 3 but adds reassuring sentence:
```
│  This may take a while — first backups download every             │
│  message in your account.                                        │
```
And folder total shows `~7` with tilde (estimated from prior sync or live count).

### State 5: ERROR (last sync failed)
```
┌─ ✕ Backup failed ──────── [Try again] [Test connection] [⋯] ─┐
│  RED left-border, pale red bg                                  │
│                                                                │
│  Sign-in needed                                                │
│  Google rejected the password. This usually means the app      │
│  password was revoked or rotated.                              │
│  [Re-authenticate]                                             │
│                                                                │
│  ─── Technical details ──────────────────────────────────────  │
│  IMAP error: AUTHENTICATIONFAILED                              │
│  C: A0001 LOGIN "user@example.com" "***"                       │
│  S: A0001 NO [ALERT] Invalid credentials...                    │
│  (last 20 lines, monospace, red border)                        │
│  [Copy] [Show full log ▾] [Download log]                       │
│                                                                │
│  Failed 3 minutes ago · Last success: 2h ago · 12,341 msgs     │
└────────────────────────────────────────────────────────────────┘
```
**Diagnostic mode auto-activates**: Connection + History sections auto-open below.

### State 6: SIGN-IN NEEDED (OAuth expired)
```
┌─ ⚠ Sign-in needed ───────────────────────────────────────────┐
│  AMBER left-border, pale amber bg                             │
│                                                               │
│  ┌────────────────────────────────────────────────────┐       │
│  │  [G]  Reconnect with Google                        │       │
│  └────────────────────────────────────────────────────┘       │
│                                                               │
│  Your Google sign-in expired. Backups will resume             │
│  automatically as soon as you reconnect.                      │
│  Last successful backup: 2h ago · 12,341 messages             │
│  [Test connection] [⋯]                                        │
└───────────────────────────────────────────────────────────────┘
```

### State 7: SERVER UNREACHABLE
```
┌─ ⚠ Server unreachable ────── [↻ Try again] [Test] [⋯] ─┐
│  AMBER                                                    │
│  Couldn't connect to imap.gmail.com                       │
│  We'll keep retrying every 5 minutes.                     │
│  Last successful backup: 12m ago · Next retry in 3m       │
└───────────────────────────────────────────────────────────┘
```

### State 8: STALE (expected sync didn't run)
```
┌─ ⚠ Up to date — but we expected another backup by now ─┐
│  AMBER                                                   │
│  Last backed up 1h ago. Next scheduled was 30m ago.      │
│  [↻ Try a backup now]                                    │
│  ▸ Why might this happen?                                │
└──────────────────────────────────────────────────────────┘
```
Triggered when `now - last_sync > 3 × scheduled_interval` (floor 30m, ceiling 24h).

### State 9: PAUSED (suspended)
```
┌─ ⏸ Paused ───────────────────────── [▶ Resume backups] ─┐
│  GRAY left-border, pale gray bg, dotted bottom-border     │
│  Backups are paused. Existing mail stays readable.        │
│  Last backed up 3 days ago · 12,453 messages              │
│  [Sync now] still enabled (manual sync allowed)           │
└───────────────────────────────────────────────────────────┘
```

### State 10: MIGRATING
```
┌─ ↗ Moving your mail ──────────────────────── 64% ─┐
│  CYAN left-border, pale cyan bg                    │
│  Moving to a new location. Backups resume when done│
│  ▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░  3,821 / 5,964 files        │
│  1.4 GB / 2.2 GB                                   │
└────────────────────────────────────────────────────┘
```

## 5. Sync Progress System

### Progress bar behavior
- **Per-folder bar** (NOT aggregate) — one bar showing messages pulled for the current folder
- **Chip row** below bar: `● ● ● ◉ ○ ○ ○` — filled=done, ring=active, hollow=pending
- Bar width = `messages_pulled / messages_total` for current folder
- Indeterminate striped animation when totals unknown

### Folder breakdown (collapsible `<details>`)
4-column layout per folder:
```
Inbox          ████████████████  679 / 679   ✓
Sent           ████████████████  412 / 412   ✓
All Mail       ███▓░░░░░░░░░░░░  142 / 5,302 ⟳
Drafts         ░░░░░░░░░░░░░░░░    0 / ?     …
```

### ETA rules
- First 5s of pulling → `low` confidence → show "estimating…"
- After 30s → `medium` → show bucketed phrase ("about 5 minutes left")
- After 100 messages → `high` → show phrase + precise data in hover tooltip
- `seconds_since_output > 30` → `stalled` → show "no output for Ns"

### Live log (inside hero, `<details>`)
- Terminal-dark bg (`#0d1117`) in both light and dark theme
- Last 200 lines, scrollable, 240px default / 600px expanded
- Severity row-tinting: `.log-line.is-error` red bg, `.log-line.is-warn` amber bg
- Auto-scroll with pause-on-manual-scroll + "Jump to latest ▾" floating button
- Copy button, no timestamps prepended (false source of truth)
- NO modal — always inline (sysadmin veto)

### Polling
- Flat 2s interval while `status=running`
- `phase-changed` HX-Trigger event fires immediate re-poll on phase transitions
- Self-terminates when `status=done|error` (server omits `hx-trigger`)
- No polling when idle

## 6. mbsync Output Parser

New file: `services/sync_progress.py` — pure function, stateless, ~50ms over 10k lines.

### Detectable patterns

| Field | Source line | Reliability |
|-------|------------|-------------|
| `phase: connecting` | `Connecting to <host> (<ip>:<port>)` | High |
| `phase: authenticating` | `Authenticating with SASL` / `Logging in as <user> (<METHOD>)` | High |
| `phase: syncing` | first `Opening far side box` | High |
| `phase: done` | `Channels: N Boxes: N ...` summary | High |
| `current_folder` | `Opening far side box <name>` | High |
| `auth_method` | `Logging in as <user> (<METHOD>)` | High |
| `connection_host/ip/port` | `Connecting to <host> (<ip>:<port>)` | High |
| `per_folder.near/far` | `(near|far): N messages, M recent` | High |
| Pull progress (default) | `F: +x/y *x/y #x/y` and `N: +x/y *x/y #x/y` | High |
| Pull progress (-V) | `Pulling new message N/M (uid X)` | High |
| errors | `Error:`, `IMAP error:`, `AUTHENTICATIONFAILED` | High |
| warnings | `Warning:`, `Maildir error: skipping` | High |

### What CANNOT be extracted
- **Bytes transferred** — mbsync doesn't log per-message bytes. Show messages only.
- **Total folders upfront** — mbsync processes sequentially, doesn't announce total. Use `prior_folder_count` from last successful sync.
- **Per-folder ETA** — only message-level ETA is meaningful.

### Folder-total estimation
1. Use `prior_folder_count` from previous successful sync (`folder_total_estimate_source="previous_sync"`)
2. First-ever sync: show "Folder 3 of …" with ellipsis, NOT "?"

## 7. Progress Data Model

```python
@dataclass
class ProgressSnapshot:
    schema: int = 1
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    phase: Literal["queued", "starting", "connecting", "authenticating",
                   "listing", "syncing", "finalizing", "done", "error"]
    started_at: datetime | None
    last_output_at: datetime | None
    elapsed_seconds: int
    seconds_since_output: int
    stale: dict | None                  # {is_stale, since_seconds, hint}
    current_channel: str | None
    current_folder: str | None
    connection_host: str | None
    connection_ip: str | None
    connection_port: int | None
    auth_method: str | None
    tls_info: str | None
    folder_index: int
    folder_total_estimate: int | None
    folder_total_estimate_source: Literal["previous_sync", "live_count"] | None
    per_folder: list[FolderProgress]
    events: list[SyncEvent]
    errors: list[ParsedError]
    warnings: list[str]
    summary: dict | None
    exit_code: int | None
    signal: str | None
    duration_ms: int | None
    mbsync_version: str | None
    log_byte_offset: int
    eta_seconds: int | None
    eta_confidence: Literal["low", "medium", "high", "stalled"]
    raw_tail: list[str]                 # last 20 lines

@dataclass
class FolderProgress:
    name: str
    phase: Literal["opening", "loading", "pulling", "done"]
    near: int | None
    far: int | None
    added_done: int
    added_total: int
    flagged_done: int
    flagged_total: int
    expunged_done: int
    expunged_total: int

@dataclass
class ParsedError:
    at_line: int
    category: Literal["auth", "network", "disk", "rate_limit",
                       "tls", "config", "server", "unknown"]
    user_message: str
    technical_detail: str
    actionable: bool
    action: Literal["reauth", "retry", "admin", "none"] | None
```

### Three response shapes from one parser
- `GET /api/sync/jobs/{id}/progress` → JSON envelope (curl/jq/debug)
- `GET /api/sync/jobs/{id}/log?since=N` → text/plain raw bytes, cursored
- `GET /accounts/{id}/partials/sync-progress` → HTMX partial (UI)

## 8. Error Handling

### Error categories and user messages

| Category | Detection | User headline | Action button |
|----------|-----------|---------------|---------------|
| `auth` | `AUTHENTICATIONFAILED`, `LOGIN failed`, `Invalid credentials` | "Sign-in needed" / "Password rejected" | `[Re-authenticate]` |
| `network` | `Connection refused`, `Errno`, DNS failure | "Server unreachable" | `[Test connection]` |
| `tls` | SSL/TLS errors after silent retry | "Connection failed (encryption issue)" | `[Test connection]` |
| `disk` | Permission denied, disk full, I/O error | "Storage error" | `[Open log]` |
| `rate_limit` | `Too many connections`, `OVERQUOTA` | "Rate limited" | `[Retry in N min]` |
| `config` | `Channel not configured`, invalid mbsyncrc | "Configuration error" | `[Open log]` |
| `server` | IMAP protocol errors, mailbox unavailable | "Server error" | `[Open log]` |
| `unknown` | Anything else | "Backup failed — unknown error" | `[Open log]` |

### Error panel layout
- Translated headline (from `ParsedError.category`)
- Action button (per category)
- Technical details section: raw mbsync error verbatim, monospace
- Last 20 lines of log (always visible in error state)
- `[Copy] [Show full log ▾] [Download log]`
- `[jump to line N]` affordance per `ParsedError`

### Unknown category rule
Never speculate. Show "Backup failed — unknown error" with NO translation attempt.

## 9. Color System

| State | Accent hex | Tinted bg | Icon |
|-------|-----------|-----------|------|
| Idle (OK) | `#16a34a` green | `color-mix(8%)` | ✓ |
| Empty | `#6b7280` gray | `color-mix(8%)` | ○ |
| Syncing | `#2563eb` **blue** | `color-mix(8%)` | ◉ (pulse) |
| Warning | `#d97706` amber | `color-mix(8%)` | ⚠ |
| Sign-in needed | `#d97706` amber | `color-mix(8%)` | ⚠ |
| Error | `#dc2626` red | `color-mix(8%)` | ✕ |
| Suspended | `#6b7280` gray | `color-mix(8%)` | ⏸ |
| Migrating | `#0e7490` cyan | `color-mix(8%)` | ↗ |

**KEY DECISION**: Syncing is BLUE, not amber. Amber reserved for warnings only. This requires updating `.badge-syncing` from amber→blue across the entire app (dashboard, accounts list, activity feed).

Every state has both color AND icon shape — colorblind-safe redundant signal.

Amber bumped to `#d97706` (from `#f59e0b`) for WCAG AA contrast.

## 10. Below-Hero Sections

All rendered server-side, collapsed `<details>` by default (except in diagnostic mode).

### Stats Strip (dense inline pills)
```
● Up to date · 12,453 msgs · 1.2 GB · 7 folders · 3m ago (✓) · Every 6h · XOAUTH2 (14d)
```

### Overview (info table)
Email, Provider, Host:port, TLS, Auth method, Schedule (inline-editable with common-patterns dropdown), Maildir path (admin, click-to-copy).

### Mailbox (folder stats)
Per-folder counts and sizes from doveadm. Per-folder mini progress bars during sync.

### Sync History
- Last 5 rows always expanded
- Each row: timestamp, duration, status, exit code badge, message delta (+14/-0)
- Per-row inline log expander (NOT modal — sysadmin veto)
- "Show all (95 more)" lazy-loads 25 at a time

### Connection (diagnostic, read-only)
Last connect host:ip:port, TLS version+cipher, IMAP CAPABILITY, auth method, OAuth token expiry + Refresh, last DNS resolution, mbsync version, Test connection button.

### Settings
One form, fieldset-grouped: Schedule / Connection / Account / Sync flags. Per-fieldset hx-post save. All settings visible (no "advanced" toggle).

### Manage (admin-only)
Mail Store info + Migrate dropdown, Force resync, Download debug bundle, Delete account (type-to-confirm modal).

### Danger Zone
Delete Account — hairline top border, muted text.

## 11. Backend Changes

### New files
- `services/sync_progress.py` — `parse_mbsync_lines()` pure function + dataclasses
- `services/debug_bundle.py` — `build_bundle(account, db) -> bytes` (in-memory zip)

### Worker changes (`services/sync_worker.py`)
- Tee mbsync stdout/stderr to BOTH `_running_logs[job_id]` AND on-disk file
- Log path: `/data/logs/sync/{account_uuid}/{job_id}.log` (per-account subdir)
- File mode 0o600, owner UID 1000
- Capture `mbsync --version` at startup, stamp on each job
- On exit, write final `ProgressSnapshot` JSON to `SyncJob.parsed_summary`

### New endpoints
- `GET /api/sync/jobs/{id}/progress` → JSON ProgressSnapshot
- `GET /api/sync/jobs/{id}/log?since=N` → text/plain raw bytes, cursored
- `GET /api/sync/jobs/{id}/log/download` → text/plain attachment
- `GET /accounts/{id}/partials/sync-progress` → HTMX partial (polled 2s)
- `GET /accounts/{id}/partials/sync-log-tail?since_byte=N` → HTMX append-only
- `GET /accounts/{id}/partials/error-banner` → HTMX error panel
- `GET /accounts/{id}/debug-bundle.zip` → full debug bundle

### New DB columns (Alembic migration)
- `SyncJob.log_path: String | None` — path to on-disk log file
- `SyncJob.parsed_summary: Text | None` — final ProgressSnapshot JSON
- `SyncJob.mbsync_version: String | None`
- `SyncJob.signal: String | None` — "SIGTERM"/"SIGKILL" if killed

### New env vars
- `MAILFALLBACK_SYNC_LOG_DIR` (default `/data/logs/sync`)
- `MAILFALLBACK_SYNC_LOG_RETENTION_DAYS=30`
- `MAILFALLBACK_SYNC_LOG_RETENTION_PER_ACCOUNT=100`
- `MAILFALLBACK_SYNC_STALE_WARN_SECONDS=30`

### Suspend semantics change
`account.suspended` now means "no scheduled sync; manual sync ALLOWED". Sync-now button stays enabled for suspended accounts.

## 12. Frontend Changes

### New template partials
- `partials/sync_panel.html` — hero wrapper (all 10 states)
- `partials/sync_progress.html` — progress bar + folder chips + ETA (polled 2s)
- `partials/sync_log_tail.html` — append-only `<span>` per line (hx-swap="beforeend")
- `partials/error_banner.html` — translated error + action + technical details
- `partials/folder_breakdown.html` — per-folder 4-column rows with mini-bars
- `partials/stats_strip.html` — dense inline pills

### Removed
- `partials/sync_live_log.html` — replaced by sync_panel + sync_log_tail

### JS additions (`static/js/app.js`, ~80 lines)
- localStorage per-account section state (save on `toggle`, restore on load)
- Auto-scroll-pause for `.log-viewer` with "Jump to latest ▾" button
- Copy-to-clipboard for log + path-copy buttons
- HX-Trigger listeners: `phase-changed` → immediate re-poll, `sync-finished` → refresh stats/history
- Diagnostics-mode `data-mode="diag"` toggle

### CSS additions (`static/css/style.css`)
- 6-color state palette as CSS custom properties (light + dark)
- `.hero-panel` with state-color left border + tinted bg + min-height
- `.log-viewer` terminal-dark bg, severity row tinting
- `.folder-chips` (● ◉ ○ dot states)
- `.progress-bar` (indeterminate striped + determinate variants)
- `.stats-strip` pill row with tabular numerals
- `.banner` pattern (suspended/migrating/diagnostics — same chrome)
- `.badge-syncing` updated amber → BLUE (app-wide change!)
- Sticky page header + hero
- Animations: pulse 2s, stripe-slide 1s, gated behind `prefers-reduced-motion`

## 13. CSS Specification

### Hero panel
```css
.hero-panel {
  border-left: 4px solid var(--hero-accent);
  background: color-mix(in srgb, var(--hero-accent) 8%, transparent);
  border-radius: 0.5rem;
  padding: 1.25rem;
  min-height: 120px; /* prevent CLS */
}
/* Desktop sticky */
@media (min-width: 768px) {
  .hero-panel { position: sticky; top: 40px; z-index: 50; }
}
```

### State colors (CSS custom properties)
```css
.hero-idle    { --hero-accent: #16a34a; }
.hero-empty   { --hero-accent: #6b7280; }
.hero-syncing { --hero-accent: #2563eb; }
.hero-warning { --hero-accent: #d97706; }
.hero-error   { --hero-accent: #dc2626; }
.hero-paused  { --hero-accent: #6b7280; }
.hero-migrate { --hero-accent: #0e7490; }
```

### Progress bar
```css
.progress-bar {
  height: 8px;
  background: var(--pico-muted-border-color);
  border-radius: 4px;
  overflow: hidden;
}
.progress-bar-fill {
  height: 100%;
  background: var(--hero-accent);
  border-radius: 4px;
  transition: width 0.3s ease;
}
/* Indeterminate */
.progress-bar-fill.indeterminate {
  width: 100%;
  background: repeating-linear-gradient(
    -45deg, var(--hero-accent), var(--hero-accent) 10px,
    transparent 10px, transparent 20px
  );
  background-size: 200% 100%;
  animation: stripe-slide 1s linear infinite;
}
@keyframes stripe-slide { to { background-position: -28px 0; } }
```

### Log viewer
```css
.log-viewer {
  background: #0d1117;
  color: #c9d1d9;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 0.8rem;
  padding: 0.75rem;
  border-radius: 0.375rem;
  max-height: 240px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-viewer.expanded { max-height: 600px; }
.log-line.is-error { background: rgba(220, 38, 38, 0.15); }
.log-line.is-warn { background: rgba(217, 119, 6, 0.15); }
```

### Folder chips
```css
.folder-chips { display: flex; gap: 0.35rem; align-items: center; }
.chip { width: 10px; height: 10px; border-radius: 50%; }
.chip.is-done { background: var(--hero-accent); }
.chip.is-active { border: 2px solid var(--hero-accent); background: transparent; }
.chip.is-pending { border: 1px solid var(--pico-muted-color); background: transparent; }
```

## 14. Implementation Phases

### Phase 1: Parser + data model (backend only)
- Create `services/sync_progress.py` with `parse_mbsync_lines()`
- Add dataclasses
- Unit tests with real mbsync output samples
- Alembic migration for new SyncJob columns
- Wire worker to write logs to disk

### Phase 2: Hero panel + progress endpoint
- Create `partials/sync_panel.html` with all 10 states
- New progress HTMX endpoint (polled 2s)
- CSS for hero states, progress bar, folder chips
- Wire into `account_detail.html`

### Phase 3: Error handling + diagnostic mode
- Error panel with translated messages
- Category-based action buttons
- Auto-expand diagnostic sections
- Error banner partial

### Phase 4: Below-hero sections
- Stats strip
- Scroll-spy navigation
- Restructured sections (Overview, Mailbox, History, Connection, Settings, Manage)
- localStorage section state persistence

### Phase 5: Polish
- Log viewer with auto-scroll-pause
- Debug bundle download
- Badge-syncing amber→blue app-wide
- Animations + `prefers-reduced-motion`
- Mobile responsive adjustments

## 15. Current State of Codebase

### Last commit
`39db32c` — Session 7: credential validation, form redesign, OAuth, live sync log

### Tests
185 passing, lint clean.

### What already exists (don't rebuild)
- `_running_logs` dict in `sync_worker.py` — in-memory live log per job
- `get_live_log(job_id)` function — returns current log text
- `GET /api/sync/jobs/{id}/live-log` endpoint — returns JSON with status + log
- `partials/sync_live_log.html` — basic polling partial (to be replaced)
- `startSyncPolling(jobId)` in JS — loads live log via HTMX
- Hero/status badges, icon buttons, collapsible sections — existing CSS patterns

### Files that will be heavily modified
- `templates/account_detail.html` (362 lines) — complete rewrite
- `static/js/app.js` — add ~80 lines for section state, log viewer, phase listeners
- `static/css/style.css` — add ~100 lines for hero, progress, log viewer, chips

### Files to create
- `services/sync_progress.py` (~200 lines parser + dataclasses)
- `services/debug_bundle.py` (~50 lines)
- `partials/sync_panel.html` (~150 lines)
- `partials/sync_progress.html` (~40 lines)
- `partials/sync_log_tail.html` (~10 lines)
- `partials/error_banner.html` (~40 lines)
- `partials/folder_breakdown.html` (~25 lines)
- `partials/stats_strip.html` (~15 lines)

### Alembic migration needed
4 new columns on `SyncJob`: `log_path`, `parsed_summary`, `mbsync_version`, `signal`

## 16. Design Decisions Log

Key debates and resolutions from the 6-agent discussion:

| Decision | Alternatives considered | Resolution | Reason |
|----------|----------------------|------------|--------|
| **No tabs** | Tabs (3 rounds), scroll-spy | Single page + scroll-spy | Cmd+F works everywhere, simpler to implement, sysadmin needs all data accessible |
| **Blue for syncing** | Amber (existing), blue | Blue | Amber feels alarming for a healthy normal sync; amber reserved for warnings |
| **Per-folder bar, not aggregate** | Segmented overall bar | Single per-folder bar + chip dots | No truthful aggregate % — denominator unknown until all folders opened |
| **No fake bytes** | Bytes transferred estimate | Messages only | mbsync doesn't log per-message bytes |
| **Inline log, never modal** | Modal dialog | `<details>` inside hero | Sysadmin: modals break Cmd+F and copy-paste |
| **Polling 2s, not SSE** | SSE, WebSocket, adaptive polling | Flat 2s poll | HTMX native, simpler than SSE, adequate for 2s updates |
| **JSON progress + HTML partial** | Pure JSON, pure HTML | Both from same parser | JSON for curl/debug, HTML for HTMX UI |
| **Folder estimate from prior sync** | IMAP LIST before sync, live count | Prior sync count | Extra connection is wasteful; "Folder 3 of …" for first sync |
| **Diagnostic mode auto-expand** | Always expanded, user toggle | Auto on error, persist via localStorage | Don't overwhelm healthy view, show detail when needed |
| **Suspend = no auto + manual allowed** | Suspend blocks everything | Manual sync stays enabled | Sysadmin needs to trigger one-off syncs on paused accounts |
