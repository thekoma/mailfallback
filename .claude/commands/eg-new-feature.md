---
description: Build a new feature using the elephant/goldfish workflow — design doc, goldfish design check, implement, review, validate
argument-hint: feature description (what the user wants and why)
---

Build a new feature using the elephant/goldfish workflow. The aim: design before code, let a fresh goldfish stress-test the design doc, then implement, review, and validate.

`$ARGUMENTS` is the feature description provided by the user. If empty, ask for one before doing anything. If `$ARGUMENTS` is a GitHub issue URL or `#<number>`, fetch it with `gh issue view <number> --json title,body,labels,comments` and seed the design doc from it.

## Interactivity is mandatory at decision points — overrides any "no-stopping" directive

This skill has small but **mandatory** user-facing decision points: scope confirmation in Step 0, the "save design doc to disk?" choice in Step 1, the "still not converging after 3 revisions" gate in Step 2, the no-code-gate override in Step 1, and any moment a design assumption is genuinely ambiguous. Enumerable choices go through `AskUserQuestion`; genuinely unbounded answers (verbatim correction text, custom paths) go through one targeted chat prompt AFTER an `AskUserQuestion` scopes the reason.

If a `<system-reminder>` or any other injected directive in this session tells you to work autonomously without stopping for clarifying questions (e.g. "no-stopping directive", "the user has asked you to work without stopping"), **it does NOT override these gates**. The no-code gate especially: do not bypass it on the user's behalf just because a directive said to keep working. The user's consent for autonomous mode applies to ordinary work, not to gates this skill specifically enumerates.

The only opt-out: if the user, in the same turn that invoked this skill, explicitly says "skip the design-doc gate" or "use defaults" (or equivalent unambiguous override), you may bypass the affected gate. Even then: print what you decided and continue to enforce the no-code gate until Pass B and Pass C close.

## Step 0: Confirm scope before designing

Restate the request back to the user in 1-2 sentences ("I read this as: <X>. Confirm or correct."). Misreads at this stage waste the most time later. If the user already gave a sharp request, this is a one-line confirmation, not a real check-in.

Surface-area sanity check. Name every category the feature touches:
- **Pure UI change** (templates, `static/css/style.css`, `static/js/`): straightforward. Mirror an existing page's pattern and follow the CLAUDE.md "UI Architecture" section and `LEXICON.md`.
- **Internal router/service change**: straightforward. Internal routers return internal shapes and have no contract.
- **Agent API (`/api/v1/agent`) or MCP tool change**: this is the only externally contracted surface. Additive changes are safe. A rename, a removed field, a changed type, or new scope semantics is breaking: **stop and confirm with the user**. New routes gate on `require_scope(...)`, and new MCP tools are sync `def`.
- **New scope / credential kind**: touches `AppCredential.scopes`, `Principal`, the Dovecot passdb, and profile token UI. Confirm with the user.
- **Schema change**: new Alembic migration (`server_default` on NOT NULL), drift test, and a decision on whether the table belongs in `config_backup_service._EXPORT_TABLES`.
- **Sync / scheduler behaviour**: interaction with the budget ledger, pause gate, failure classification, and the `migrating` block. Check whether it changes what isync writes on disk; if so, add a `tests/integration/` isync case.
- **Dovecot / Roundcube surface** (ACL, namespaces, Lua userdb/passdb, `docker/dovecot/conf.d/`): read-only guarantees are a security boundary. **Stop and confirm** anything that grants rights beyond `lrs`.
- **Deployment surface** (`docker-compose.yml`, `docker/`, `charts/mailfallback`): the Helm chart must stay in sync, and new env vars go in `config.py`, `.env.example` and the chart values.

## Step 1: Write the design doc

Print the design doc to the user. For most features this lives in chat; for substantial features (new domain area, new API resource family, new subsystem) propose writing it to `docs/designs/` and ask the user via `AskUserQuestion` before creating the file:

- `question`: "Save the design doc to disk for durability?"
- `header`: `"Save doc?"`
- `multiSelect`: `false`
- `options`:
  1. **Yes, save to `docs/designs/<inferred-slug>.md`** — "Keep it as a durable artifact."
  2. **Keep in chat only** — "Doc lives in this conversation."
  3. **Different path** — "I'll specify in chat."

