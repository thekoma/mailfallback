# Phase 9 — synthesis of red-team feedback

The principal-designer red-team in `09-iteration-principal-designer.md` raised eight specific disagreements with `08-recommendation.md`, plus five "missing entirely" items, plus a structural attack on the bilingual lexicon graft and the chain widget. The senior-engineer red-team in `09-iteration-engineer.md` (status: still running at time of writing — see footnote) was tasked with the implementability angle.

This document classifies each piece of red-team feedback as **ACCEPT** (fold into a revised recommendation), **REJECT** (defend the original), or **DEFER** (open question for Andrea).

The accepted items will be applied as inline edits to the recommendation in a follow-up commit, so `08-recommendation.md` always reflects the latest decision. This file is the audit log of WHY each change happened.

---

## Disagreements — verdicts

| # | Principal-designer claim | Verdict | Reasoning |
|---|---|---|---|
| 1 | Kill the bilingual graft. Pick **Repository in both languages**. | **ACCEPT** (with caveat) | The graft was a hedge. The argument that IT and EN audiences meet constantly in the same docs site / GitHub is correct. Italian developers say `repository` daily. **Caveat:** the Italian `Deposito` was the user's own framing in the kickoff message; before locking, ask Andrea whether to drop it. If he insists on `Deposito`, ship `Deposito` in both languages instead — *not* the graft. |
| 2 | Demote chain widget from sticky-everywhere to **dashboard hero only + per-page status line**. | **ACCEPT** | The argument is right: the sticky widget competes with the sidebar, doesn't survive mobile, and offers no new actions on per-screen pages. Replace with a one-line per-page status header. The dashboard hero stays — it teaches the model. |
| 3 | The "four security-adjacent fixes" are actually 3 + 1 docs ticket. Drop SECRET_KEY docs, add a "dashboard off-site health row" instead. Upgrade the recovered-mailbox copy fix to a real **"Promote to live" button**. | **ACCEPT** | The recount is honest. The "Promote to live" upgrade is the single most-leveraged change in the entire red-team — it closes the recovery loop the audit explicitly left open. The SECRET_KEY page can move to Wave 1.5 as a separate doc commit, not counted as a UI fix. |
| 4 | "Volume" collides with Docker / K8s / LVM. Use **"Storage" / "Archivio"** or keep "Mail Store". | **ACCEPT** | Strong observation — MFB's audience runs K8s. "Volume" was inherited from `MailStore.path` and is admin-jargon. **Decision:** rename to **"Storage volume"** in EN (the qualifier disambiguates) and **"Archivio fisico"** in IT. Or, less invasively, keep "Mail Store" / "Mail store" and just stop calling them "Stores" plural in nav. The cheapest fix is keeping the term and making the page label longer. |
| 5 | DB rename should be **Wave 5 regardless of P3**, not deferred indefinitely. | **REJECT** | The principal designer is right that the divergence is debt. But the audit's analysis-only constraint was explicit, and the DB rename has a non-trivial blast radius (Alembic + tests + 30 references). Andrea can decide to schedule it whenever he wants, but tying Wave 5 to "after Wave 4 + stable test suite" is a sensible engineering policy that doesn't need to be in the binding recommendation. **Pushback:** add a one-paragraph "After Wave 4" follow-on note in `08-recommendation.md` saying the rename is the next natural step regardless of P3 demand. |
| 6 | "Aggiorna ora" is too weak; use **"Sincronizza ora"**. | **ACCEPT** | The argument lands: Italian users see "Aggiorna" on every browser refresh button, and the rec optimised for "not jargon" where it should have optimised for "the user's existing word". `Sincronizza` matches what the user types when configuring mbsync. *Note:* this means we keep "Sincronizza" as a verb in the IT lexicon for stage 2, which slightly weakens the qualifier discipline (the noun "Sync" was banned but the verb form is now back). Acceptable trade. |
| 7 | The qualifier-discipline lint should be **advisory in CI, not failing**. Limit scope to `templates/` + router flash strings. | **ACCEPT** | Concession is correct. Make the check a `warn` not a `block`, with the option to upgrade to `block` once false-positive rate is known. Keep the scope tight from day one. |
| 8 | Add a **30-day tooltip-on-hover** mapping old labels to new (not just a release-notes toast). | **ACCEPT** | Cheap, durable, and respectful of muscle memory. One-liner per renamed label, controlled by a feature flag that auto-disables 30 days post-deploy. |

