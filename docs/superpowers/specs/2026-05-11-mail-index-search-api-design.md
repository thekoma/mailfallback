# Mail Index + Search API — Backend Foundation

## Problem

The current `/api/restore/workspace/search` endpoint mounts each in-range restic snapshot (full `restic restore`, ~75 MB per snapshot) and runs IMAP `SEARCH` cross-namespace. This does not scale:

- Gmail-class mailboxes: 13 GB / ~150k messages.
- Paranoid backup users: dozens of snapshots per day.
- Mounting 30 snapshots per search session = 2+ GB of temp disk + minutes of `restic restore` per request.
- Cross-account search is impossible (search is scoped to one account at a time).
- Body search uses Dovecot's FTS but only after the heavy mount step.

The fundamental mismatch: snapshots are a storage-system artifact, not a search axis. Users describe lost mail by sender / topic / fuzzy time, never by snapshot ID. With mbsync's no-expunge flag, the live Maildir is functionally a superset of every prior snapshot for ~95 % of recovery cases. We are mounting snapshots to search content that is already on local disk.

## Solution

Build a **persistent metadata index** in PostgreSQL that captures, per `(account_id, message_id)`, the headers needed to answer 95 % of search queries (Date, From, Subject, To, folder, account, alive-flag, snapshot-membership). Search hits the index — sub-100 ms responses on millions of rows — and only escalates to Dovecot FTS for body content on the survivors of the index query. Snapshots become a dimension on each row (a join-table membership), never a search prerequisite.

This spec covers **backend only**: schema, build pipeline, REST API. The UI redesign that consumes this API is a separate spec (cycle 2).

## Architecture

### Components

```
┌─────────────────────┐
│ sync_worker         │ post-sync hook → header-parse new files → upsert
│ (existing)          │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐    ┌──────────────────────────┐
│ index_service (new) │───▶│ mail_index.* schema      │
│ - upsert_message    │    │ - messages               │
│ - mark_deleted      │    │ - snapshot_messages      │
│ - record_snapshot   │    │ - rebuild_status         │
│ - prune_snapshot    │    └──────────────────────────┘
└──────────┬──────────┘                ▲
           │                            │
           │     ┌──────────────────────┴──┐
           │     │ search_service (new)    │
           │     │ - search_messages       │ ◀──┐
           │     └─────────────────────────┘    │
           │                                     │
           ▼                                     │
┌─────────────────────┐                ┌─────────┴────────────┐
│ backup_worker       │                │ restore router (mod) │
│ - on success: bulk  │                │ POST /api/restore/   │
│   record_snapshot   │                │      search (new)    │
└─────────────────────┘                └──────────────────────┘
```

### Data flow

1. **mbsync sync completes** (existing `sync_worker._run_scheduled_sync`): post-hook calls `index_service.upsert_message_set(account_id, maildir_root)`. The service walks any files newer than `last_indexed_at`, parses headers (`email.parser.BytesHeaderParser`, body untouched), upserts into `mail_index.messages`. Files removed since last walk are marked `deleted_at = now()`.

2. **restic backup completes** (existing `backup_worker.execute_backup`): post-hook calls `index_service.record_snapshot(account_id, snapshot_id)`. Single bulk INSERT: every currently-alive `message_id_hash` for the account gets a row in `snapshot_messages` for the new `snapshot_id`.

3. **restic forget --prune completes** (new wrap around existing prune flow): `index_service.prune_snapshot(snapshot_id)` deletes the corresponding rows from `snapshot_messages`. Single transaction.

4. **User search** (new `POST /api/restore/search`): `search_service.search_messages(query, filters)` issues a Postgres query against the index. If the request opts into body search, the result set (≤ N hundreds of `Message-Id`s) is passed to a thin Dovecot FTS pass that filters by body keyword on the live Maildir.

## Data Model

New Alembic migration creates schema `mail_index` with three tables.

### `mail_index.messages`