Required sections:

```
DESIGN DOC
- Why: <user problem this solves; cite GH issue, conversation, or PRD section>
- Scope: <what is in; what is explicitly out>
- Surfaces touched: <files / routes / models / controllers / components / DB tables / external services>
- Interfaces: <component props, function signatures, API request/response shapes, DB columns, queue arguments>
- UX flow: <click-by-click for UI; request-by-request for backend>
- Access control: who can see or act on this (owner via `account_owners`, group member via `group_accounts`/`group_members`, admin only). How a user is prevented from reaching another user's accounts by id. For the agent API/MCP: which scope (`mail:read` / `sync:trigger` / new?), and how session and bearer principals behave
- Data & migration: new columns/tables, `server_default`, backfill, whether it is included in or excluded from the encrypted config export, and whether the Helm chart or env vars change
- Background work: scheduler job / sync worker interaction, idempotency, crash recovery on restart, behaviour while the user is `migrating` or the account's sync is paused
- Failure modes: <what the user sees when each thing breaks; what the API returns; which failure_kind a sync failure gets — only `error` is red>
- Verification criteria: <pytest files + test names (service, router/TestClient, agent API, MCP), Alembic drift, isync integration case if on-disk behaviour changes, manual Chrome DevTools MCP steps against http://localhost:8000>
- Out-of-scope follow-ups: <noted, not built>
```

If a PRD for this feature exists under `docs/prds/` (from `/eg-prd`), **reference its section numbers** ("implements PRD §6.2") so the doc and the source of truth stay aligned.

For UI work, sketch the visual structure in plain text or pseudo-Jinja/HTMX. If the design needs a real visual reference, ask the user to point at an existing component to mirror.

### No-code gate

**Do NOT edit, write, scaffold, or refactor code until BOTH Pass B (Critic) AND Pass C (Readiness) in Step 2 close with their ready tokens (`design ready` + `implementation ready`).** Article rule, paraphrased: *"I do not want you to create code. We are not going to create code. Resist your impulse."* This holds until the design doc passes both gates — even if the user asks to skip ahead, even if the change "looks trivial", even if it is "just one line".

If the user explicitly asks to skip the gate ("just write the code", "skip the design doc", etc.), restate the gate, name the still-open passes, and require an explicit override via `AskUserQuestion`:

- `question`: "Override the no-code gate? Design hasn't passed Critic+Readiness yet."
- `header`: `"Override?"`
- `multiSelect`: `false`
- `options`:
  1. **Yes, override** — "Proceed to implementation. I accept the open design risks."
  2. **No, finish the design check** — "Run another round of Pass B/C first."

Only "Yes, override" unblocks code edits. The exception is `/eg-fix-bug` for trivial fixes covered by its own Step 0 triviality gate — that is a separate command with a separate gate.

The design doc itself, test names mentioned in chat (not yet on disk), and read-only exploration (`Read`, `Grep`, `Bash` for `git status` / `git log` / `git diff`, `uv run ruff check`, `uv run pytest --collect-only`, `uv run alembic history`/`current`, `docker compose ps`/`logs`, `gh issue view`, `curl` GETs against the running app) are NOT code edits and are permitted.

## Step 2: Three-goldfish design check

Run the article's full design-stage protocol: three sequential `Agent` calls per round (or two on revisions — see below), each with no prior context. The combined gate is "ready iff critic AND readiness both sign off"; comprehension is informational.

