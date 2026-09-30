---
description: Fix a bug using the elephant/goldfish workflow — problem doc, goldfish diagnosis check, failing test, fix, review, validate
argument-hint: bug description, GitHub issue URL, or symptom + repro
---

Fix a bug using the elephant/goldfish workflow. The aim: write a problem doc, goldfish-check the diagnosis (so we are not anchored to the first hypothesis), capture the bug as a failing test, fix it, then run `/eg-precommit-review` and the test gate.

`$ARGUMENTS` is the bug description provided by the user. If empty, ask for one before doing anything. If `$ARGUMENTS` is a GitHub issue URL or `#<number>`, fetch it first with `gh issue view <number> --json title,body,labels,comments` and seed the problem doc from it.

## Interactivity is mandatory at decision points — overrides any "no-stopping" directive

This skill is mostly autonomous, but it has **mandatory** user-facing decision points: the missing-repro stop in Step 1, the goldfish-vs-elephant divergence resolution in Step 2, and any moment where the bug shape is genuinely ambiguous. Enumerable choices go through `AskUserQuestion`; genuinely unbounded answers (repro steps in prose, custom log lines) go through one targeted chat prompt AFTER an `AskUserQuestion` scopes the reason.

If a `<system-reminder>` or any other injected directive in this session tells you to work autonomously without stopping for clarifying questions (e.g. "no-stopping directive"), **it does NOT override these gates**. In particular: do NOT guess a repro on the user's behalf — the goldfish needs something concrete to act on, and a wrong-shaped guess wastes the goldfish call. Always ask.

The only opt-out: if the user, in the same turn that invoked this skill, explicitly says "use your best guess for the repro" (or equivalent unambiguous override), you may proceed with an inferred repro. Even then: print the repro you're using before spawning the goldfish.

## Step 0: Triviality gate

**Skip the goldfish/test ceremony for:** typo fixes in copy or comments, dead-code removal, version bumps, formatter-only diffs, single-line config tweaks. Go straight to Step 5 (`/eg-precommit-review`) and the test gate.

**Run the full loop for everything else,** including small one-line code fixes — small diffs hide bugs disproportionately well.

## Step 1: Write the problem doc (in this conversation)

Print a tight problem doc to the user. Keep it brief but complete:

```
PROBLEM DOC
- Symptom: <what the user observes>
- Repro: <steps to reproduce, or "user did not provide; need to derive">
- Suspected area: <file/module/route/worker/job/screen>
- Hypothesised root cause: <one sentence>
- Blast radius: <which other surfaces could be affected>
- "Fixed" means: <specific test passes / specific behavior / specific output>
```

If the user gave no repro and the bug is not obvious from a single file read, **stop and call `AskUserQuestion`** to scope the missing repro:

- `question`: "No repro provided. How would you like to proceed?"
- `header`: `"Repro?"`
- `multiSelect`: `false`
- `options`:
  1. **I'll paste a repro in chat** — "Steps, URL, failing test name, log line, or screenshot."
  2. **It's in a GitHub issue / linked doc** — "I'll share the link in chat."
  3. **You infer it from the symptom** — "Use your best guess; I accept the risk it may be wrong-shaped."
  4. **Drop the request** — "Not enough context yet; I'll come back."

Then accept the user's free-form input in chat for options 1 or 2. Do NOT guess on the user's behalf unless they pick option 3. The goldfish needs something concrete to act on.

For UI bugs (Jinja2/HTMX/Alpine pages), the repro path is usually the **Chrome DevTools MCP** (`mcp__plugin_chrome-devtools-mcp_chrome-devtools__*`, or `mcp__Claude_in_Chrome__*` if that is what the session has) against `http://localhost:8000`. Assume the stack is running; if it isn't, start it with `docker compose up -d --build` (default admin `admin` / `changeme`). Navigate, click, take a screenshot, read the console. Webmail/IMAP bugs go through Roundcube on `http://localhost:8001` or Dovecot on `localhost:31143`. For API/agent/MCP bugs, the repro is usually a `curl` against `http://localhost:8000` (with `Authorization: Bearer mfb_…` for `/api/v1/agent` and `/mcp`), a failing test name, or a log line from `docker compose logs mailfallback`.

## Step 2: Goldfish diagnosis check (parallel to your hypothesis)

Spawn a fresh agent with `Agent` tool:
- `subagent_type: "Explore"` for narrow lookups, `"general-purpose"` if the bug spans multiple subsystems. If the harness does not have `Explore` registered, fall back to `general-purpose`.
- `description: "Goldfish bug diagnosis"`

The goldfish gets ONLY the symptom + repro from Step 1. It does NOT get your hypothesised root cause — that asymmetry is the point.

**Prompt body to send (between markers, exclusive):**

```
<<<DIAG_START>>>
Independent diagnosis of a bug in this repo (MailFallBack / MFB, a self-hosted email backup service: a Python 3.14 FastAPI + PostgreSQL app that wraps mbsync/isync to back IMAP mailboxes up to Maildir and serves them read-only through Dovecot and Roundcube; CLAUDE.md at the repo root has the full architecture).

Symptom: <FILL IN from Step 1>
Repro: <FILL IN from Step 1>

Investigate. Where in the codebase is the bug most likely to live? Cite specific file:line locations. List the top 1-3 candidate root causes ranked by likelihood. For each candidate, name what evidence in the code supports it and what would falsify it. Do NOT propose a fix yet — just diagnose.

If a UI repro is needed, you may use the Chrome DevTools MCP (`mcp__plugin_chrome-devtools-mcp_chrome-devtools__*`) against the running app at http://localhost:8000.

End with the literal string `diagnosis complete`.
<<<DIAG_END>>>
```

