# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Primary: the self-hosting admin.** Technical, runs MFB on their own infrastructure (Docker Compose or a homelab Kubernetes cluster via the Helm chart). Configures sources, mail stores, repositories, users and groups, and is usually also a mailbox owner.
- **Secondary: family members.** Non-technical users who own or share a few mailboxes. They log in to check that their mail is safe, browse it through the webmail, or get something back. They never touch stores, repositories or system settings.
- **Agents acting for the admin.** The admin reaches MFB heavily through the MCP server (`/mcp`) and the agent API (`/api/v1/agent`): search, message and attachment retrieval, sync triggers. The web UI is not the only front door, and often not the busiest one.

## Product Purpose

An email safety net. MFB keeps an independent, continuously refreshed local copy of each mailbox (mbsync → Maildir), optionally pushes encrypted snapshots off-site (restic → Repository), and exposes the copy read-only over IMAP and webmail. It exists because a cloud provider can lock a person out of decades of correspondence without warning.

Success means two things:
1. **Quiet confidence.** An owner opening MFB can tell at a glance whether every mailbox is healthy and current, and it is obvious when one needs attention.
2. **Fast recovery.** On the bad day (a lockout, a deleted thread, a lost attachment), the owner finds the message or attachment quickly and gets it back.

## Positioning

The copy is on hardware the owner controls, it keeps working when the provider doesn't, and it is searchable down to the contents of attachments. It is a working fallback (read-only IMAP, webmail, search, restore to any mailbox), not an archive file on a disk. It is also agent-native: the same search and retrieval are exposed to the owner's AI tools over MCP with scoped tokens.

## Operating Context

- **Periodic check-in.** Mostly set-and-forget. The owner opens the dashboard to confirm everything is syncing, and reacts to notifications (Apprise: re-auth needed, sync error, sync paused, stale mailbox).
- **Search and attachment retrieval.** The capability the admin values most: full-text search across mailboxes, including attachment contents (Apache Tika), and pulling attachments out. Used from the UI (restore workspace) and even more from agents over MCP.
- **Crisis recovery.** Restore staging workspace (search → stage → push back to a mailbox), and Recover (snapshot → new suspended mailbox).
- **Administration.** Occasional, done by the admin: accounts, OAuth re-authentication, mail stores, store migration, repositories and backup policies, users, groups/SSO, audit log, config backup.
- Self-recovering states (throttled, transient, budget paused, interrupted) are normal operation, not failures. Only `error` is a real problem.

## Capabilities and Constraints

- **Stack (fixed by the codebase):** server-rendered Jinja2 templates, HTMX, Alpine, Pico CSS, Lucide icons. Frontend assets are vendored and pinned in `static/vendor/vendor.json`. There is no JS build step.
- **Code rules:** all CSS lives in `static/css/style.css` (classes only, no inline styles). All JS lives in `static/js/` (no inline `<script>`). Existing dark mode is persisted per user with no flash on reload. The mobile breakpoint is 768px, where the sidebar becomes a drawer.
- **Vocabulary:** `LEXICON.md` is binding for all user-facing copy. The UI is English only. Bare "Backup" as a noun is banned. The four-stage model is Source → Local backup → Repository → Snapshot, and every screen should let the user place themselves in that chain.
- **Roles:** admin and user. Users see only mailboxes they own or share through a group. Admin surfaces are hidden from users.
- **Open to a full UI rewrite:** the owner explicitly accepts replacing the current look and page structure when justified. Product functions, vocabulary, and the technical constraints above stay.

## Brand Commitments

- Name: **MailFallBack**, short form **MFB**. Tagline in the README: "Your email safety net".
- A logo exists (`src/mailfallback/static/favicon.svg`). It is not declared binding. The owner is open to replacing the visual identity.

## Evidence on Hand

- Current UI screenshots: `docs/screenshots/` (e.g. `02-dashboard.png`), used in the README and the MkDocs site.
- User-facing documentation: `docs/src/` (getting-started, user-guide, admin-guide, architecture).
- Vocabulary audit and reasoning: `docs/superpowers/analysis/2026-05-10-ux-lexicon-audit/` (local, untracked).
- No public release has shipped yet and there is no external user base. There are no testimonials, customer logos or usage statistics, and none may be invented.

## Product Principles

1. **Calm by default, loud only when it matters.** Healthy and self-recovering states read as quiet. Real errors and required actions (re-auth, `error`) are unmistakable.
2. **Always show where you are in the chain.** Source → Local backup → Repository → Snapshot is the mental model every screen reinforces.
3. **Finding beats browsing.** Search, and attachment search especially, is the most valuable path. Getting from "I need that email" to the message or file should be short.
4. **Two audiences, one interface.** Admin power stays out of the way of a non-technical family member checking their own mailboxes.
5. **The UI and the agent surface tell the same story.** Terms, states and scopes match between the web UI, the agent API and MCP.
