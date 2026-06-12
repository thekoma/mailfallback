# Restore Plan 3/3 — Attachment View, Download, Tika Content Search

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fourth workspace preset "An attachment" that searches attachments by name/type/size — and inside their content when Tika is enabled — with click-to-download, completing the restore cycle spec.

**Architecture:** `search_attachments` queries `mail_index.attachments` (filename always; `content_text` tsvector when enabled) joined to messages for context. Download resolves `(account_id, hash, part_index)` server-side, fetches the raw message via the battle-tested locator/snapshot path, re-walks MIME with the `part_index` contract, and streams the decoded part as `application/octet-stream`. Tika extraction rides the existing index/backfill walk (capped, failure-tolerant, gated by `tika_enabled`). Spec: `docs/superpowers/specs/2026-06-11-restore-staging-attachments-design.md`. UI contract: `.claude/mockup_attachments_view_reference.png` (frozen).

**Baseline:** branch `feat/restore-staging` @ `5e87c5e`, 777 tests green. New branch: `feat/restore-attachments-view` (stacked).

---

## Verified facts the implementers MUST honor (accumulated across Plans 1-2)

1. **`part_index` contract**: counts ALL non-multipart leaves in `msg.walk()` order (`index_service._parse_attachments`, pinned by `test_part_index_skips_body_and_is_stable`). The download endpoint re-walks with the SAME algorithm.
2. **FTS expression**: queries hit the GIN index ONLY via the exact `models.ATTACHMENTS_FTS_EXPR` (PG matches expression indexes structurally). SQLite tests: LIKE fallback (the messages-tsv pattern in `search_service.search_messages`).
3. **Carry-over fix (MANDATORY, from Plan-1 Task-4 review)**: `search_messages`'s attachment enrichment fetches whole `MailIndexAttachment` entities — once `content_text` is populated, every search page would drag extracted text into memory. Switch it to explicit column projection (`with_entities`/`db.query(cols)`) in this cycle's FIRST task.
4. **Locator stack**: `preview_service._locate_live_file` (INBOX both-bases + flag-rename fallback) and `_snapshot_bytes(db, account, row, max_bytes=...)` (capped dump + prefix locate drift recovery) are correct and tested — REUSE, do not reimplement. For download use `staging_service.STAGING_DUMP_MAX_BYTES` (100 MiB) and treat `len(raw) >= cap` as failure (the Plan-2 C1 lesson: caps must not silently truncate user-bound bytes).
5. **Access model**: visibility = ownership∪groups for everyone; admin escalation ONLY via `include_all` through `_workspace_account_for_user` (routers/restore.py) with audit. New endpoints follow it.
6. **Route order**: literal GET routes register ABOVE `GET /{job_id}` (guard comments at both sites).
7. **Pico paints `[role="button"]`** — use real `<button>`/`<a>` only.
8. **XSS trap (new, design-level)**: `ts_headline` output embeds match markers in text extracted from HOSTILE mail attachments. NEVER render snippets via `x-html`. Contract: the service returns snippets with `[[[`/`]]]` markers (custom StartSel/StopSel); the JS splits on the markers and builds text nodes + `<mark>` elements via `x-for`/template — no HTML injection path.
9. Audit actions to add: `attachment.download` (always), and the include_all escalation on attachment search reuses `restore.search_all` with `details.kind = "attachments"`.
10. Tika: `settings.tika_enabled` / `settings.tika_url` exist (drive Dovecot FTS today). Caps from the spec: skip parts > 20 MB, 10 s timeout per part, store first 200 KB of extracted text. Failures → `content_text` NULL, NEVER fail indexing/sync. The UI content toggle renders ONLY when `tika_enabled` (copy-must-match-behavior).

---

### Task 1: `search_attachments` service + projection carry-over fix

**Files:** `src/mailfallback/services/search_service.py`; tests in `tests/test_search_service.py`.

