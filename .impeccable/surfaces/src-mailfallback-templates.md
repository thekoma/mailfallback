---
version: 1
slug: "src-mailfallback-templates"
primary_target: "src/mailfallback/templates"
related_targets: ["src/mailfallback/static/css/style.css"]
---

# Surface brief: MailFallBack web UI (all surfaces)

Scope: the whole web UI — shell/navigation, owner home, admin dashboard, mailbox list + detail, search/restore/recover/staging, admin pages (users, mail stores, repositories, groups, system, audit), profile + tokens, login, wizard + forms, empty/error/loading states, mobile. Visitor mode: **Operate**.

Audience and job: owners (family members, and the admin on their own mailboxes) come to learn "is my mail safe?", find a message or attachment, and get it back — low frequency, high stress on the bad day. The admin runs everything densely, often in the evening and on a phone after a notification. Role-split views: non-admins always see the owner view; the admin lands on the admin view and reaches the owner view of their own mailboxes in one click.

Content/proof: resolver states and timestamps (`services/mailbox_status.py`, 13 states), message/size counts, full-text and attachment search. No invented claims. Copy and terminology as already written (LEXICON.md + the clarify pass).

Constraints: Jinja2 + HTMX + Alpine, server-rendered; all CSS in `static/css/style.css`, all JS in `static/js/`, no inline CSS/JS; vendored assets; routes, data model, agent/MCP API and resolver unchanged; WCAG AA in light and dark; 12px text floor; `prefers-color-scheme` honoured; no horizontal scroll at 390px. Code-led build (no image generation).

Unresolved for the builder (decide, don't invent claims): the exact self-hosted grotesque; the provider "stamp" drawn with neutral marks, never third-party logos.

## Direction contract

THESIS: Every mailbox is an airmail envelope in your care: its postmark tells you when it was last copied, and a hand stamp tells you — only when needed — what is wrong. Refuses the category default: a Pico/SaaS admin console of same-size KPI cards, sidebar badges and neon-on-dark status pills.

OWN-WORLD: Pale blue onionskin ground (never neutral white; navy-night paper with chalk ink in dark mode), navy ink, the red-and-blue par-avion chevron used only as the shell's frame edge in its own hues (never as a status), circular postmarks with date and time in tabular figures, rotated rubber hand stamps with their own shapes per state (Delivered, In transit, Held at post office, Postage due, Delayed, Return to sender — the only red —, Not delivered, Suspended), registered-mail labels for actions, a provider stamp in the envelope corner. One self-hosted grotesque at one weight, hierarchy by size; strict declared grid.

STORY: The owner sees one postmark verdict and believes their mail is safe or knows the one thing to do; they search from the address field and forward what they need back. The admin reads the sorting-office ledger, sees the route Source → Local backup → Repository → Snapshot in the same space as the list, and acts on the stamped rows only.

FIRST VIEWPORT: Owner home — one dominant envelope across the content column: postmark "Safe · 30 SEP 2026 · 21:04" (or the stamp of what needs action) top-right, addressee line with the owner's mailboxes count; directly below, the address field (search messages and attachments) as the primary control; then the owner's envelopes stacked, state stamp first. Admin dashboard — the ledger fills the column: route strip at top, then dense rows (mailbox, owner, postmark, stamp, action), a single system-health indicator in the top bar.

FORM: Airmail envelope / aerogramme stationery — position 7 of the ordered grounded list (re-roll round 1), assigned by the roll; seed key 6483e7ac. Raises: one grotesque one weight, paper never neutral (design annual); one envelope dominates each screen (zoo map); map and list are one space on the admin view (doujin catalog); honest tabular live numbers, no invented percentages (variable specimen); strict declared grid (Crouwel). Signature interaction: on sync completion the postmark stamps down with the new time (one short authored motion, off under prefers-reduced-motion).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
