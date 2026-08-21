# Agent API

`/api/v1/agent` is a read-mostly HTTP API for wiring an AI agent, script, or
integration up to a user's mailboxes: search, fetch a message and its
attachments, hand off to IMAP directly, and trigger or poll a sync. It is the
only versioned, externally contracted surface in MFB — everything under
`/api/restore` is the web UI's private contract and follows the UI's own
shapes instead.

This page is aimed at whoever is wiring an agent up to it. For how the
authentication and scope model is built, see the
[Security Model](../architecture/security.md#agent-api-authentication).

The same tokens also work over [MCP](#model-context-protocol), for a client
that speaks that protocol instead of raw HTTP.

## Creating a token

On the **Profile** page, under **Access tokens**, create a token with a name
and one or more scopes:

| Scope | Grants |
|-------|--------|
| `imap` | IMAP login only (Dovecot, Roundcube). No access to this HTTP API. |
| `mail:read` | The read endpoints below: mailboxes, search, message and attachment fetch, IMAP-coords lookup. |
| `sync:trigger` | Both sync endpoints — queuing a sync **and** reading a job's status. |

Pick `mail:read` for an agent that only searches and reads mail, and add
`sync:trigger` if it should also be able to ask for a fresh sync or poll one
it queued. A token holding only `imap` is a valid IMAP password but gets a
403 from every route on this API — that separation is intentional, since
`imap` is the scope the IMAP-only skills use and it shouldn't imply anything
more.

The token is shown exactly once, at creation, as `mfb_<prefix>_<secret>`.
Store it then; it can't be recovered later, only revoked and replaced.

## Authenticating

Send the token as a standard bearer header:

```bash
curl -H "Authorization: Bearer mfb_abc123..._def456..." \
  https://mfb.example.com/api/v1/agent/mailboxes
```

The scheme name (`Bearer`) is matched case-insensitively. If the header names
the bearer scheme at all, the request is treated as a token attempt end to
end: a token that turns out to be revoked, expired, or malformed — or whose
owning user is disabled or is being migrated to a different mail store —
gets a plain `401`, and the request never falls back to a browser session
cookie that might be attached to the same connection.

## Endpoints

### `GET /mailboxes`

The mailboxes this token's user can search, with what is actually indexed in
each — `indexed_messages` and `folders` come from the search index, not the
provider, so they answer "what can I search right now" rather than "how many
messages does the provider report".

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://mfb.example.com/api/v1/agent/mailboxes
```

```json
[
  {
    "account_id": "8f2b...",
    "name": "Work Gmail",
    "email_address": "andrea@example.com",
    "provider": "google",
    "last_sync_at": "2026-08-20T09:14:00Z",
    "indexed_messages": 41823,
    "folders": ["INBOX", "Sent", "[Gmail]/All Mail"]
  }
]
```

Requires `mail:read`.

### `POST /search`

Indexed search across every mailbox the token's user can see.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "invoice fiscozen", "deep": false, "page_size": 20}' \
  https://mfb.example.com/api/v1/agent/search
```

```json
{
  "results": [
    {
      "account_id": "8f2b...",
      "message_id": "<abc@mail.example.com>",
      "message_id_hash": "a1b2c3...",
      "subject": "Invoice #4471",
      "from_addr": "billing@fiscozen.it",
      "from_name": "Fiscozen",
      "to_addrs": ["andrea@example.com"],
      "date_sent": "2026-07-02T10:03:00Z",
      "folder_path": "INBOX",
      "alive_in_live": true,
      "snapshots": ["2026-07-03T00:00:00Z"],
      "has_attachments": true,
      "attachments": [
        {"filename": "invoice-4471.pdf", "ext": "pdf", "size_bytes": 88213, "part_index": 2}
      ],
      "body_matched": null
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "partial": false
}
```

Request fields: `query`, `account_ids` (omit for "every visible mailbox"),
`range_start` / `range_end`, `include_deleted` (default `true`),
`snapshot_id`, `deep`, `page`, `page_size` (max 200). There is no
`include_all` field — see [What this API does not
do](#what-this-api-does-not-do).

Setting `deep: true` additionally runs a live body search over Dovecot,
bounded by a server-side timeout. `body_matched` is `true`/`false` per hit
only when `deep` was requested; it is `null` when it wasn't, so a caller
can't confuse "didn't match" with "wasn't checked". If the deep pass times
out partway through, the response still comes back with whatever it found
so far and `"partial": true` — treat that as "more may exist, not all
folders were reached" rather than as an error.

Requires `mail:read`.

### `POST /search-attachments`

Search attachments by filename, and by extracted text when the optional Tika
integration is enabled.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "imponibile", "include_content": true, "exts": ["pdf"]}' \
  https://mfb.example.com/api/v1/agent/search-attachments
```

```json
{
  "results": [
    {
      "account_id": "8f2b...",
      "message_id": "<abc@mail.example.com>",
      "message_id_hash": "a1b2c3...",
      "part_index": 2,
      "filename": "invoice-4471.pdf",
      "ext": "pdf",
      "size_bytes": 88213,
      "content_snippet": "...imponibile € 1.200,00...",
      "subject": "Invoice #4471",
      "from_addr": "billing@fiscozen.it",
      "folder_path": "INBOX",
      "date_sent": "2026-07-02T10:03:00Z",
      "alive_in_live": true,
      "snapshots": []
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50,
  "content_search_available": true
}
```

`content_search_available` says whether `include_content` means anything on
this deployment — without it, a caller can't tell "no matches" apart from
"content search is switched off here". Request fields: `query`,
`account_ids`, `exts`, `min_size` / `max_size`, `include_content`,
`range_start` / `range_end`, `page`, `page_size`.

Requires `mail:read`.

### `GET /messages/{account_id}/{message_id_hash}`

Headers, a body snippet, and the attachment list for one message —
`message_id_hash` and `part_index` come straight out of a search hit, so
finding a message and fetching it is a two-call sequence with no
re-derivation in between.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://mfb.example.com/api/v1/agent/messages/8f2b.../a1b2c3...
```

```json
{
  "subject": "Invoice #4471",
  "from_addr": "billing@fiscozen.it",
  "from_name": "Fiscozen",
  "to_addrs": ["andrea@example.com"],
  "date_sent": "2026-07-02T10:03:00Z",
  "folder_path": "INBOX",
  "alive_in_live": true,
  "source": "live",
  "body_snippet": "Please find attached your invoice...",
  "attachments": [
    {"filename": "invoice-4471.pdf", "ext": "pdf", "size_bytes": 88213, "part_index": 2}
  ]
}
```

`source` is `"live"` when served from the current Maildir, or
`"snapshot:<id>"` when the message is gone from the live mailbox and was
served from the newest snapshot that still holds it.

Requires `mail:read`.

### `GET /messages/{account_id}/{message_id_hash}/attachments/{part_index}`

Raw attachment bytes — the `part_index` from a search hit or from
`GET /messages/{account_id}/{message_id_hash}` addresses exactly one
attachment, so a search result carries everything needed to download it
without a separate lookup step.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://mfb.example.com/api/v1/agent/messages/8f2b.../a1b2c3.../attachments/2 \
  -o invoice-4471.pdf
```

The response is always `application/octet-stream` with
`X-Content-Type-Options: nosniff`, regardless of the attachment's real type —
a hostile HTML or SVG attachment downloads as a file, it never renders on
MFB's origin. Every download is audited the same way the UI's own attachment
download is.

Requires `mail:read`.

### `POST /imap-coords`

Resolves Message-IDs (as returned by search) to live IMAP folder keys and
UIDs, so an agent can hand off to an existing IMAP client instead of
re-fetching bytes over HTTP — search here, then `SELECT` and `FETCH` there.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"account_id": "8f2b...", "message_ids": ["<abc@mail.example.com>"]}' \
  https://mfb.example.com/api/v1/agent/imap-coords
```

```json
{
  "resolved": {
    "Work Gmail (andrea@example.com)/INBOX": ["1523"]
  },
  "missing": [],
  "imap_unavailable": false
}
```

`resolved` keys are namespace-prefixed folder names exactly as Dovecot
publishes them — `SELECT` them as-is, a bare folder name will not match.
Values are real IMAP UIDs. Message-IDs beyond the first 200 in a single
request are silently ignored (neither resolved nor reported missing) — cap
each request to 200 IDs or fewer.

`imap_unavailable: true` means Dovecot itself could not be reached, so every
ID that would otherwise have been resolved landed in `missing` for that
reason rather than because it was checked and not found. Treat that case as
"retry", not as "these messages don't exist" — a single folder failing to
resolve on an otherwise-reachable connection does *not* set this flag, it
just adds those IDs to `missing` as ordinary non-matches.

Requires `mail:read`.

### `POST /sync/{account_id}`

Queue a sync for one mailbox.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://mfb.example.com/api/v1/agent/sync/8f2b...
```

```json
{
  "job_id": "d4e5...",
  "account_id": "8f2b...",
  "status": "pending",
  "source": "agent",
  "already_queued": false,
  "requested_at": "2026-08-20T09:00:00Z",
  "started_at": null,
  "completed_at": null,
  "failure_kind": null
}
```

If a sync for this mailbox is already pending or running, that existing job
is returned instead, with `"already_queued": true` — this is not an error, it
means the mailbox is already covered. A polling agent needs no special case
for it; it's the ordinary "nothing further to do" outcome, not a failure.

Requires `sync:trigger`.

### `GET /sync/jobs/{job_id}`

Status of one sync job.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://mfb.example.com/api/v1/agent/sync/jobs/d4e5...
```

```json
{
  "job_id": "d4e5...",
  "account_id": "8f2b...",
  "status": "completed",
  "source": "agent",
  "already_queued": false,
  "requested_at": "2026-08-20T09:00:00Z",
  "started_at": "2026-08-20T09:00:03Z",
  "completed_at": "2026-08-20T09:01:47Z",
  "failure_kind": null
}
```

`status` is one of the ordinary job states; `failure_kind` (when set) is one
of `throttled` / `budget_paused` / `transient` / `interrupted` / `error` —
only `error` is a real failure, the others are self-recovering pauses that
will resolve on their own.

This route needs the `sync:trigger` scope, not `mail:read`, even though it
only reads a job's status — deliberately, so a read-only agent can search and
fetch mail without also being able to find out whether a sync it didn't
trigger is running.

## The search-then-fetch flow

A typical agent session looks like:

1. `POST /search` (or `/search-attachments`) with a query.
2. Take a hit's `account_id` + `message_id_hash`, and, for a specific file,
   the attachment's `part_index`.
3. `GET /messages/{account_id}/{message_id_hash}` for the headers and a body
   snippet (capped at 2048 characters — see the endpoint reference above),
   or go straight to
   `GET /messages/{account_id}/{message_id_hash}/attachments/{part_index}`
   for the file.

That pairing — `message_id_hash` plus `part_index` — is what lets an agent go
from "find the invoice" to "download the PDF" in one pass, with nothing to
look up in between. If the agent instead wants to keep working through an
existing IMAP client rather than downloading over HTTP, `POST /imap-coords`
takes the same search hits' Message-IDs and turns them into folder keys and
UIDs that client can `SELECT`/`FETCH` directly.

## Failure modes an agent must handle

| Condition | Response | Meaning |
|-----------|----------|---------|
| Missing, malformed, expired, or revoked token — or a token whose owning user is disabled or is being migrated to a different mail store | `401` | Not authenticated. Never falls back to a session. |
| Token valid, but missing the scope the route requires | `403` | Authenticated, but not authorized for this action. |
| A mailbox, message, attachment, or job the caller cannot see | `404` | Never `403` — the API does not confirm that something exists if the caller isn't allowed to see it. |
| `message_id_hash` is not valid hex | `400` | Malformed request, not an authorization outcome. |
| Deep search (`deep: true`) hits its timeout | `200` with `"partial": true` | Results so far, not everything checked. Not an error. |
| `POST /imap-coords` can't reach Dovecot | `200` with `"imap_unavailable": true` | The IDs in `missing` were never checked — retry, don't conclude the mail is gone. |
| `POST /sync/{account_id}` on a mailbox already syncing | `200` with `"already_queued": true` | The existing job is returned; nothing further needed. |
| `POST /sync/{account_id}` on a suspended or migrating account, or a self-recovering pause | `409` | Refused, naming the reason. Unlike the web UI, this route never overrides a pause on the caller's behalf — an agent cannot weigh burning the provider's daily quota the way a human triggering it manually can. |
| The attachment behind a download exceeds the internal extraction cap | `502` with `"attachment too large to extract"` | The message bytes were found but are too large to safely parse for that one part; not an authentication or authorization outcome. |

## What this API does not do

- **No writes and no sending.** Every route is a read, a search, or a sync
  trigger — there is no endpoint that modifies, deletes, or sends mail.
- **No access to another user's mail, ever — including for an admin.**
  `include_all` does not exist as a field on any request model here. A token
  minted by an admin user sees exactly that admin's own mailboxes, the same
  as any other user's token would.
- **`mail:read` does not imply `imap`.** A token needs the `imap` scope
  separately to use as an IMAP/Roundcube password; `mail:read` only unlocks
  this HTTP API's read endpoints.

## Model Context Protocol

MFB also exposes the same mailbox operations as an MCP server, for clients
that talk MCP instead of HTTP — an agent framework's MCP client, an IDE, or
any tool that lets you add a remote MCP server by URL. It's the same tokens,
the same scopes, and (mostly) the same operations as the REST API above,
wrapped as MCP tools instead of routes.

### Enabling it

MCP is **off by default**. A deployment needs two settings:

- `MAILFALLBACK_MCP_ENABLED=true`
- `MAILFALLBACK_MCP_PUBLIC_URL` — the externally-reachable base URL MFB is
  served at (e.g. `https://mfb.example.com`, no path, no query string).

Setting only the first does not half-start the server: at boot, if
`mcp_enabled` is true but `mcp_public_url` is empty, MFB logs an error and
does not mount `/mcp` at all — there is no sensible default to guess for a
public URL, so it refuses to build a server that would advertise the wrong
one.

`mcp_public_url` is not cosmetic. It feeds the MCP SDK's transport-security
settings on two axes:

- **Issuer/resource metadata** — it's the URL MFB claims to be, in the
  protected-resource metadata a client fetches during discovery.
- **The host allowlist** — the SDK's DNS-rebinding protection rejects any
  request whose `Host` (or `Origin`) doesn't match an allowed value. Get the
  public URL wrong (or leave it as an internal/Docker-network hostname) and
  *every* request gets rejected with `421` or `403` — which looks exactly
  like an authentication failure but isn't one; the token is never even
  reached.

If a reverse proxy in front of MFB already validates `Host` (and MFB is only
ever reached through it), the allowlist is redundant and can be turned off
with `MAILFALLBACK_MCP_DNS_REBINDING_PROTECTION=false`. Leave it enabled
otherwise.

### Connecting

Point an MCP client at `https://<host>/mcp` (streamable HTTP transport) with
the token as a bearer header, same as the REST API:

```
Authorization: Bearer mfb_abc123..._def456...
```

The scheme name is matched case-insensitively, same as the REST surface.

**MFB is not an OAuth 2.1 resource server, and that's a real limitation.**
The MCP specification's authorization model expects a remote server to
support OAuth 2.1 discovery and token issuance. MFB doesn't do that — it
authenticates MCP the same way it authenticates IMAP and the REST API: one
static bearer token, created once on the Profile page, used everywhere. That
means one credential model to reason about instead of three, but it also
means an MCP client that insists on driving a full OAuth discovery-and-grant
flow before it will talk to a server will not connect here. A client that
lets you configure a static `Authorization` header for a remote MCP server
works fine.

### Scopes and tools

Same three scopes as the REST API (`imap`, `mail:read`, `sync:trigger`) —
see [Creating a token](#creating-a-token) above. A token holding only `imap`
reaches the server (authentication succeeds) but every tool call on it is
refused, because none of the eight tools accept the bare `imap` scope. That's
intentional: `imap` is the scope the IMAP-only skills use, and it shouldn't
imply anything more here either.

Eight tools, mirroring the REST endpoints above:

| Tool | Arguments | Scope | Read-only |
|------|-----------|-------|-----------|
| `list_mailboxes` | *(none)* | `mail:read` | yes |
| `search_mail` | `query`, `account_ids`, `range_start`, `range_end`, `include_deleted`, `snapshot_id`, `deep`, `page`, `page_size` | `mail:read` | yes |
| `search_attachments` | `query`, `account_ids`, `exts`, `min_size`, `max_size`, `include_content`, `range_start`, `range_end`, `page`, `page_size` | `mail:read` | yes |
| `get_message` | `account_id`, `message_id_hash` | `mail:read` | yes |
| `download_attachment` | `account_id`, `message_id_hash`, `part_index` | `mail:read` | yes |
| `imap_coords` | `account_id`, `message_ids` | `mail:read` | yes |
| `sync_now` | `account_id` | `sync:trigger` | **no** |
| `sync_status` | `job_id` | `sync:trigger` | yes |

Seven tools are annotated read-only (`read_only_hint: true`), so an MCP
client that surfaces that hint can auto-approve them without a
confirmation prompt. `sync_now` is the only one that changes state — it
queues a sync job — and is annotated accordingly.

The arguments and response shapes match their REST counterparts one for
one (`search_mail` is `POST /search`, `imap_coords` is `POST /imap-coords`,
and so on) — see the endpoint reference above for request fields, response
fields, and the failure-mode table. The same rules apply here: `deep: true`
on `search_mail` can come back with `partial: true` rather than an error,
`sync_now` on an already-syncing mailbox returns the existing job with
`already_queued: true` instead of failing, and `sync_now` refuses outright
(rather than overriding, the way the web UI may) on a suspended, migrating,
or self-recovering-paused account. **Admin role does not travel with a
token here either**: a token minted by an admin sees only that admin's own
mailboxes, exactly like every other token.

Two tools carry MCP-specific notes worth calling out on their own:

- **`download_attachment`** returns the attachment as base64 in
  `content_base64`, inline in the tool result. A part over 5 MiB is refused
  rather than served — base64 inflates the payload by a third and the whole
  thing rides inside one JSON-RPC response, so the cap exists to keep that
  response bounded. Read it as a route, not a dead end: the error names the
  message's folder and points the caller at `imap_coords` to resolve IMAP
  coordinates and fetch the same attachment directly over IMAP instead.
- **`imap_coords`** returns namespace-prefixed IMAP folder keys (exactly as
  Dovecot publishes them — `SELECT` them as-is) and real IMAP UIDs, the
  bridge from an MCP search hit to fetching over an existing IMAP
  connection. `imap_unavailable: true` means Dovecot itself could not be
  reached, so every id that landed in `missing` was never actually checked
  — the right response is to retry, not to conclude the mail is gone.