```sql
CREATE TABLE mail_index.messages (
    account_id        UUID         NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    message_id_hash   BYTEA        NOT NULL,           -- SHA-1(Message-Id), 20 B
    message_id        TEXT         NOT NULL,           -- raw Message-Id, for display + dedup audit
    date_sent         TIMESTAMPTZ,                     -- RFC822 Date header, NULL if missing/unparseable
    from_addr         TEXT,                            -- normalised "user@domain"
    from_name         TEXT,
    subject           TEXT,
    to_addrs          TEXT[],                          -- normalised list
    folder_path       TEXT         NOT NULL,           -- latest known location
    maildir_filename  TEXT         NOT NULL,           -- latest known filename (UID + flags)
    size_bytes        INTEGER,
    first_seen_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at        TIMESTAMPTZ,                     -- NULL = alive in live Maildir
    tsv               TSVECTOR,                        -- subject + from + to_addrs + from_name
    PRIMARY KEY (account_id, message_id_hash)
);

CREATE INDEX idx_messages_account_date  ON mail_index.messages (account_id, date_sent DESC);
CREATE INDEX idx_messages_tsv           ON mail_index.messages USING GIN (tsv);
CREATE INDEX idx_messages_account_alive ON mail_index.messages (account_id) WHERE deleted_at IS NULL;
```

`tsv` is recomputed in a `BEFORE INSERT/UPDATE` trigger from `subject`, `from_addr`, `from_name`, `to_addrs`. Sizing: ~600 B/row + ~40 % index overhead → 13 GB Gmail account ≈ 150 MB total.

### `mail_index.snapshot_messages`

```sql
CREATE TABLE mail_index.snapshot_messages (
    snapshot_id       TEXT         NOT NULL,           -- restic short ID (8 hex)
    account_id        UUID         NOT NULL,
    message_id_hash   BYTEA        NOT NULL,
    PRIMARY KEY (snapshot_id, account_id, message_id_hash),
    FOREIGN KEY (account_id, message_id_hash)
        REFERENCES mail_index.messages (account_id, message_id_hash) ON DELETE CASCADE
);

CREATE INDEX idx_snapmsg_account_msg ON mail_index.snapshot_messages (account_id, message_id_hash);
```

Sizing: 16 B/row × 150k messages × 30 snapshots = ~72 MB/account/month. Acceptable for forward-only scope. Pruning is `DELETE WHERE snapshot_id = X`.

### `mail_index.rebuild_status`

```sql
CREATE TABLE mail_index.rebuild_status (
    account_id        UUID         PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    state             TEXT         NOT NULL,           -- 'idle' | 'live_indexing' | 'snap_backfilling' | 'failed'
    last_indexed_at   TIMESTAMPTZ,                     -- watermark for incremental scans
    backfill_progress INTEGER,                          -- snapshots processed so far (when backfilling)
    backfill_total    INTEGER,                          -- snapshots to process
    last_error        TEXT
);
```

Drives the "indexing in progress" UI affordance and the `mfb index` CLI commands.

## Components

### `services/index_service.py` (new)

Pure-Python service, no I/O beyond Postgres + filesystem reads of Maildir. Public functions:

```python
def upsert_message_set(db: Session, account_id: str) -> int
    # Walk Maildir for files newer than last_indexed_at, header-parse,
    # bulk upsert into mail_index.messages. Mark missing files deleted.
    # Updates rebuild_status.last_indexed_at on success.
    # Returns count of rows touched.

def record_snapshot(db: Session, account_id: str, snapshot_id: str) -> int
    # Bulk INSERT every alive message_id_hash for this account into
    # snapshot_messages with the given snapshot_id. Idempotent
    # (ON CONFLICT DO NOTHING).
    # Returns count of rows inserted.

def prune_snapshot(db: Session, snapshot_id: str) -> int
    # DELETE all snapshot_messages rows for the snapshot_id.
    # Returns count of rows deleted.

def backfill_snapshots(db: Session, account_id: str) -> Iterator[ProgressEvent]
    # CLI-only. For each existing restic snapshot:
    #   restic ls --recursive snap-X --json
    #   match filenames against alive messages
    #   bulk INSERT snapshot_messages
    # Yields progress events for the CLI to display.
```

The header parser is `email.parser.BytesHeaderParser` (stops reading at the first blank line — no body I/O).

