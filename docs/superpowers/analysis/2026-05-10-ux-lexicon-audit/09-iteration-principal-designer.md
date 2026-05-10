# Principal-designer red team on the recommendation

## TL;DR — what I'd change

- Kill the bilingual graft. Pick one lexicon and ship it. "Deposito" in IT, "Repository" in EN is not a compromise; it is two products in one head.
- Demote the chain widget from "centrepiece" to "dashboard hero only". A sticky four-dot bar on every page is a trinket that will be CSS-hidden by week six.
- Promote one Wave 1 fix the audit treats as documentation: the SECRET_KEY / Postgres double-loss footgun deserves a UI surface, not a docs page.
- Replace the "Recovered mailbox suspended" pattern with a real action: "Promote recovered mailbox to live". The audit explicitly defers the only thing that closes the recovery loop.
- Move the `LEXICON.md` lint check out of Wave 1 and into day zero of Wave 2. It is process, not product, and it is being used as a load-bearing risk mitigation. That is a smell.

## What the recommendation gets right

- **Rejecting Option C is the correct call.** P3 is hypothetical; building compliance reports for a persona you have never spoken to is fan-fiction. The doc names this honestly.
- **Option A is correctly diagnosed as a regret machine.** Renaming labels around a buried section just changes which words the user can't find.
- **Banning bare "Backup" as a noun.** This is the single most leveraged lexicon decision in the document. It survives any other design choice and is worth the whole audit.
- **The /restore split into Recover vs Move.** The verb overload was real; two flows with two doors fixes it cleanly. This is the most underrated change in the recommendation.
- **Naming the "Backup configured" badge as a lie.** That is a security-grade observation dressed as a copy fix, and elevating it to Wave 1 is exactly right.

## What I disagree with

| # | Recommendation's claim | What's wrong | What I'd do instead |
|---|---|---|---|
| 1 | "Repository" graft on "Deposito" resolves both audiences. | It resolves neither. The IT user reading docs sees `Deposito` in chrome and `Repository` in the restic README and has to translate. The EN user gets `Deposito` if they ever talk to Andrea. The graft creates a glossary, not a vocabulary. | Pick one. If the product's primary author thinks in Italian and ships to a homelab IT-speaking audience, ship Italian-first with EN as a strict 1:1 mirror — same metaphor, translated. "Deposito" → "Depot" or "Repository", pick one and lock it. No bilingual cleverness. |
| 2 | The chain widget belongs sticky on every page. | Persistent navigational chrome is a tax. Power users dismiss it (the rec admits this); new users stop seeing it after day three (banner blindness). The "counts under each dot" only matter on the dashboard. Everywhere else it is decoration. | Keep the dashboard hero. Keep the per-account variant. Drop the sticky. Replace with a single status line in the page header where it actually matters. |
| 3 | Four security-adjacent fixes go before the rename. | One of the four (the SECRET_KEY docs page) is not a fix; it is a docs ticket. Bundling it inflates the count and lets a real UI gap (no in-product disaster-recovery surface) hide behind a markdown file. | Three UI fixes in Wave 1; the SECRET_KEY problem becomes a Wave 1.5 *product* item: a "Recovery readiness" panel on Dashboard showing whether the user has exported the secret and the DB. |
| 4 | "Mail Store" → "Volume" / "Volumi". | "Volume" already means something in Docker, K8s, and LVM — exactly the audience MFB targets. You will spend the next two years explaining "no, not *that* volume." | Use "Storage" / "Archivio fisico" or keep "Mail Store" plainly. Anything but a word that collides with the homelab vocabulary the rec leans on elsewhere. |
| 5 | The model rename is deferred indefinitely, gated on P3. | The longer GUI labels and DB names diverge, the harder onboarding new contributors becomes, and the more bugs hide in the gap between what the user sees and what the code calls it. P3 is the wrong gating criterion. | Gate the rename on Wave 4 completion plus a stable test suite. Do it as Wave 5 regardless of P3. It is a one-week migration and pure debt repayment. |
| 6 | "Aggiorna ora" replaces "Sync now" for refresh. | "Aggiorna" in IT means update — not the same emotional weight. Italian users see it on every browser refresh button. It will read as "reload page". | Use "Sincronizza ora" — it is the same word the user already types when configuring mbsync, and it is unambiguous. The rec optimises for not-jargon; here that is wrong. |
| 7 | The qualifier-discipline lint is "1 day to set up". | A regex that flags `\bbackup\b` without qualifiers will fire on every comment, every test fixture, every commit message, and every legacy migration string. False-positive triage is forever. | Make it advisory in CI (warning, not failure) and run it only on `templates/` and router flash strings. Accept that perfection is not the goal; reducing slippage is. |
| 8 | The "what changed in this release" toast on next login is sufficient migration UX. | Toasts get dismissed in 200 ms. The lexicon change is not a release-note event; it is a vocabulary swap on a product some users have memorised. | Add a tooltip-on-hover for the first 30 days mapping old label to new ("Mail Store → Storage"). One-line addition to the icon library. |

## What's missing entirely