---

## "Missing entirely" — verdicts

| # | Principal-designer claim | Verdict | Reasoning |
|---|---|---|---|
| 9 | No accessibility audit. Coloured dots are the canonical bad pattern. | **ACCEPT** | Add to Wave 4 as a non-negotiable: every status pill / chain-widget dot must have an icon + text label + `aria-live`. The chain widget colour mapping needs a sanity test against WCAG contrast at minimum. |
| 10 | No mobile story. | **ACCEPT** | Add to Wave 4 the constraint: every new component must work at 360px. Specifically the per-page status line (replacement for the sticky chain widget) must be designed mobile-first. |
| 11 | No empty-Repositories state designed. | **ACCEPT** | Add to mockups doc and Wave 4 a specific empty-state for /admin/repositories — the hardest of all, because it's the page where the user discovers the off-site concept. |
| 12 | No onboarding for existing users. | **ACCEPT** | The 30-day tooltip from disagreement #8 partially handles this. Add explicitly: on first login post-rename, show a one-screen "what changed" page (dismissible, durable bookmark-able), separate from a toast. |
| 13 | No measurement plan. | **DEFER** | Designing user tests for a homelab self-hosted product with no centralised analytics is genuinely hard. The "five tests" in the recommendation are asynchronous-thought-experiments, not field tests. Either accept the limitation or invest in a separate analytics design — outside this audit's scope. Andrea decides. |

---

## Structural critiques — verdicts

### The bilingual graft (item 1 above)

**ACCEPT** in the form: pick one English term, mirrored in Italian. **Recommended pick:** `Repository` in both languages — the principal designer's argument is convincing and `Repository` is already in Italian developer vocabulary.

**Open for Andrea:** if he wants to defend `Deposito` on identity grounds (it was his own seed framing), ship `Deposito` in both. **Not acceptable:** the graft.

### The chain widget (item 2 above)

**ACCEPT** the demotion. The revised design:

- **Dashboard:** keep the hero card with the four-stage diagram + counts + status. Teaching surface.
- **Account detail:** add a per-account status header (one line: "Mirror ✓ · Repository ⚠"). No widget.
- **Other pages:** no chain UI. The sidebar already navigates.

This is cheaper to build, friendlier to mobile, and survives the "I dismissed it on day 3" test.

### The lint check positioning

**ACCEPT** — move from Wave 1 to "day 0 of Wave 2", and downgrade from blocking to advisory. Same content, lighter operational profile.

---

## What to revise in `08-recommendation.md`

A follow-up commit to that file should:

1. **Replace the bilingual term-mapping table** with single-language Repository (or, if Andrea picks `Deposito`, single-language Deposito for both).
2. **Demote the chain widget**: keep dashboard hero, add per-account header line, drop sticky.
3. **Re-list the four "before anything else" fixes** as: (a) "Backup configured" badge, (b) "insecure_tls" labelling + warning, (c) **"Promote to live" button** for recovered mailboxes, (d) **dashboard off-site health row** (instead of SECRET_KEY docs).
4. **Move the SECRET_KEY docs page** to a separate Wave 1.5 deliverable, not counted in the four UI fixes.
5. **Rename "Volume" → "Storage volume" / "Archivio fisico"** (or keep "Mail Store").
6. **Replace "Aggiorna ora" with "Sincronizza ora"** in the IT lexicon.
7. **Soften the lint check** from blocking to advisory and scope to `templates/` + router flash messages.
8. **Add 30-day tooltip-on-hover** as the migration UX.
9. **Add accessibility constraint** to Wave 4 (icon + text + aria-live for all status surfaces).
10. **Add mobile constraint**: 360px floor for new components.
11. **Add empty-Repositories state** to the mockups + Wave 4.
12. **Add a one-screen "what changed" page** for existing users on first login post-rename.