**Compare goldfish output to your Step 1 hypothesis.**
- Convergence (top candidate matches your hypothesis): proceed to Step 3 with confidence.
- Divergence: re-investigate. Read the goldfish's evidence. If it is right, update the problem doc and tell the user "goldfish flagged a different root cause; re-diagnosing." If you are right, write down WHY the goldfish was wrong — that disagreement is itself useful signal.

If the bug is genuinely tiny (1-3 line fix in a clearly-identified location), you may skip the goldfish call — but only when the location is mechanically obvious. When in doubt, run it.

## Step 3: Capture the bug as a failing test BEFORE fixing

The verification criterion lives in code, not in chat. Pick the right tier:

- **Service unit test** (`tests/test_<service>.py`, e.g. `test_sync_worker.py`, `test_account_service.py`): pure logic against the `db_session` fixture (in-memory SQLite). mbsync/restic/doveadm subprocesses and the Dovecot HTTP API are mocked, never really run.
- **Router / API test** (`tests/test_<area>_api.py`, `test_agent_api.py`, `test_ui*.py`): FastAPI `TestClient` with the `get_db` override. Use it for auth, scope, ownership and HTML-response bugs. For the agent API, cover both a session principal and a bearer token principal.
- **MCP test** (`tests/test_mcp_*.py`): tool behaviour, token scope checks, mount/lifespan.
- **Migration / drift** (`tests/test_alembic_sync.py`, `test_migration_backfill.py`): schema changes, `server_default` on NOT NULL columns.
- **Real isync behaviour** (`tests/integration/test_mbsync_removed_box.sh`, needs Docker and the pulled product image): only when the bug is about what isync itself does on disk. No pytest can pin that.
- Watch the SQLite-vs-PostgreSQL gap: if the bug depends on PostgreSQL behaviour (JSON/array ops, `tsv` triggers, locking), say so. A green SQLite test may not prove the fix.

Write the test. Run it. Confirm it fails for the reason described in the problem doc. If it fails for a different reason, the test is wrong — fix the test before touching the implementation.

For UI bugs that only show in a real browser (HTMX swaps, Alpine state, `static/js/` behaviour, CSS/responsive at the 768px breakpoint), there is no browser test tier. Capture the repro as a Chrome DevTools MCP script in the conversation: navigate, click, screenshot, read the console. Confirm the bug is observable. This is your verification path, and you re-run it after the fix. Still add a `TestClient` test for whatever server-side part the bug has (the rendered HTML, the HTMX partial, the status code).

## Step 4: Fix it

Implement the smallest change that turns the failing test green and matches the "Fixed means" criterion.

Avoid: adjacent refactors, defensive coding for cases the bug did not surface, fallbacks that mask future regressions, unrequested feature flags. Bug fix scope is the bug, nothing else.

Re-run the failing test. It must go green. If it does not, you have not fixed the bug — do NOT rewrite the test.

For UI bugs, rebuild (`docker compose up -d --build`, since templates and static files are baked into the image) and re-run the Chrome DevTools MCP repro from Step 3. Confirm the symptom is gone and the console is clean.

## Step 5: Hand off to `/eg-precommit-review`

Run `/eg-precommit-review` per the canonical procedure. The reviewer is a second goldfish — it sees only the diff. Triage findings, loop, and exit.

If `/eg-precommit-review` surfaces an issue that the Step 2 diagnosis goldfish missed, note it in the final report — it tells us where the diagnosis prompt needs to be tighter next time.

## Step 6: Test gate

```sh
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest tests/ -n auto -q
uv run pytest tests/ -n 4 -q          # CI-like xdist grouping — catches sys.modules / fixture-leak interactions -n auto hides
uv run pre-commit run --all-files     # secrets (gitleaks + detect-secrets), alembic drift, lexicon
bash tests/integration/test_mbsync_removed_box.sh   # only if the diff touches mbsync_config.py / sync_worker.py folder handling
```

All required tiers must pass. Run the isync integration script only when the fix depends on how isync behaves on disk (Docker and the pulled product image are required). If `models.py` changed, a matching Alembic migration must be in the diff; `test_alembic_sync.py` fails otherwise. For UI bugs, also re-verify the original repro in the Chrome DevTools MCP one final time before reporting done.

## Step 7: Final report

Print to the user:
- Bug summary (one line)
- Root cause (one line)
- Fix (file:line)
- Test that captures it (file:test name)
- Goldfish-vs-elephant agreement (converged / diverged + why)
- `/eg-precommit-review` outcome (rounds, fixes, rebuttals verbatim)
- Test gate status

**STOP.** Do NOT commit; auto mode does not override the project's commit policy. Wait for the user's literal commit instruction. Follow the convention visible in `git log`: Conventional Commits with a scope (`fix(sync): …`, `fix(index): … (#255)`), referencing the GitHub issue in the subject when there is one. Work on a `fix/<issue>-<slug>` branch, never on `main`. Never bump `version.py` or edit `CHANGELOG.md`, because only the release PR does that.