### `services/search_service.py` (new)

```python
def search_messages(
    db: Session,
    user: User,
    query: str,
    *,
    account_ids: list[str] | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    include_deleted: bool = True,
    snapshot_id: str | None = None,
    body: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> SearchResult
```

Two-phase semantics:

**Phase 1 (always runs)**: Postgres query against `mail_index.messages` joined with `account_owners`/`group_members` to enforce visibility. Filters: tsv-match on `query` (subject + from + to), date range, account scope, alive-or-deleted. If `snapshot_id` is set, INNER JOIN `snapshot_messages`.

**Phase 2 (if `body=True`)**: For each row from Phase 1 (capped at 500 candidates), construct a Dovecot SEARCH on the live Maildir's namespace+folder+UID list. Filter the candidate set down to messages whose body matches the keyword. Add to results. Snapshot-only matches (where the message is `deleted_at` and not in live Maildir) are excluded from body search in v1 — they would require mounting the snapshot, which defeats the purpose of the index. Documented limitation; addressed in cycle 2 if needed.

### `routers/restore.py` extension

Add `POST /api/restore/search` calling `search_service.search_messages`. Auth + ownership via existing `get_current_user` + `account_service.list_accessible_accounts` (returns the union of owned accounts + group accounts visible to the user).

The existing `POST /api/restore/workspace/search` becomes a **deprecated wrapper**: it accepts the old request shape, translates to the new search call (single account, body=request.search_body, etc.), and returns results in the legacy `{results, mounted_snapshots}` shape. This keeps the current UI working until cycle 2 ships the new UI.

### `services/sync_worker.py` modification

In `_run_scheduled_sync`, after the mbsync subprocess completes successfully, call:

```python
from mailfallback.services import index_service
try:
    index_service.upsert_message_set(db, account_id)
except Exception:
    logger.exception("post-sync index update failed for %s", account_id)
    # The sync itself succeeded; index will catch up on the next run.
```

The `try/except` is critical: index failures must NOT fail syncs.

### `services/backup_worker.py` modification

Same pattern: after `restic_service.run_backup` succeeds and the snapshot ID is known, call `index_service.record_snapshot(db, account_id, snapshot_id)` inside a try/except.

### `services/restic_service.py` extension

Add wrapper `prune_with_callback(destination, account_id, retention_args, *, on_pruned: Callable[[list[str]], None])`. Parses `restic forget --prune --json` output to extract removed snapshot IDs and invokes `on_pruned` once after the prune transaction. Wired by callers to call `index_service.prune_snapshot` for each removed ID.

### CLI: `mfb index` (new)

Three commands invoked by an admin via `docker compose exec mailfallback uv run mfb index ...`:

```
mfb index status [--account <id>]                 # show rebuild_status rows
mfb index rebuild-account <id>                    # full re-walk of live Maildir
mfb index backfill-snapshots <id>                 # populate snapshot_messages for existing snapshots
```

`backfill-snapshots` lists snapshots via `restic_service.list_snapshots`, then for each runs `restic ls --recursive snap-X --json` (new helper in `restic_service`), parses the file tree, matches filenames to `mail_index.messages.maildir_filename` (stable prefix match — flag suffixes are stripped), and bulk INSERTs `snapshot_messages`. Resumable via `rebuild_status.backfill_progress`.

## Build Pipeline

### Steady-state (post-deploy)

1. `mbsync` syncs new mail → `sync_worker` parses headers → upserts into `mail_index.messages` → updates `last_indexed_at`.
2. `restic backup` creates snap-N → `backup_worker` calls `record_snapshot(account_id, "N")` → bulk INSERT all alive `message_id_hash` for the account into `snapshot_messages`.
3. `restic forget --prune` removes snap-K → wrapper calls `prune_snapshot("K")` → DELETE rows.
4. User search → `search_service` queries Postgres. Optional body filter does Dovecot SEARCH on candidates.

Steady-state cost per new mail: ~1 ms (header parse + UPSERT). 100 new mails / day / account ≈ negligible.