- [ ] Fix the Plan-1 carry-over FIRST (explicit columns in `search_messages`'s attachment fetch: `filename, ext, size_bytes, account_id, message_id_hash, part_index` — no `content_text`); existing tests stay green.
- [ ] TDD `search_attachments(db, *, user, query="", account_ids=None, include_all=False, exts=None, min_size=None, max_size=None, include_content=False, page=1, page_size=50) -> dict`:
  - scope exactly like `search_messages` (accessible ∪ admin include_all);
  - base query: `MailIndexAttachment` JOIN `MailIndexMessage` ON (account_id, message_id_hash) — explicit columns only;
  - `query`: PG → `text(ATTACHMENTS_FTS_EXPR) @@ plainto_tsquery('simple', :q)` when `include_content and settings.tika_enabled`, else `filename ILIKE %q%` (term-AND over whitespace-split terms); SQLite → LIKE on filename (+ content_text when include_content);
  - snippet: PG + content mode → `ts_headline('simple', coalesce(content_text,''), plainto_tsquery(...), 'StartSel=[[[,StopSel=]]],MaxWords=18,MinWords=8')` only for rows whose content matched; else None. SQLite → None (UI hides);
  - filters: `ext.in_(exts)` when given; size range on `size_bytes` (NULL sizes excluded by size filters, included otherwise);
  - order: message `date_sent DESC NULLS LAST`; total + page;
  - each hit: `{account_id, message_id_hash (hex), part_index, filename, ext, size_bytes, content_snippet, subject, from_addr, folder_path, date_sent (iso|None), alive_in_live, snapshots (ids), has_live_or_snap...}` — reuse the snapshot-presence batch pattern from `search_messages`.
  - Tests: filename match; ext/size filters; content match via a row with content_text (PG path can't run on SQLite — test the SQLite LIKE path + mark the expression-vs-constant equality assertion (`ATTACHMENTS_FTS_EXPR` appears verbatim in the compiled PG SQL) with a compile-only test using `dialect=postgresql` statement compilation, no PG needed); include_content ignored when tika disabled (monkeypatch settings); scoping (foreign account invisible; admin+include_all sees it).
- [ ] Suite + ruff; commit `feat(search): attachment search with optional content matching`.

### Task 2: API — attachments search + download endpoints

**Files:** `routers/restore.py`, `services/audit_service.py` (label), tests `tests/test_attachments_api.py`.

- [ ] `POST /api/restore/attachments/search` (above the catch-all): pydantic model mirroring the service params (typed, no bare dicts — Plan-2 Task-5 lesson); `include_content` honored only when `settings.tika_enabled` (else forced False, response carries `"content_search_available": settings.tika_enabled`); include_all escalation audited via `restore.search_all` with `details={"kind": "attachments", "query": ...}` once per honored request.
- [ ] `GET /api/restore/attachments/{account_id}/{message_id_hash_hex}/{part_index}/download`: account via `_workspace_account_for_user` (404/escalation per house rules; `include_all` query param); row lookup in `mail_index.attachments` (404 if absent); raw message via `_locate_live_file` else `_snapshot_bytes(max_bytes=STAGING_DUMP_MAX_BYTES)` (len>=cap → 502 "attachment too large to extract"); `BytesParser(policy=default).parsebytes` → walk counting non-multipart leaves to `part_index` (404 if walk ends first — MIME changed is impossible per immutability, but belt+braces); `payload = part.get_payload(decode=True)` (None → 404); response: `Response(content=payload, media_type="application/octet-stream", headers={"Content-Disposition": ...})` with RFC-6266 filename: ASCII fallback (non-ASCII → underscore) + `filename*=UTF-8''<percent-encoded>`; quotes/control chars stripped. ALWAYS octet-stream (attached HTML/SVG must not execute on our origin). Audit `attachment.download` (resource_id=account.id, details={filename, message_id_hash, part_index, source: live|snapshot}).
- [ ] Tests: owner 200 + exact headers (Content-Disposition both forms, octet-stream) + payload bytes round-trip (build a real Maildir message with a known attachment, index it, download it); non-owner 404; admin include_all 200 + TWO audit rows (escalated preview-style? no — download always audits: assert the attachment.download row; the escalation needs no second row); bad hex 400; unknown part 404; snapshot-only path (restic mocked, drift-tolerant via the existing helpers — one test); oversize → 502.
- [ ] Suite + ruff; commit `feat(restore): attachment download endpoint with audit`.

### Task 3: Tika extraction at index time + backfill content mode

**Files:** `services/index_service.py`, `src/mailfallback/cli/index.py` (+argparse), tests `tests/test_index_attachments.py`.

- [ ] TDD `_extract_attachment_text(payload: bytes, content_type: str) -> str | None` in index_service: `httpx.put(f"{settings.tika_url}/tika", content=payload, headers={"Accept": "text/plain", "Content-Type": content_type or "application/octet-stream"}, timeout=10.0)` (VERIFY tika's HTTP verb — the Dovecot fts config uses PUT `{tika_url}/tika/`; mirror what works: PUT on /tika with the trailing slash exactly as `fts_decoder_tika_url` does); 200 → `.text[:204_800]` (200 KB), anything else/exception → None with `logger.debug`. Module-level `TIKA_MAX_PART_BYTES = 20 * 1024 * 1024`, `TIKA_TEXT_CAP = 204_800`.
- [ ] Wire into `_parse_attachments`: when `settings.tika_enabled` and payload is not None and `len(payload) <= TIKA_MAX_PART_BYTES` → `content_text = _extract_attachment_text(...)`; else None. (The decoded payload is already in hand for size — zero extra I/O.) Tests mock `index_service.httpx` (success → stored truncated; timeout exception → None + row still created; oversized part skipped without HTTP call; tika_enabled False → no HTTP call at all). The existing no-Tika tests must stay green unchanged (default settings have tika_enabled False in tests — VERIFY, else monkeypatch).
- [ ] Backfill content mode: `mfb index backfill-attachments <account_id> --content-only` → `index_service.backfill_attachment_content(db, account_id) -> int`: iterates DISTINCT messages having attachment rows with `content_text IS NULL` (and `tika_enabled` — refuse with a clear message otherwise), locates the file (both-bases; skip-if-missing like the metadata backfill), re-walks, extracts ONLY for the NULL rows (match by part_index), commits per BATCH_SIZE. Resumable by construction (NULL → filled). Tests: fills NULLs, leaves filled rows untouched, skips missing files without marking anything, refuses when tika disabled.
- [ ] Suite + ruff; commit `feat(index): tika content extraction with caps and content backfill`.

### Task 4: UI — "An attachment" preset

**Files:** `templates/restore_workspace.html`, `static/js/restore_workspace.js`, `static/css/style.css`, `routers/ui_restore.py` (context flag `tika_enabled`), tests `tests/test_restore_ui.py`.

Visual contract: `.claude/mockup_attachments_view_reference.png` — chip "An attachment" in SECOND position; type filter chips (PDF / Documents / Sheets / Images / Archives / Other); size chip ("> 1 MB" toggle); content toggle "Search inside attachments — extracted via Tika" ONLY when tika enabled (default ON when visible); results table: type icon (lucide: file-text/file-spreadsheet/image/archive/file), filename.ext as a real `<a>` download link (href = the download endpoint + `?include_all=` when admin-escalated), snippet line with `<mark>` highlights, containing-message cell (subject + account/live/snap badges + sender + folder), size (fmtSize), date, actions: "Preview" (reuses openPreview with a result-shaped object) and "Add to staging" (reuses addToStaging — items shape already matches).

- [ ] Context: `tika_enabled` flag into the page (template global or context var — follow how `webmail_enabled` flows).
- [ ] Template: new preset chip `attachment` between single-mail and folder; the preset's panel reuses the search row (scope select + admin toggle + query input are SHARED — they live outside the preset templates; verify and keep) + its own filter chip row + results table + the SAME staging bar. Ext groups (JS constant): pdf=[pdf]; doc=[doc,docx,odt,rtf,txt]; sheet=[xls,xlsx,ods,csv]; image=[jpg,jpeg,png,gif,webp,heic,svg]; archive=[zip,rar,7z,tar,gz]; other = null (no ext filter) — "Other" chip sends `exts: []`? NO: Other = NOT IN all-known — implement server-side? KEEP SIMPLE: Other chip omitted from v1 if the service has no NOT-IN support — DECISION: service accepts `exts` list only; UI chips are multi-select toggles over the known groups; no chip selected = all attachments. Drop "Other" (mock shows it, but copy-must-match-behavior wins — note the deviation for the report).
- [ ] JS: `attResults`, `attTotal`, `attSearching`, `attFilters {groups: Set, minSize: null|1048576, includeContent: tikaEnabled}`, `runAttachmentSearch()` (page_size 100, statusText pattern "N of M"), snippet renderer per the XSS contract: split on `[[[`/`]]]` into segments, alternate text/mark via `x-for` over a parsed array — NO x-html. Download links are plain anchors (no fetch). Preview/staging actions delegate to the existing methods (verify openPreview tolerates the attachment-hit shape: it needs account_id + message_id_hash — pass a minimal object).
- [ ] CSS: filter chips reuse `.ws-chip` sizing (smaller variant `.ws-fchip`), table styles consistent with the workspace card look (`.ws-att-table`), `<mark>` themed via `--ws-cyan-soft` background.
- [ ] Template tests: preset chip + table markup render; content toggle present when tika flag true, ABSENT when false (both ways, like the webmail gating test).
- [ ] Suite + ruff + node --check; commit `feat(ui): attachment search preset with download and content snippets`.

### Task 5: Live verification + critic (controller-run)

- [ ] Rebuild from worktree; tika container reachable (`curl tika:9998/tika` from app container — version line); `tika_enabled` true in the dev env (check `.env`; if false, enable for the session and note it).
- [ ] Backfill content: `mfb index backfill-attachments <both accounts> --content-only`; record counts + a `SELECT count(*) FROM mail_index.attachments WHERE content_text IS NOT NULL`.
- [ ] Browser e2e: attachment preset → search a term known to appear INSIDE a PDF (e.g. an invoice number from the Google Workspace fattura) with content ON → hit with snippet + mark; content OFF → no hit (name-only); download the PDF → file bytes land (verify via fetch + length + magic bytes `%PDF`), headers correct; audit row exists; ext filter chips narrow correctly; "Add to staging" from the attachment row works (bar count bumps).
- [ ] Screenshots dark/light 1440 + 420 vs the frozen attachments mockup; gemini critic; fix in-scope findings.
- [ ] Full suite + cleanup (temp admin etc.); push branch; stacked PR (base feat/restore-staging).

---

## Self-review notes

- Spec coverage: phase 5 (T1-T2, T4), phase 6 (T3 + content bits of T1/T4), phase 7 (T5). Out-of-scope guard: no in-app attachment rendering (download only), no "Other" type chip (disclosed deviation).
- Type consistency: hits carry `message_id_hash` hex + `part_index` — the download URL and staging items both consume them; `exts` lowercase no-dot everywhere (the model's documented `ext` contract).
- The snippet marker contract ([[[/]]]) appears in T1 (producer) and T4 (consumer) identically.
