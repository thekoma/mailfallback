# Restore: cross-account search, staging area, attachment index

**Date:** 2026-06-11
**Status:** approved (brainstorm 2026-06-11)
**Reference mockups (contract — implementation must match closely):**
- `.claude/mockup_restore_staging_reference.png` — workspace with cross-account results, preview pane, staging bar
- `.claude/mockup_attachments_view_reference.png` — "An attachment" preset view

## Problem

Three gaps in the restore experience:

1. **Search is single-mailbox.** The workspace forces a mailbox choice up front; the index API (`POST /api/restore/search`) already searches all visible accounts, but the UI still calls the legacy single-account wrapper.
2. **No way to read a message before restoring it.** Searching "antonio@test.it" returns 50 hits with identical subjects; the user cannot tell which one matters. Restores are blind.
3. **Attachments are invisible.** Nine times out of ten the attachment is what the user actually wants, but search results say nothing about them and there is no way to get one out without a full restore.

## Solution overview

Four additions, one cycle:

1. **Cross-account search by default** — scope select "All mailboxes (N)", per-result mailbox badge.
2. **In-app preview pane** — headers + body snippet for any hit (live or snapshot), without leaving the page.
3. **Staging area** — a per-user writable Dovecot namespace (`Staging/`). Selected results are copied there; the user curates in webmail (reads everything, deletes the irrelevant); MFB then pushes the survivors to the upstream IMAP server. Optional step — direct folder/full restores are untouched.
4. **Attachment index** — per-attachment rows (name/ext/size + MIME part locator) captured at index time, a fourth workspace preset "An attachment" to search them, click-to-download, and **content search**: when Tika is enabled (`tika_enabled`, already in the stack for Dovecot FTS), attachment text is extracted at index time and searchable cross live+snapshot, with per-attachment snippets. Without Tika, the view degrades to name/type/size and the content toggle does not render (copy-must-match-behavior either way).

**Out of scope (explicitly):** multiple named staging areas, per-tier quota UI, deep search over snapshot bodies, attachment *preview/rendering* in-app (download only).

## Decisions log (from brainstorm)

| Question | Decision |
|---|---|
| Push destination with mixed sources | Default: each message returns to its **origin mailbox**; optional override to a single destination. Chosen in the push panel. |
| Upstream folder placement | Default: **original folder** (created if missing); option "everything into `Restored/<date>`". Chosen in the push panel. |
| Curation surface | **Webmail (RW staging) + in-app preview** in the results pane. |
| Staging lifecycle | **One per user**, TTL + scheduled cleanup. |
| SaaS readiness | TTL and quota are first-class model fields with enforcement active from day one; **permissive defaults** (TTL 7 days, quota unlimited) so local/self-hosted deployments never notice. Env config now; moves to settings-in-DB with that refactor. |
| Attachment scope this cycle | Metadata + locator + dedicated view + download + **content extraction/search gated by `tika_enabled`** (user: "se facciamo la sezione per la ricerca degli allegati questa parte è implicitamente da fare"). One backfill pass covers both metadata and content — backfilling content later would mean re-reading every mailbox twice. |

## Architecture

### Data model (one Alembic migration, atomic with models)

**`mail_index.attachments`** — one row per attachment part:

| column | type | notes |
|---|---|---|
| `account_id` | String FK accounts.id CASCADE | composite PK part |
| `message_id_hash` | LargeBinary(20) | composite PK part — FK pair to `mail_index.messages` |
| `part_index` | Integer | composite PK part — index in the MIME walk |
| `filename` | Text | decoded (RFC 2047/2231) |
| `ext` | Text | lowercase, derived at insert ("pdf", "docx", "" if none) |
| `size_bytes` | Integer | decoded payload length |
| `content_type` | Text | as declared, informational only |
| `content_text` | Text nullable | Tika-extracted text, capped at 200 KB; NULL when Tika is off, extraction failed, or type not extractable |

GIN index on `to_tsvector('simple', coalesce(filename,'') || ' ' || coalesce(content_text,''))` for combined name+content search; snippets via `ts_headline`. (SQLite tests: plain LIKE fallback, same pattern as the messages tsv.)

The file locator lives on the existing `mail_index.messages` row (`folder_path`, `maildir_filename`) — attachments join to it; no duplication.

**`mail_index.messages`** gains `has_attachments` Boolean NOT NULL `server_default 'false'` (fast 📎 marker in message results without a join).

An attachment = a MIME part with a filename (from Content-Disposition or Content-Type name param). Inline images without filename do not count.

**`staging_areas`** — at most one per user:

| column | type | notes |
|---|---|---|
| `id` | String UUID PK | |
| `user_id` | String FK users.id CASCADE, **unique** | |
| `created_at` / `expires_at` | DateTime tz | `expires_at` = created + TTL |
| `max_bytes` | BigInteger NOT NULL `server_default '0'` | 0 = unlimited |
| `bytes_used` | BigInteger NOT NULL `server_default '0'` | maintained by service |

**`staging_messages`** — origin bookkeeping for each staged message:

| column | type | notes |
|---|---|---|
| `id` | String UUID PK | |
| `staging_id` | FK staging_areas.id CASCADE | |
| `source_account_id` | String FK accounts.id | push-to-origin target |
| `message_id_hash` | LargeBinary(20) | |
| `original_folder` | Text | for folder_mode=original |
| `staged_filename` | Text | basename inside the staging Maildir |
| `size_bytes` | Integer | |
| `staged_at` | DateTime tz | |

The staging **Maildir is the source of truth for contents** (webmail deletions just remove files); rows exist for quota, origin mapping and push. `reconcile()` drops rows whose file disappeared and recomputes `bytes_used`.

### Storage & Dovecot

- Staging Maildir: `{store_path}/.dovecot-home/{username}/staging/` (same root the Lua userdb already serves), uid/gid 1000.
- **Lua userdb** (`config_generator._dovecot_lua_userdb` + internal API): when the user has an active staging area, the userdb response includes one extra namespace: `namespace/mfb_staging/prefix = Staging/`, `mail_path = .../staging`, `separator = /`, listed, not inbox.
- **ACL**: the generated global `dovecot-acl` keeps `* owner lrs` and gains:
  ```
  Staging owner lrwstie
  Staging/* owner lrwstie
  ```
  (read, write flags, write-deleted, insert, expunge — delete works in webmail; no mailbox create/delete/admin.) Everything else stays read-only.

### Services

**`index_service`** — during the existing per-file header parse, MIME-walk the message: set `has_attachments`, insert `mail_index.attachments` rows. When `tika_enabled`: POST each attachment's decoded bytes to Tika (`{tika_url}/tika`, `Accept: text/plain`) with hard caps — parts > 20 MB skipped, 10 s timeout per part, result truncated to 200 KB — and store `content_text`. Extraction failures are logged and leave `content_text` NULL; **they never fail indexing or sync** (same contract as every other index path). New CLI `mfb index backfill-attachments [account_id]`, resumable + idempotent (delete-and-reinsert per message), covering metadata and content in a single pass. No change to snapshot bookkeeping (attachment rows describe the message; `snapshot_messages` already says where it exists).

**`staging_service`** (new):
- `get_status(db, user)` — count/bytes/expiry/quota (creates nothing).
- `add_messages(db, user, items)` — items = `[(account_id, message_id_hash), …]`; visibility check per account; **quota check first** (413-style error with clear message); copy each message file into the staging Maildir — live file via `messages.folder_path/maildir_filename` under the account's Maildir, else targeted extraction from the newest snapshot containing it (restic, bounded to that path); insert rows, bump `bytes_used`; get-or-create the area (TTL clock starts at creation).
- `reconcile(db, staging)` — walk staging Maildir vs rows (run before status/push).
- `empty(db, user)` — delete files + rows + area.
- `cleanup_expired(db)` — scheduler job (every 15 min, always on — the enforcement path is exercised even in dev).
- `push(db, user, destination, folder_mode)` — reconcile, group survivors by target account (`destination="origin"` → per-row `source_account_id`; else the override account id), create one restore job per target with mode `staging_push`, return job ids. **Each job removes its own staged messages on success** (completion hook); a failed job leaves its messages staged for retry. The area itself is deleted when it becomes empty.

**`restore_worker`** — new mode `staging_push`: source = staged files read **directly from disk** (no source IMAP connection), target = upstream IMAP APPEND, reusing the existing retry/append/skip-duplicates plumbing. Folder per message: `original_folder` or `Restored/<YYYY-MM-DD>` per `folder_mode`; create-if-missing reuses existing logic.

**`search_service`** — `search_messages` response gains `has_attachments` + `attachments: [{filename, ext, size_bytes}]` per hit (single aggregate join). New `search_attachments(db, user, query, account_ids, exts, min_size, max_size, include_content, page, page_size)` → rows from `mail_index.attachments` joined to messages for subject/from/date/folder + live/snap presence, ordered by date. Matching: filename always; plus `content_text` tsv match when `include_content` (only honored if `tika_enabled`); content hits return a `ts_headline` snippet per attachment.

**Preview** — `GET /api/restore/preview/{account_id}/{message_id_hash}` (hash hex-encoded in URLs, here and below): visibility check; live → read the Maildir file directly; snapshot-only → bounded restic dump; parse headers + first ~2 KB of the text/plain part (text/html stripped as fallback); return JSON. No IMAP session needed.

**Download** — `GET /api/restore/attachments/{account_id}/{message_id_hash}/{part_index}/download`: visibility check; locate file (live, else newest snapshot via restic dump); `BytesParser` → walk to `part_index` → decoded payload as `StreamingResponse`. **Security:** always `Content-Disposition: attachment` with RFC 6266-sanitized filename; `Content-Type: application/octet-stream` (never the declared type — an attached HTML/SVG must not execute on our origin); ids resolve server-side, no client-supplied paths.

### API surface (all under existing auth + account-visibility rules)