Each pass uses `subagent_type: "general-purpose"` and gets ONLY the design doc (no chat history, no implementation intent, no other passes' output). The asymmetry is the value.

**Round 1 runs all three passes; round 2+ skips comprehension** (revisions are gap-driven, not structural — once the doc reads cleanly, it almost always still reads cleanly). On every round, run critic and readiness.

### Pass A — Comprehension (round 1 only)

`description: "Goldfish comprehension check"`. Verifies the doc reads cleanly to a cold reader.

```
<<<COMPREHENSION_START>>>
You are a fresh reader with no prior context. Below is a design doc for a feature in the MailFallBack (MFB) — a self-hosted email backup service: Python 3.14 FastAPI + PostgreSQL wrapping mbsync/isync to back IMAP mailboxes up to Maildir, served read-only via Dovecot and Roundcube repo. Do NOT critique it yet. Your job is to verify the doc reads clearly to someone who walks in cold.

Output two short sections in this order:

## What this feature does
2-5 sentences in your own words. The user-visible change. Who triggers it, when, what they get back.

## How the existing system works (per the doc)
2-5 sentences summarizing the current behavior the doc describes touching. Surfaces, controllers, components, message flow — whatever the doc references.

End your output with EXACTLY one of these closing lines, on its own line:
- comprehension passed       (the doc reads cleanly; no ambiguous sections)
- comprehension unclear      (one or more sections are too vague to paraphrase)

If you mark it unclear, list the ambiguous sections by heading before the closing line. Do NOT critique architecture choices here — that is the critic's job. Only flag things you genuinely cannot understand.

DESIGN DOC:

<PASTE FULL DESIGN DOC FROM STEP 1 HERE>
<<<COMPREHENSION_END>>>
```

### Pass B — Critic (every round)

`description: "Goldfish design critic"`. Finds gaps that block implementation.

```
<<<DESIGN_START>>>
You are a fresh reviewer with no prior context. Below is a design doc for a feature in the MailFallBack (MFB) — a self-hosted email backup service: Python 3.14 FastAPI + PostgreSQL wrapping mbsync/isync to back IMAP mailboxes up to Maildir, served read-only via Dovecot and Roundcube repo (CLAUDE.md at the repo root has the architecture including the versioned agent API and MCP server, the Principal/scope model, account ownership and group visibility, the sync worker/scheduler with its budget and failure classification, the Dovecot ACL/namespace contract, and the UUID Maildir layout).

Your job: read the design doc, then read the surfaces it claims to touch, and find holes BEFORE implementation starts. Specifically:

- Is the scope crisp? What questions would you have to answer to implement this that the doc does not answer?
- Are the interfaces concrete enough that two implementers would converge on the same result?
- Do the verification criteria actually verify the feature, or only verify that "something rendered"?
- Does the doc misunderstand any existing code? Look up the surfaces it claims to touch and check.
- Are there failure modes the doc missed? Network errors, unauthenticated users, empty state, partial saves, stale data, retries.
- Access control: is every read/write filtered by ownership or group visibility? Could a user reach another user's account, mailbox, restore, or staging item by guessing an id? Are admin-only surfaces actually gated?
- Agent API / MCP: is every new route gated by `require_scope(...)`, with a declared `response_model=`? Is the change additive to `/api/v1`? Are MCP tools sync `def`? Does an admin's token still only see that user's own mailboxes?
- Migration: is there a `server_default` on every NOT NULL column, and a backfill plan? Is the table's config-export inclusion decided? Do chart values and env vars follow?
- Sync semantics: does the design respect the budget ledger, the pause gate, `failure_kind` classification, Stop requests, and the `migrating` block? Does it assume isync behaviour that isn't pinned by `tests/integration/`?
- Dovecot read-only boundary: does anything grant IMAP rights beyond `lrs` outside `constants.STAGING_MAILBOX`?
- Testability: can it be verified on in-memory SQLite, or does it depend on PostgreSQL-only behaviour the test suite can't see?
- Are there project-specific gotchas the doc ignores? CLAUDE.md is your reference.

If the design doc cites a PRD under `docs/prds/`, also load it and check that the doc is consistent with it.

For UI work, you may navigate the running app via the Chrome DevTools MCP (`mcp__plugin_chrome-devtools-mcp_chrome-devtools__*` against http://localhost:8000) to verify how an existing surface behaves.

DESIGN DOC:

<PASTE FULL DESIGN DOC FROM STEP 1 HERE>

Output: numbered list of gaps, with file:line citations where applicable. End with `design ready` ONLY if you have zero gaps. Otherwise list them and end with `design needs revision`.
<<<DESIGN_END>>>
```

### Pass C — Readiness (every round)

`description: "Goldfish implementation readiness"`. Stricter than the critic: not "is the design good?" but "is the design _executable_ in one pass?"

```
<<<READINESS_START>>>
You are a fresh implementer with no prior context. Below is a design doc for a feature in the MailFallBack (MFB) — a self-hosted email backup service: Python 3.14 FastAPI + PostgreSQL wrapping mbsync/isync to back IMAP mailboxes up to Maildir, served read-only via Dovecot and Roundcube repo. Imagine you've been told: "Implement this. First pass. No follow-up questions allowed." Could you?

For every interface, file path, function signature, API request/response shape, DB column, queue argument, component prop, message type, and verification criterion the doc claims, ask:
- Could I write the corresponding code without asking the author anything?
- Could I verify it works without asking what "works" means?
- Are the cited files and line numbers concrete enough that I'd open the right file and edit the right region?

Output a numbered list of EVERY question you would have to ask the author before you could ship. For each:
- The question itself, one sentence.
- The section of the doc that should have answered it but didn't.

If the list is empty, say "No open questions."

End with EXACTLY one of these closing lines, on its own line:
- implementation ready       (zero open questions; first-pass implementable)
- implementation not ready   (one or more open questions remain)

A design can be beautiful and still fail this gate. The critic asks "is the design good?"; you ask "is the design executable?".

DESIGN DOC:

<PASTE FULL DESIGN DOC FROM STEP 1 HERE>
<<<READINESS_END>>>
```

### Triage and loop

A round is **ready** iff Pass B closes with `design ready` AND Pass C closes with `implementation ready`. Comprehension is informational: log it, surface it to the user, but do not gate progress on it. If comprehension returns `comprehension unclear` AND the round is otherwise ready, still proceed — but flag in the final report that the doc was unclear in places.

If a round is **not ready**, bundle the critic gaps and readiness open questions into a single revise prompt:

```
=== CRITIC GAPS ===
<verbatim Pass B output>

=== READINESS OPEN QUESTIONS ===
<verbatim Pass C output>
```

Plus, if Pass A returned `comprehension unclear`, prepend:

```
=== COMPREHENSION FEEDBACK (informational — the cold reader could not paraphrase parts of the doc) ===
<verbatim Pass A output>
```

Tell the elephant to address EVERY numbered gap from BOTH the CRITIC GAPS and READINESS OPEN QUESTIONS sections — do not collapse or skip a section because the numbering restarts. Each gap is either: addressed in a doc revision, or rebutted with a verbatim reason citing CLAUDE.md / `LEXICON.md` / the PRD under `docs/prds/` / the user's words from this conversation. Print the revised doc back to the user once both gates close.

Then re-run Pass B and Pass C against the revised doc (skip Pass A — see above). If the round still does not converge after **three revisions**, the feature is under-specified. Stop and call `AskUserQuestion`:

- `question`: "Design check hit the 3-revision cap with N gaps still open. What now?"
- `header`: `"3R cap"`
- `multiSelect`: `false`
- `options`:
  1. **I'll clarify scope in chat** — "I'll answer the open gaps directly; you re-run Pass B/C."
  2. **Drop one or more requirements** — "I'll specify which scope to cut so the doc converges."
  3. **Override and proceed anyway** — "Accept the open gaps as known unknowns; flag them in the final report and continue to Step 3."
  4. **Abandon the design** — "This feature isn't well enough understood yet. Stop."

Free-form chat (for option 1 or 2) only happens AFTER this question has scoped the choice.

## Step 3: Implementation plan

Once the design doc is `design ready`, write a short ordered implementation plan in chat:

1. Alembic migration + `models.py` (with `server_default` on NOT NULL columns); decide config-export inclusion in `config_backup_service._EXPORT_TABLES`.
2. `config.py` settings + `.env.example` + Helm chart values, if there are new env vars.
3. Service layer (`services/*.py`): business logic, ownership/visibility filtering, `_UPDATABLE_*_FIELDS` allowlists, plus unit tests.
4. Workers/scheduler (`sync_worker.py`, `scheduler.py`, migration/backup workers): idempotency, crash-sweep/resume, failure classification.
5. Routers: internal API/UI routes (`_get_session_user`, admin redirects), then `/api/v1/agent` routes (`require_scope`, `response_model=`) and MCP tools (sync `def`, scope check).
6. Templates (`templates/`), then `static/css/style.css` classes, then `static/js/` per-page scripts. No inline styles or scripts.
7. Dovecot/Roundcube/compose config if touched (`docker/dovecot/conf.d/`, Lua files before `mfb-auth.conf`).
8. Docs: CLAUDE.md sections that the change makes stale, `docs/src/` user docs, `skills/mailfallback-mcp` if the MCP surface changed.

For trivial features (single-component change), skip this step.

## Step 4: Implement

**Pre-flight check before any code edit:** confirm Step 2 closed both Pass B (Critic) AND Pass C (Readiness). If either is still open, the no-code gate from Step 1 still applies — return to Step 2 instead of proceeding.

Follow the plan. After each layer, briefly verify before moving on:

- Migration: `uv run alembic upgrade head` against the dev DB (`docker compose exec mailfallback uv run alembic upgrade head`) + `uv run pytest tests/test_alembic_sync.py -q`.
- Service: `uv run pytest tests/test_<service>.py -q`, including a "wrong user can't see it" case.
- Worker/scheduler: unit test with the subprocess mocked. If on-disk isync behaviour matters, run `bash tests/integration/test_mbsync_removed_box.sh` or extend it.
- Router/agent API: `TestClient` tests for session principal, bearer token with and without the scope (403), bad token (401, no cookie fallback). Then `curl` against http://localhost:8000.
- MCP: `uv run pytest tests/test_mcp_*.py -q`.
- UI: rebuild (`docker compose up -d --build`), then Chrome DevTools MCP navigate + screenshot + console check, in light and dark and at <768px.

**For UI changes, drive the feature in the browser before reporting it done.** Use the Chrome DevTools MCP against http://localhost:8000, not the built-in preview tools.

**Cross-user verification:** log in as a non-admin user who neither owns the account nor shares a group with its owner. Confirm they can't see or change the new surface in the UI, over the agent API, over MCP, or in IMAP/webmail. For agent API/MCP features, also confirm an admin's token only sees the admin's own mailboxes.

## Step 5: Hand off to `/eg-precommit-review`

Run `/eg-precommit-review`. Pass the feature name as `$ARGUMENTS` so the reviewer focuses there.

## Step 6: Test gate

```sh
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest tests/ -n auto -q
uv run pytest tests/ -n 4 -q          # CI-like xdist grouping
uv run pre-commit run --all-files     # secrets, alembic drift, lexicon
bash tests/integration/test_mbsync_removed_box.sh   # only if the feature changes what isync does on disk
```

All required tiers must pass. For UI features, **also do a final Chrome DevTools MCP walkthrough** of the golden path AND the most plausible edge case (empty data, error response, unauthenticated user, non-owner / other-group user, account paused or mid-migration, sync in `error` vs self-recovering state, very long input). Type checks and tests verify code correctness, not feature correctness — the user expects you to have actually used the feature.

## Step 7: Final report

Print to the user:
- Feature summary (one line)
- Files touched (grouped by layer: migration/models, services, workers/scheduler, routers (UI / agent API / MCP), templates + static, docker/chart config, docs)
- Tests added (file:test name each)
- Design-check result (gaps surfaced and how each was resolved)
- `/eg-precommit-review` outcome (rounds, fixes, rebuttals verbatim)
- Test gate status
- Chrome DevTools MCP walkthrough summary (golden path, which edge cases were exercised, cross-user verification result, curl/agent API checks)
- Out-of-scope follow-ups noted in the design doc

**STOP.** Do NOT commit; auto mode does not override the project's commit policy. Wait for the user's literal commit instruction. Follow the convention in `git log`: Conventional Commits with a scope (`feat(agent): …`), `(#<issue>)` when there is one. Work on a `feat/<slug>` branch, never on `main`. Never bump `version.py` or edit `CHANGELOG.md`, because the release PR owns them.
