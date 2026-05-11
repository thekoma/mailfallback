# Restore Workspace — Unified, Story-Driven, Snapshot-Aware

## Problem

The current `/restore` page treats the actual restore action as a second-class citizen: it sits as a small "Move mail between mailboxes" card in the footer, while the page header is dominated by the Calendar of Safety and snapshot picker. The user almost always lands on `/restore` to *do something* — recover a deleted message, restore a folder, rebuild a wiped mailbox — and instead has to scroll past reassurance UI to find the tool that actually does the job.

There is also a hard architectural gap: the `/restore/move` tool only accepts **live IMAP accounts** as source. To restore from a snapshot today, the user must:

1. Pick a snapshot from `/restore/jump` → MFB does a **full restic restore** to a permanent Recovery sub-object.
2. Open Roundcube webmail.
3. Manually drag/copy messages from the Recovery namespace to their live mailbox.
4. Remember to delete the Recovery later.

This is heavy (full snapshot restored to disk even to fish out one message), slow (multi-step UI dance), and breaks the user's mental model — the Recovery is treated like a permanent artefact when it's usually a means to an end.

## Solution

Rebuild `/restore` as a **unified workspace** organised around the user's actual intent ("what do you need back?") rather than around the storage substrate. The workspace surfaces three story-driven entry points (single mail / folder / whole mailbox), runs search across live + selected snapshots via Dovecot's existing IMAP search, and uses **IMAP COPY** to put results back where they belong. Snapshots become **ephemeral Dovecot namespaces** mounted on demand by selectively restoring only the snapshots the user has scoped, with TTL-based auto-cleanup.

No new search engine, no parallel catalog database, no FUSE in the initial scope.

## User Stories

The workspace is shaped by three concrete recovery scenarios, each with a different optimal flow but all collapsible into the same UI:

1. **"I lost a single mail"** — search-first. User types a query (subject, sender), gets results across live + snapshots in the chosen time range, picks one or more, hits Restore.
2. **"I lost a folder / a subset"** — browse-first. User picks a folder in the source, picks a time anchor, restores the folder content (filtered or full).
3. **"I lost the whole mailbox"** — full-restore. User picks a snapshot (latest or a specific one) and restores everything to a destination mailbox (often a different provider). This is the only case that justifies a long-lived **persistent Recovery**, because the user may need days to migrate.

The UI presents these as three preset chips at the top of the workspace. Picking one pre-configures the form (search vs folder picker, time range default, persistent vs ephemeral mount); the user can always tweak.

## Workspace UX

### Layout

Single page, no full-screen takeovers. Two columns under a preset chip strip:

- **Left sidebar** (~240px): source mailbox dropdown, time-range slider, source toggles (Live / Snapshot), Advanced disclosure, destination mailbox dropdown.
- **Right panel**: search box + results list with per-result provenance badges and a "Restore selected" action.

Health banner and Calendar of Safety are demoted: rendered as a compact sidebar panel below the controls or moved to a smaller "Status" strip. They are context, not the primary surface.

### Time range and cost transparency

The time-range slider has two handles (start / end). As the user drags, MFB shows the count of in-range snapshots and the estimated temp disk for selective restore (e.g. "2026-03-01 → today · 8 snapshot, ~120 MB temp"). This makes the cost of a wide range visible without forcing the user to reason about restic internals.

Default range on landing: **last 7 days** (covers the common "I deleted something yesterday" case while keeping mount cost low).

### Search behaviour

When the user submits a search:

1. MFB queries Dovecot SEARCH on the live namespace immediately (instant, free).
2. For each in-range snapshot not already mounted, MFB triggers a selective restore in the background; the UI shows mount progress per snapshot.
3. As each snapshot becomes a namespace, MFB issues Dovecot SEARCH against it and merges results in the UI.
4. Each result row shows provenance badges: `live · INBOX`, `snap-2026-03-15 · Cestino`, etc. Messages that exist in multiple namespaces are deduplicated by `Message-ID` and show a combined badge ("live + 6 snap").