The DB rename language stays as-is (deferred to a future Option C decision) but with a one-paragraph note saying it's the natural Wave 5 follow-on regardless of P3.

---

## What I'd push back on (REJECT defended)

- **The "P3 is the wrong gating criterion for the DB rename"** — the principal designer is right that P3 is hypothetical, but the alternative ("Wave 5 regardless") doesn't address the real reason the rename was deferred: **blast radius** (Alembic + 30 refs + tests). The audit was constrained to analysis-only and the DB rename is implementation. Keep deferred but acknowledge it shouldn't wait for P3 specifically.

That's the only outright rejection. Everything else is either accepted or deferred for Andrea.

---

## Engineer red-team — verdicts

The senior-engineer red-team agent (`09-iteration-engineer.md`) landed and gave concrete file-level findings. All accepted unless noted.

| # | Engineer claim | Verdict | Reasoning / action |
|---|---|---|---|
| E1 | **4 weeks is optimistic.** Realistic is 4.5–5 weeks; 6 with review cycles. | **ACCEPT** | Update the recommendation's timeline. The compounding risk is the chain-widget wave (5–7 days, not 5). |
| E2 | **`last_successful_run_at` likely doesn't exist on `AccountBackup`** — Wave 1's "Backup configured" badge fix needs an Alembic migration to honestly distinguish "last attempted" from "last successful". | **ACCEPT** | Add to Wave 1 explicitly: a small Alembic migration adding `last_successful_run_at`. Otherwise the badge fix is half-honest. |
| E3 | **Snapshot count caching is mandatory.** Live restic shelling on a 5s polling chain widget would melt disk I/O and lie under load. Need `AccountBackup.last_snapshot_count` + `last_snapshot_at` cached, updated by `backup_worker`. | **ACCEPT (critical)** | This is the single biggest implementation risk the original recommendation didn't acknowledge. Add to the Alembic migration (now batched: `last_successful_run_at` + `last_snapshot_count` + `last_snapshot_at`). The widget's "N snapshots" reads from cache, not restic. |
| E4 | **Audit log strings render in the UI** — `audit_logs.action="backup_destination.create"` appears in `admin_audit.html`. DB rename deferral is **safe only if** a small display-mapper is added in Wave 2. | **ACCEPT** | Lightweight: a `dict` mapping legacy action strings to display names in the audit template. ~5 lines. Wave 2 deliverable. |
| E5 | **No i18n infrastructure exists.** The bilingual `LEXICON.md` table is aspirational. Wave 2 ships **English only**; the IT column is a future-i18n placeholder. | **ACCEPT** | This honestly clarifies what Wave 2 delivers. The Italian column in `LEXICON.md` becomes a "translation target", not a binding contract. Italian rollout is a separate epic when gettext / Babel is set up. |
| E6 | **Recent commit `4934ad3` (edit backup destination, inline expandable form) is mid-flight.** Wave 1's badge work will collide with `partials/account_backup.html`. | **ACCEPT** | Sequence: let any pending feature commits land first, then start Wave 1. If Wave 1 starts before, coordinate via PR. |
| E7 | **Verify `account.suspended` is honored in Dovecot Lua userdb** before relying on the "recovered = suspended" pattern as a safety mechanism. | **ACCEPT (action item)** | Add to Wave 1.5 as a verification task before the "Promote to live" button is built. If the suspended flag isn't actually blocking IMAP login, the whole recovery model needs a different mechanism. |
| E8 | **Extend `system_status.html` for the chain widget; don't build a parallel sticky bar.** | **ACCEPT** | The principal designer already demoted the sticky variant. The dashboard hero is now the only persistent surface; per-page status is a one-liner in the page header. Parallel-bar concern resolved. |
| E9 | **Sequencing counter-proposal:** split Wave 1 into 1a (foundations: LEXICON + lint + audit display map, Day 1-2) and 1b (security fixes, Day 3-5). Add Wave 2.5 for the batched Alembic migration. | **ACCEPT** | Adopt directly. The 1a/1b split protects the foundation; the Wave 2.5 migration consolidates 3 columns in one round of test fixture updates. |
| E10 | **Test impact: ~30 assertion updates + ~15 new tests.** Should keep parallel pass time under 10s. No fixture surgery. | **ACCEPT (informational)** | Confirms test budget. ~390 → ~405 tests. Fold into milestone planning. |