Steady-state cost per new snapshot: O(messages alive in account). 150k mails ≈ 100 ms (bulk INSERT). Negligible.

### Migration (one-time, at deploy)

1. Alembic migration 014 creates `mail_index` schema + three tables + indexes + tsv trigger.
2. Empty schema means search returns nothing initially. The deprecated `/workspace/search` wrapper detects this (rebuild_status missing or state≠idle) and falls back to the legacy mount-based path automatically.
3. Admin runs `mfb index rebuild-account <id>` per account to populate. ~5 min per 150k-mail account, single-threaded. Resumable. Search transitions to using the index once `state=idle`.
4. Snapshot bits start populating from this moment forward (forward-only — confirmed user choice). Snapshots created BEFORE this moment have no rows in `snapshot_messages`; the index correctly reports them as "not searchable via index". The legacy `/workspace/search` path still mounts them on demand.
5. Optionally, admin runs `mfb index backfill-snapshots <id>` to retroactively populate older snapshots. Long-running, bounded I/O on restic metadata only. Resumable.

## Body Search Strategy

Body search is the only operation where the index alone does not answer the question. Strategy:

1. Phase 1 (Postgres) narrows candidates by header criteria + date + account + folder + snapshot membership. Cap output at 500 message IDs per account (configurable). Empirically this is enough for most user queries; the UI can warn if Phase 1 returned 500 (suggesting filters should be tighter before body grep).

2. Phase 2 (Dovecot) builds an IMAP `SEARCH UID <id_list> TEXT "<query>"` per account namespace. Dovecot's `fts_flatcurve` answers from its FTS index without scanning. Returns the survivors.

3. Result merging: phase 1 results (no body match) are returned with `body_matched=false`; phase 2 survivors flag `body_matched=true`. UI may filter or sort accordingly.

Snapshot-only messages (alive in snap, not in live) cannot have their bodies searched without mounting the snapshot. v1 excludes them from body search results, with a documented caveat. cycle 2 may address this via on-demand single-message extraction (`restic dump snap-X path/to/cur/<filename>` → grep), but this is out of scope here.

## Mail Lifecycle

A row in `mail_index.messages` has three states:

- **alive** (`deleted_at IS NULL`): present in the live Maildir as of `last_seen_at`.
- **deleted** (`deleted_at IS NOT NULL`): no longer present in live Maildir; still in `snapshot_messages` for any past snapshot that contained it.
- **revived**: was deleted, now appears again (e.g., user restored from snapshot via the existing flow). The next `upsert_message_set` sees the file present, sets `deleted_at = NULL`, updates `last_seen_at` and `maildir_filename`.

We never `DELETE FROM mail_index.messages`. Account deletion cascades via the `ON DELETE CASCADE` foreign key.

## API Shape

### `POST /api/restore/search`

Request:

```json
{
  "query": "fattura marzo",
  "account_ids": ["<uuid1>", "<uuid2>"],
  "range_start": "2026-04-01T00:00:00Z",
  "range_end":   "2026-05-11T23:59:59Z",
  "include_deleted": true,
  "snapshot_id": null,
  "body": false,
  "page": 1,
  "page_size": 50
}
```

`account_ids` defaults to all accounts visible to the caller (cross-account by default). `snapshot_id` defaults to null (search across all snapshots + live, deduped per Message-Id). `body` defaults to false.

Response:

```json
{
  "results": [
    {
      "message_id": "<abc@host>",
      "account_id": "<uuid>",
      "subject": "Re: fattura marzo",
      "from_addr": "fornitore@ditta.it",
      "from_name": "Mario Rossi",
      "to_addrs": ["andrea@example.com"],
      "date_sent": "2026-03-12T14:33:00Z",
      "folder_path": "INBOX",
      "alive_in_live": true,
      "snapshots": ["snap1", "snap2", "snap3"],
      "body_matched": null
    }
  ],
  "total": 187,
  "page": 1,
  "page_size": 50,
  "phase2_skipped_count": 0
}
```

`alive_in_live` lets the UI badge live-only vs snapshot-only matches. `snapshots` is the list of snapshot IDs containing this message (computed via JOIN to `snapshot_messages`). `body_matched` is null if `body=false`, true/false if `body=true` was honoured.