If the live search returns 0 hits and no snapshots are in range, the UI suggests expanding the range with the cost preview ("nothing in last 7 days. Try last 30 days? +5 snap, ~80 MB").

### Restore action

"Restore selected" applies the existing IMAP COPY engine from `2026-05-06-mail-restore-design.md`:

- Source = each selected message's namespace (live or `snap-X`)
- Destination = chosen destination mailbox
- Folder mapping options carry over (Original / `Restored/` prefix / Custom)

A success row appears in History; the user can dismiss the workspace, the ephemeral mounts will be cleaned up automatically when their TTL expires.

### Advanced disclosure

Collapsed by default. Contains:

- Strategy override per snapshot (force mount / force unmount)
- "Search in body" toggle (more expensive — requires reading message files, not just index cache)
- TTL override for ephemeral mounts
- "Promote this Recovery to persistent" — the escape hatch from ephemeral to today's behaviour

## Architecture

### Recovery model evolution

The `Recovery` SQLAlchemy model gains a `kind` column:

- `ephemeral` (new default for workspace-driven restores) — created on demand by the workspace, has a TTL, auto-cleaned by a scheduler job after N minutes of idle (no Dovecot access, no UI reference).
- `persistent` (today's behaviour) — created by explicit user action ("I lost everything, restore the whole snapshot"), no TTL, lives until deleted.

Both kinds are still Dovecot namespaces with the same schema otherwise. The `kind` distinction is purely about lifecycle.

Existing `Recovery` rows migrate to `kind = persistent` by default — no behaviour change for what's already there.

### Mount manager (new service: `mount_service.py`)

Single small service responsible for the mount lifecycle:

```python
def ensure_mounted(account_id, snapshot_id, kind="ephemeral") -> Recovery
def touch_mount(recovery_id)         # bumps last_accessed_at, defers cleanup
def cleanup_idle_mounts()            # scheduler-invoked, removes ephemeral past TTL
def force_unmount(recovery_id)
```

`ensure_mounted` is idempotent: if a Recovery already exists for `(account_id, snapshot_id)`, return it (and bump `last_accessed_at`). Otherwise:

1. Allocate a target dir under `{store_path}/.recovery-mounts/{account_id}/{snapshot_id}/`
2. Run `restic restore {snapshot_id} --target {target_dir}` (this is `restic_service.restore_snapshot` already)
3. Create `Recovery` row with `kind`, `mounted_at`, `last_accessed_at`
4. Trigger Dovecot reload (the dynamic Lua userdb already handles namespace assembly from Recovery rows)

`cleanup_idle_mounts` is scheduled hourly via APScheduler:

- Find ephemeral Recoveries where `last_accessed_at < now - TTL`
- For each: `force_unmount` → `rm -rf` the target dir → delete the Recovery row → reload Dovecot

### Search routing (extends `restore.py` router)

The workspace search endpoint:

1. Accepts `(account_id, query, range_start, range_end, sources, search_body)`
2. Computes the snapshot list in range from the existing restic snapshot listing
3. For each snapshot in range that is not yet mounted: calls `mount_service.ensure_mounted(..., kind="ephemeral")` parallelised through a dedicated thread pool (mirrors `backup_worker` pattern), capped by `MAX_PARALLEL_MOUNTS`
4. Streams partial results to the UI via HTMX SSE / polling — each chunk is a Dovecot SEARCH against one namespace, deduplicated by `Message-ID`

Subject + sender search uses Dovecot's index cache (`dovecot.index.cache`, present in every snapshot, no FTS plugin required). Body search is gated behind the Advanced toggle: without an FTS plugin (Solr/Xapian) Dovecot greps the message files, which is slow on large mailboxes. If we want fast body search later, configuring an FTS plugin is an orthogonal change.

### Restore action

Reuses the existing IMAP COPY engine. The only change: the source dropdown is now driven by the user's selection in the workspace (could be live namespace or any mounted snapshot namespace), not by a separate `/restore/move` form.

### `/restore/move` legacy

The existing `/restore/move` page is kept as-is for now (no breaking change for users who bookmarked it) but is no longer linked from `/restore`. It can be deprecated in a follow-up once the new workspace covers the same scenarios cleanly.

## Data Model Changes

### `Recovery` table

Add columns:

```sql
ALTER TABLE recoveries ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'persistent';
ALTER TABLE recoveries ADD COLUMN last_accessed_at TIMESTAMP NOT NULL DEFAULT now();
ALTER TABLE recoveries ADD COLUMN ttl_minutes INTEGER NULL;  -- NULL means no TTL
```

Migration: existing rows → `kind = 'persistent'`, `last_accessed_at = restored_at`, `ttl_minutes = NULL`.

New ephemeral Recoveries: `kind = 'ephemeral'`, `ttl_minutes = 30` (configurable via env `MAILFALLBACK_RECOVERY_EPHEMERAL_TTL_MINUTES`).

### Settings

```
MAILFALLBACK_RECOVERY_EPHEMERAL_TTL_MINUTES (default 30)
MAILFALLBACK_RECOVERY_MAX_PARALLEL_MOUNTS (default 5) — caps fanout
MAILFALLBACK_RECOVERY_BACKEND (default "restore", future: "fuse")
```

## Backend Pluggability

The mount manager exposes a single interface; today the only implementation is the selective-restore backend.

A future `fuse` backend would implement the same interface using `restic mount` + bidirectional mount propagation. It is **explicitly out of scope** for this spec because the operational cost (privileged container, K8s mount propagation, Dovecot mmap quirks on FUSE) is high and the ephemeral selective-restore approach already gives "good enough" behaviour for the common cases.

If implemented later, switching backends is a single env var; no schema or UI change.

## Migration & Backwards Compatibility

- Alembic migration 013: add the three new columns to `recoveries`.
- Existing `Recovery` rows preserve their behaviour (persistent, no TTL).
- The `/restore/move` page keeps working; it's just not the recommended entry point any more.
- The `/restore/jump` endpoint that mounts a snapshot stays — it now defaults to creating a `persistent` Recovery (matching today's user expectation when they explicitly pick a snapshot).
- Calendar of Safety, banner, and per-account snapshot strip remain on the page but in a smaller status panel.

## Out of Scope

- **Catalog database** (storing parsed `dovecot.index*` metadata in PostgreSQL): rejected — duplicates Dovecot's own indexing and adds a parallel ingest pipeline. Search uses Dovecot directly via mounted namespaces.
- **FUSE backend** for mount manager: deferred to a future spec. Current default and only implementation is selective restore.
- **Cross-account search** (search "fattura marzo" across all my mailboxes at once): out of scope, single-mailbox workspace only.
- **`/restore/move` deprecation**: kept available; deprecation is a follow-up decision after the new workspace is validated in production.

## Open Questions

- **Default time range**: spec says "last 7 days" — confirm before implementation. Could be parameterised by `MAILFALLBACK_RECOVERY_DEFAULT_RANGE_DAYS`.
- **Concurrency cap**: `MAX_PARALLEL_MOUNTS = 5` is a guess. Real cap depends on disk I/O and restic memory profile under concurrent restores. Tunable; revisit after first benchmarks.
- **Mount progress UI**: HTMX SSE vs polling — polling is simpler and we already do it elsewhere; SSE would feel snappier. Decide during plan phase.
- **Roundcube integration**: when the user wants to verify a recovery in Roundcube before restoring, do we expose ephemeral mounts as visible namespaces, or only as internal sources for the workspace? Default position: visible (consistent with persistent Recoveries) but with a clear "ephemeral, will disappear in N min" badge.