| Endpoint | Purpose |
|---|---|
| `POST /api/restore/search` | UI switches to it; `account_ids: null` = all visible |
| `POST /api/restore/attachments/search` | attachment preset |
| `GET /api/restore/attachments/{acct}/{hash}/{part}/download` | download |
| `GET /api/restore/preview/{acct}/{hash}` | preview pane |
| `GET /api/restore/staging` | status (count, bytes, expires_at, quota) |
| `POST /api/restore/staging/items` | add selected |
| `DELETE /api/restore/staging` | empty |
| `POST /api/restore/staging/push` | `{destination: "origin"\|<account_id>, folder_mode: "original"\|"restored"}` |

Audit log actions: `staging.add`, `staging.empty`, `staging.push`, `attachment.download`.

### Configuration

- `MAILFALLBACK_STAGING_TTL_MINUTES` (default `10080` = 7 days)
- `MAILFALLBACK_STAGING_MAX_BYTES` (default `0` = unlimited)

Interim env-var debt, to fold into the settings-in-DB refactor like the rest of tier-2 config. Attachment content extraction adds **no new config**: it keys off the existing `tika_enabled` / `tika_url` (one knob governs FTS and attachment indexing alike — per-tier gating later becomes a settings-in-DB question).

### UI (match the two reference screenshots)

**Workspace (single-mail preset):** scope select "All mailboxes (N)" first in the search row (defaults to all; individual account selectable); result rows add mailbox badge, 📎 marker and attachment chips (`name.ext · size`, ext accented); right-hand **Preview panel** fills on row click (headers, body snippet with fade, attachments box, "Add to staging" + "Open full in webmail" — the webmail button renders only when the message has a live source, since snapshot-only mail is not in any webmail namespace); **staging bar** docked bottom (count · bytes · TTL · quota; "Apri in webmail" → Roundcube `?_task=mail&_mbox=Staging`; "Svuota" with confirm; "Push upstream →" opens the panel with the two radio groups and confirm). Push progress reuses the existing restore status strip.

**"An attachment" preset (new chip, second position):** search row + type filter chips (PDF / Doc / Foglio / Immagine / Archivio / Altro → ext groups) + size chip; results table: type icon, `name.ext` (clickable = download), content snippet with highlighted hits (when content search ran), containing message (subject + mailbox/live/snap badges, sender, folder), size, date, actions "Anteprima msg" (opens the same preview) and "📥 Staging". The "Cerca anche nel contenuto" toggle renders **only when `tika_enabled`** (default ON when visible); with Tika off it is absent, not disabled.

**Responsive:** below 768px the two panels stack (preview becomes an inline expand under the selected row); the staging bar spans full width.

**Destination select** disappears from the single-mail sidebar (replaced by push-time choice); folder/full presets keep theirs.

## Error handling

- Quota exceeded on add → explicit error toast with current usage vs limit; nothing partially copied (check before copy; per-message copy failures roll back that message's row).
- Restic extraction failure for one message → that message reports failed, others proceed; staged set remains consistent via reconcile.
- Push job failures leave their messages in staging (retry possible); partial success reported per job in the status strip.
- Preview/download of a message whose file vanished (sync deletion between index and click) → 404 with friendly message, index row marked deleted on next sync pass (existing behavior).
- Staging expired between page load and action → 410-style error, UI refreshes the bar.

## Testing

- **index_service**: MIME fixtures — single/multiple attachments, RFC 2047/2231 filenames, inline image without filename (not counted), nested multipart; backfill idempotence. Tika extraction (HTTP mocked): success, timeout, oversized part skipped, failure leaves NULL and indexing proceeds; everything skipped when `tika_enabled` is false.
- **staging_service**: quota deny, TTL cleanup, reconcile after out-of-band file deletion, push grouping (origin vs override), folder modes, empty-after-success / keep-after-failure.
- **restore_worker staging_push**: APPEND grouping, folder creation, skip-duplicates (mocked IMAP, as today).
- **routers**: visibility 403/404 on all new endpoints; download headers (Content-Disposition, octet-stream); preview live vs snapshot (restic mocked).
- **config_generator**: ACL lines, Lua userdb staging namespace fields.
- Existing suites must stay green; sqlite-compat for new columns (ARRAY/JSON variant pattern already in use).

## Phases (implementation plan will detail)

1. **Attachment index** — models + migration, MIME walk in index_service, backfill CLI, search response enrichment.
2. **Cross-account search + preview** — UI scope select + badges + chips, preview endpoint + panel.
3. **Staging backend** — models (same migration as 1), staging_service, Dovecot namespace + ACL, status/add/empty endpoints, scheduler cleanup.
4. **Push** — restore_worker mode, push endpoint + panel, staging bar UI, webmail link.
5. **Attachment preset + download** — attachments search endpoint + view (name/type/size) + download.
6. **Content extraction** — Tika client in index_service (caps + failure tolerance), content matching + snippets in search and view, toggle gated by `tika_enabled`, backfill covers content.
7. **Polish + live verification** — compare against both reference screenshots at 1440/768/420, dark+light, gemini critic pass.

Each phase lands green (tests + lint) and independently shippable; 1–2 are valuable even if later phases slipped a cycle.