### Risks the engineer flagged that the audit had missed

1. **Snapshot caching mandatory** (E3) — the most important.
2. **Audit display map** (E4) — without it, "Repository" in nav and "backup_destination.create" in audit page coexist.
3. **In-flight commit collision** (E6) — operational coordination, not design.
4. **Dovecot honors suspended flag** (E7) — verification gate.
5. **i18n is fictional** (E5) — clarify Wave 2 scope.

### What to add to the recommendation revision (engineer-driven)

11. **Adopt 1a/1b split** for Wave 1 + add Wave 2.5 (batched Alembic).
12. **Time estimate: 4.5–5 weeks**, not 4.
13. **Wave 1 must include three new `AccountBackup` columns** in a single Alembic.
14. **Wave 2 must include the audit-action display mapper.**
15. **Wave 1.5 must verify the Dovecot Lua userdb honors `suspended`** before "Promote to live" is built.
16. **`LEXICON.md`'s Italian column is aspirational** until gettext is set up. Don't ship IT-language UI in Wave 2.
17. **Coordinate with mid-flight `4934ad3`** before Wave 1 starts.
18. **Chain widget extends `system_status.html`** (already implied by the demotion, but make it explicit).

---

## Score after revisions

If `08-recommendation.md` is updated per the verdicts above, the principal designer's score improves:

| Dimension | Before | After (estimated, post-PD revisions) | After engineer revisions | Reason |
|---|---|---|---|---|
| Clarity | 8/10 | 9/10 | 9/10 | Bilingual graft removed; "four fixes" recounted honestly. Engineer's i18n clarification adds honesty. |
| Durability | 6/10 | 7/10 | 8/10 | Mobile + accessibility folded in (PD); audit-display map and snapshot caching close two foreseeable bug classes (engineer). |
| Ambition | 5/10 | 5/10 | 5/10 | Still defensive — the right call for a 5-week wave. Ambition belongs in a future Option C. |
| Implementability | 7/10 | 8/10 | 9/10 | Lint downgrade + chain demotion (PD) + 1a/1b/2.5 split + acknowledged 5-week timeline (engineer) make this realistic. |

Net: **clarity 9 / durability 8 / ambition 5 / implementability 9**. A defensible, ship-able v1 of the rollout.

---

## Net effect on `08-recommendation.md`

The recommendation should be revised in a single follow-up commit to reflect:

- 4.5–5 week timeline (was 4 weeks).
- Wave 1 is split: 1a (foundations) + 1b (4 security fixes, batched Alembic for 3 new `AccountBackup` columns).
- Wave 1.5: Dovecot suspended-flag verification + DR doc.
- Wave 2: English-only lexicon rename + audit-action display mapper. Italian column is i18n-target only.
- Wave 4: chain widget *extends* `system_status.html`; dashboard hero is the only major new surface; per-page status is a one-liner in page headers; mobile-first 360px floor; accessibility (icons + text + aria-live) mandatory.
- Lexicon: Repository OR Deposito in **both languages** (Andrea picks).
- DB rename: "Mail Store" stays as-is OR renamed to "Storage volume" / "Archivio fisico" — Andrea picks. "Volume" is rejected outright.
- IT verb for stage 2: "Sincronizza ora", not "Aggiorna ora".
- Migration UX: 30-day tooltip-on-hover + one-screen "what changed" page on first login.

This is a substantive but additive revision — none of it invalidates the structural shape of the recommendation. The audit produces a **stronger v1.1 recommendation document** out of two red-team passes.
