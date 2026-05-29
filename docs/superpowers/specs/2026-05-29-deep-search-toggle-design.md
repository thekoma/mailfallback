# Deep Search Toggle — Design

**Date:** 2026-05-29
**Branch:** `feat/mail-index`
**Status:** Approved (pending implementation plan)

## Problem

Body search through the index path returns fewer results than the legacy
`SEARCH TEXT`. Phase 1 indexes only `subject`, `from_addr`, `from_name`, and
`to_addrs` in the tsvector; Phase 2 only filters the Phase-1 *survivors* by body
— it never adds messages that match by body alone. Real example: searching
"google" on `andrea.cervesato@live.it` returns 5 hits via the index vs ~9 via
Roundcube's `SEARCH TEXT`. Header text (Received, X-Forwarded, Reply-To) and
footer text ("Powered by Google") are missed entirely.

## Decision

Adopt **(a) as default + (b) behind a "Deep search" toggle**:

- **Default** stays the fast Phase-1 tsv search (subject / from / from_name /
  to). Unchanged.
- **Deep search ON** runs Phase 1 tsv **UNION** a full-folder IMAP `SEARCH BODY`
  across every live folder of the selected account, closing the gap to
  legacy semantics.

The existing "Body" checkbox is **removed**. Its semantics (filter Phase-1
survivors only) were the incomplete behavior we are replacing; keeping it
alongside "Deep search" would be two confusing body-ish controls.

## Scope & Constraints

- **Live-only.** Deep search finds body matches by querying Dovecot via IMAP,
  which serves only live Maildir. Messages that exist only in a snapshot
  (pruned/deleted) have metadata in the index but their bodies are not served by
  Dovecot, so they are not body-searchable. They remain covered by Phase 1
  (subject/from/to). The UI shows a note: *"Deep search covers active mail
  only."*
- **No schema change.** Body-only matches are already rows in
  `mail_index.messages` (the index is populated from the live Maildir); they
  simply did not match the tsv. Deep search finds them by body and unions them
  in. Option (c) — indexing body in a tsvector — is explicitly **not** part of
  this work.
- **Soft timeout.** A configurable deadline (default 10s) bounds the per-folder
  IMAP loop. On timeout, return results collected so far plus a `partial=true`
  flag; the UI surfaces *"partial results — narrow the date range."*

## Data Flow (deep search, per selected account)

1. **Phase 1:** run the tsv query → set of index rows `R1`. When `deep=true`,
   fetch the **full** match set (not a single page) because the UNION must
   happen before pagination.
2. **Body search:** for each live folder of the account →
   `UID SEARCH BODY "<sanitized keyword>"` → matching UIDs. Then a single
   `UID FETCH <uids> (BODY[HEADER.FIELDS (MESSAGE-ID)])` → Message-Ids → hash →
   look up index rows `R2`.
3. **Union:** `R1 ∪ R2` deduplicated by `message_id_hash`; mark
   `body_matched=true` on `R2` members.
4. Apply filters (date range, `include_deleted`, snapshot scope), sort, then
   **paginate in memory** over the merged set.
5. The per-folder IMAP loop is wrapped by a deadline. If exceeded, stop, return
   what was collected, set `partial=true`.

Note: one `SEARCH BODY` per folder is *more* efficient than the current Phase 2,
which issued one SEARCH per candidate survivor.

## Components

- **`services/search_service.py`**
  - `search_messages(...)`: replace the `body: bool` parameter with
    `deep: bool`. Add the deep branch (full Phase-1 set + body union + timeout).
  - New helper `_dovecot_body_search_folder(account, query, deadline)` returning
    the set of `message_id_hash` that match in a folder (SEARCH BODY + FETCH
    Message-Id). Replaces the survivor-filtering use of `_dovecot_filter_body`.
  - Result dict: add `partial: bool`; keep `body_matched`; remove
    `phase2_skipped_count` (no longer meaningful).
- **`routers/restore.py`**
  - `RestoreSearchRequest.body` → `deep`.
  - Deprecated wrapper `WorkspaceSearchRequest.search_body` → `deep`; translate
    accordingly. Reuse the existing `_sanitize_imap_string` helper.
- **`config.py`**
  - Add `deep_search_timeout_seconds: int = 10`
    (env `MAILFALLBACK_DEEP_SEARCH_TIMEOUT_SECONDS`).
- **UI**
  - `templates/restore_workspace.html`: remove the "Body" checkbox; add a
    **Deep search** toggle placed prominently (outside the collapsed Filters
    panel). Add the live-only note and a partial-results banner.
  - `static/js/restore_workspace.js`: `filters.body` → `deepSearch`; payload
    `search_body` → `deep`; render the partial-results banner from the response.

## Error Handling & Edge Cases

- Per-folder IMAP errors are caught; the folder is skipped without failing the
  query (same as current Phase 2).
- Empty query + deep: deep does not run (nothing to body-search).
- Keyword sanitization strips CR/LF/control chars before IMAP (reuse
  `_sanitize_imap_string`).
- `MAILFALLBACK_USE_INDEX_SEARCH=false` → legacy mount-based fallback unchanged.

## Testing (TDD)

- UNION + dedupe by `message_id_hash`.
- Timeout → `partial=true` with collected results.
- Live-only: a snapshot-only message matching only by body does **not** appear
  among `body_matched` results.
- Keyword sanitization.
- Default (deep off) → Phase 1 behavior unchanged (no regression).
- Dovecot IMAP calls are mocked as in the existing Phase 2 tests.