### `POST /api/restore/workspace/search` (deprecated wrapper)

Translates the old single-account request to the new `/search` call, formats the response in the legacy shape, returns 200 OK. Logs a deprecation warning. Kept until cycle 2 lands the new UI; then removed.

## Migration & Rollout

- **Alembic migration 014**: schema + tables + indexes + tsv trigger. Required.
- **Service code**: `index_service`, `search_service`, `mfb index` CLI all ship together.
- **Existing routes**: `/api/restore/workspace/search` becomes a wrapper. `/api/restore/jump` and the existing recovery flow are unchanged (they handle disaster recovery — separate path).
- **Deploy gate**: search returns "indexing in progress" on accounts whose `rebuild_status.state ≠ idle`. Non-blocking. Existing legacy path remains as fallback.
- **Admin runbook**: after deploy, run `mfb index rebuild-account <id>` per account. Document expected duration (~3 min per 100k mails).
- **Feature flag**: env var `MAILFALLBACK_USE_INDEX_SEARCH` (default `true`). Setting to `false` forces all searches through the legacy mount path. Lets users roll back if the index path misbehaves.

## Out of Scope

- **Restore UI redesign** (unified search box, progressive filters, deprecation of slider/preset workspace). This is cycle 2.
- **Body indexing in the catalog**. v1 keeps body search as a Phase 2 against Dovecot FTS. Cycle 3+ may add full-text body index in Postgres if Phase 2 cost becomes a problem.
- **Threading** (`In-Reply-To`, `References`). Not indexed. Spec author and user agreed: scope creep for v1.
- **Cross-snapshot diff UI** ("when did this Message-Id first appear / disappear"). Future cycle.
- **Recovery namespace integration** in `snapshot_messages`. The ephemeral mounts created by the current workspace flow do not produce restic snapshot IDs and thus do not appear in the index. Cycle 2 may either eliminate ephemeral mounts (replaced by index queries + on-demand extract) or wire them as virtual snapshot IDs.
- **Body search of snapshot-only messages**. Documented v1 limitation: only live-Maildir messages get body search. Snapshot-only matches return without `body_matched` info. Addressed in cycle 2/3 via per-message `restic dump` if needed.

## Future Work

- **Threading** (In-Reply-To/References) — enables "find this conversation" feature.
- **Body content in catalog** (TSVECTOR over body text) if Phase 2 cost exceeds budget. Storage cost: roughly doubles index size.
- **Catalog-aware restore UI** (cycle 2): search-first surface, progressive filters, dedup by Message-Id with snapshot provenance badges.
- **Retention policy on `mail_index.messages`**: prune rows where `deleted_at < now() - interval` AND no `snapshot_messages` row references them. Keeps the index lean over time.

## Open Questions

- **`message_id_hash` collision**: SHA-1 truncated to 20 B has collision probability negligible at our scale (10^9 messages need ≈ 10^48 hash space). Confirmed acceptable.
- **Concurrency**: simultaneous syncs on the same account (rare) handled by `INSERT ... ON CONFLICT (account_id, message_id_hash) DO UPDATE`. No application-level lock needed. Confirmed by user.
- **Recovery namespace bits**: deferred to cycle 2 when UI is redesigned. Confirmed by user.
- **Default `account_ids`**: cross-account by default. May surprise users who expect single-account scope. Mitigation: UI can default the picker to a single account; the API just doesn't constrain.
- **Phase 2 cap of 500 candidates**: empirical guess. Tunable via env var `MAILFALLBACK_SEARCH_BODY_CANDIDATE_CAP` once deployed.

## Future Work — settings migration

The new settings introduced here (`MAILFALLBACK_USE_INDEX_SEARCH`, `MAILFALLBACK_SEARCH_BODY_CANDIDATE_CAP`) follow MFB's current env-var convention. Per the planned settings-in-DB refactor (`docs/superpowers/specs/2026-05-11-restore-workspace-design.md` Future Work section), they migrate to the DB-backed operational tier when that work lands. No spec change required here.