- **No accessibility audit.** The chain widget is built on coloured dots. Red/green/yellow/grey is the canonical bad pattern. Where are the icons, the text labels, the aria-live for state changes?
- **No mobile story.** The recommendation ships a sticky sub-header on every page and never asks what happens at 360px. Marco checks the dashboard on his phone from the couch. The rec is built for a 1440px monitor.
- **No empty-Repositories state.** The mockups show a populated repositories table. What does P5 see at /admin/repositories before they have one? The hardest empty state in the product is undesigned.
- **No onboarding for the existing user.** Every user with `created_at < 30 days` is targeted by the chain widget. Every user older than that gets nothing — no migration UX, no "we renamed things", no opt-in tour. The 200 existing installs are treated as collateral.
- **No measurement plan.** The "how to know this worked" section lists five tests, four of which require putting humans in front of screens. Who runs them? When? With what users? Without a recruiting plan this is theatre.

## The lexicon graft is suspicious

The graft is a fudge. The two audiences do not live in separate rooms — they meet constantly in the same docs site, the same GitHub issues, the same forum screenshot. The "Italian voice + restic alignment" argument assumes a clean split that does not exist: the homelab IT reader bounces between Italian release notes and the restic English README ten times an hour.

The graft also *betrays the recommendation's own logic*. The doc argues "Backup" must be banned as a bare noun because overloaded vocabulary causes failures. Then it ships an overloaded vocabulary across two languages and calls it a feature. If "Deposito" survives, it survives translated as "Depot". If it does not, "Repository" should win in IT too — Italian developers say `repository` daily.

Force a choice. My pick: **Repository in both languages**. Restic and Kopia already won the metaphor war for this audience; IT users type `git push` to a repository every day; the strongest personas (P1, P2, P4) all live in code-adjacent vocabulary.

## The chain widget — necessary or a vanity component?

Mostly vanity. The dashboard hero is genuinely useful — it teaches the model where teaching is invited. The sticky variant is a ribbon. It does not navigate (four destinations the user already has in the sidebar), does not act (no buttons), does not fit on mobile.

Worse, it competes with the product's actual nav. MFB has a sidebar. Adding a horizontal status bar above content means two navigations to scan before content. That is a regression dressed as polish.

Replace with **a single status line in the page header** showing only the stage relevant to that page. /accounts shows mirror + repository. /admin/repositories shows repository. Dashboard keeps the full hero. Same teaching, half the chrome, survives 360px.

## Wave 1's "four security-adjacent fixes" — too many or too few?

Too many of one type, missing the right one.

The four:

1. "Backup configured" badge — **correct**. Ship it.
2. "insecure_tls" labelling — **correct, but underbuilt**. Add a confirmation step, not just a banner.
3. Recovered-mailbox flash rewording — **correct copy fix**, but the underlying bug ("limbo state with no resolution") is what should be in Wave 1. The rec defers "promote to live" to Option C. That is wrong: the copy fix without the action is honest about a broken state, which is worse than the lie.
4. SECRET_KEY docs page — **wrong category**. This is documentation, not a UI fix. It should not count toward "four security-adjacent fixes".

What is missing: a **last-successful-backup timestamp visible at dashboard level**, not buried in account detail. The whole audit hinges on the observation that users cannot tell whether off-site is healthy. The Wave 1 fix list talks about it on a badge but does not surface it where Marco sees it once a month.

So: drop #4, replace with "dashboard off-site health row". Upgrade #3 from copy fix to "promote recovered mailbox" button. That is the right four.

## Forecast: what will Andrea push back on?

- **"The chain widget is too much."** Likely. He prefers density and dislikes ornament. *Rebuttal:* agree on the sticky variant, defend the dashboard hero as load-bearing for P5, show a 360px mockup proving it does not survive mobile.
- **"`Volume` is wrong because of Docker."** Possible — he runs K8s. *Rebuttal:* he is right; concede and propose `Storage` / `Archivio` instead.
- **"Why not just ship the rename and skip the IA work?"** Likely framed as "I will do the IA later". *Rebuttal:* every Italian-speaking founder has shipped a rename without IA and bought regret. Show him Option A's "HIGH risk of regret" row from his own audit.
- **"The lint check feels heavy for one developer."** Likely. *Rebuttal:* concede; downgrade to advisory; let his engineering taste pick the failure threshold later.
- **"P3 is real, I want compliance reports."** Possible — he hopes for it. *Rebuttal:* show him the audit's own P3 paragraph: "not yet a validated audience". Build the lexicon now, build the report when one real Giulia writes an issue.

## My final score

| Dimension | Score | One-line justification |
|---|---|---|
| Clarity | 8/10 | Decisions are crisp, scope is named, deferrals are explicit; loses points for the bilingual graft and the "four fixes" miscount. |
| Durability | 6/10 | Lexicon and IA work survive, but the deferred model rename and missing mobile story will create rework within a year. |
| Ambition | 5/10 | Plays defensively. Rejects C for valid reasons but never asks what would make MFB *delightful*, only what makes it less wrong. |
| Implementability | 7/10 | Four-week single-developer estimate is plausible; loses points because the lint check, the chain widget polish, and the mobile gap are all underbid. |
