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

## Engineer red-team status

The senior-engineer red-team agent (`09-iteration-engineer.md`) was running at the time this synthesis was written. If/when it completes, this synthesis should be revisited with the implementability findings folded in. Specifically the engineer agent was asked to reality-check:

- The 4-week effort estimate
- The qualifier-discipline lint implementation cost
- The chain-widget HTMX architecture (now demoted, so partially obsolete)
- File-level impact of the four security-adjacent fixes
- Whether DB-name leaks into user-facing text invalidate the "deferred" claim
- Test impact

If the engineer's findings significantly change the scope or sequencing, this synthesis is updated and the recommendation revised again.

---

## Score after revisions

If `08-recommendation.md` is updated per the verdicts above, the principal designer's score improves:

| Dimension | Before | After (estimated) | Reason |
|---|---|---|---|
| Clarity | 8/10 | 9/10 | Bilingual graft removed; "four fixes" recounted honestly. |
| Durability | 6/10 | 7/10 | Mobile + accessibility constraints fold the missing bits in. |
| Ambition | 5/10 | 5/10 | Still defensive — that's the right call for a 4-week wave. Ambition belongs in Option C. |
| Implementability | 7/10 | 8/10 | Lint downgrade + chain-widget demotion remove the two riskiest under-estimates. |

Net: **clarity 9 / durability 7 / ambition 5 / implementability 8**. A defensible v1 of the rollout.
