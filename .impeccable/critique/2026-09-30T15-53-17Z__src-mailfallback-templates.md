---
target: MFB web UI (shell, dashboard, accounts, admin, profile)
total_score: 21
max_score: 40
na_heuristics:
p0_count: 2
p1_count: 2
target_identity: "file:/Users/koma/src/mailfallback/src/mailfallback/templates"
timestamp: 2026-09-30T15-53-17Z
slug: src-mailfallback-templates
---
# Critique — MailFallBack web UI (shell, dashboard, accounts, admin, profile)

Method: dual-agent (A: design review · B: detector + browser). Evidence: live app with seeded demo data, desktop 1440 + mobile 390, light + dark.

## Heuristics (21/40, Acceptable)
| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of system status | 2 | Same mailbox reads re-auth on dashboard, "Initial sync" in list, green Auth dot on detail |
| 2 | Match system / real world | 2 | XOAUTH2, cron strings, raw AUTHENTICATIONFAILED, FTS/Dovecot, raw audit actions |
| 3 | User control and freedom | 3 | Stop/dismiss/show-all work; Delete equal weight to Edit |
| 4 | Consistency and standards | 1 | Accounts vs Mailbox (LEXICON), Title Case, 900-1400px widths, three token systems |
| 5 | Error prevention | 2 | Retry on invalid credentials; Configure off-site shown to non-admin |
| 6 | Recognition over recall | 2 | Search buried under Restore; icon-only audit filter |
| 7 | Flexibility and efficiency | 2 | No global search, shortcuts, skip link |
| 8 | Aesthetic and minimalist | 2 | Zero-count pills always visible; key region 4th of 5 |
| 9 | Error recovery | 2 | Great re-auth banner; raw IMAP errors elsewhere |
| 10 | Help and documentation | 3 | Chain explainer, teaching empty states, guide links |

## Design specificity
Category-interchangeable Pico/Geist/bento admin console. Only product-authored element: the Mail safety chain, demoted to the 3rd dashboard card and not reused. "Calm by default" not realised: loudest pixels are red uppercase DOWN pills and a vanity count.
Detector: 18 CLI hits (all via base.html CSS); browser 5-31/page. Real: sidebar hover 2.8:1 (style.css:192), dark chain labels 3.7:1 (style.css:691), dark panel headers 4.0:1, neutral badge 4.4:1 (style.css:1492), sub-11px text (style.css:200, :2535), h2→h4 skip on /restore, em-dash density. False positives: pulsing dots (state, reduced-motion guarded), calendar stripes (meaningful), floating-layer shadows, untraceable dark-glow #8191b5.

## Priority issues
- [P0] Mailbox status disagrees across screens (accounts_table.html:54-80, account_detail.html:73-77, dashboard.html:86, dashboard.html:199-211 ignores failure_kind, settings Dovecot Active vs strip DOWN). Fix: single per-mailbox status resolver (state, tone, label, next action, timestamp) consumed by all templates + agent API. → clarify, harden
- [P0] Admin mobile shell unnavigable: sticky status strip z-index 900 (style.css:1477) covers topbar hamburger (z 98); dashboard 476px wide at 390; accounts table 1112px in 347px; restore chips overflow. Fix: system health as one topbar indicator; mailbox cards <768px. → adapt
- [P1] Dashboard hierarchy inverted/noisy: checklist→numbers→chain→attention; Reconnect below fold; zero pills; healthy initial syncs in Needs Attention; 279986 unformatted. Fix: one-line verdict, then actionable items only; chain as persistent frame; strip shows only anomalies. → distill, layout
- [P1] Accessibility/legibility: status pills are span onclick (system_status.html:2-30), panels close every 5s (base.html:66 refresh), no skip link, tablist without tabs (restore_workspace.html:21), nested main, aria-live on static chain, colour-only meaning, 90% root + ~14 sizes → 9-11px text, contrast findings. Fix: real type scale min 12px, button pills with preserved state, AA both themes, shape+icon+text status. → audit, typeset
- [P2] Two audiences, one interface; search buried: owner sees XOAUTH2/cron/Raw data/dead-end off-site CTA; search under Restore, body search off by default (restore_workspace.js:58), unlabeled input. Fix: owner view vs admin disclosure; Search as top-level nav + global field with "/" shortcut; rename Accounts→Mailboxes. → shape, clarify

## Persona red flags
- Alex: attachment search needs Restore→chip→options→type; no shortcut; Sync All no per-row feedback; Stats "—" despite known totals.
- Sam: can't open status pills; ~10 tabs to content; mobile hamburger unreachable; ~10px legend/version; colour-only status.
- Giulia (non-technical owner): "31000 Messages", Needs Attention for a normal sync, "0 of 1 healthy" with green dot, Maildir jargon, "click any stage to manage" with no rights; indeterminate progress despite known totals; unusable off-site CTA; never told "your mail is safe, last copied 9 min ago".

## Minor observations
"1 errors" plural; LEXICON violations ("Add your first email backup", "Backup completed"); Title Case labels; ~91 inline onclick/script/style in templates (CLAUDE.md rule); ignores prefers-color-scheme; uneven stat-card spans; profile password form off-axis; schedule in KPI type; raw audit actions + browser-locale date placeholder; Retry on AUTHENTICATIONFAILED; Delete not in danger zone; treemap has no fallback for unexpected folder_stats shape; cross-check DETAIL_REDESIGN_SPEC.md before detail-page changes. (A detail-page 500 seen during the run was caused by malformed seed data, not the product.)

## Questions
1. Should the web UI shrink to three jobs (Is it safe? Find it. Get it back.) with configuration secondary, given MCP is the busiest door?
2. What if the home were a search box with a one-sentence safety verdict and the chain drawn above it?
3. Should the non-technical owner ever see Dovecot, XOAUTH2, cron, Repository, Snapshot?
