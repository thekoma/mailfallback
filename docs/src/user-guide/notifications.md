# Notifications

MFB can ping you the moment a mailbox needs attention, or when a long-running
job finishes — without you having to keep the dashboard open. Delivery goes
through [Apprise](https://github.com/caronc/apprise), an open-source
notification library that speaks dozens of services through one URL syntax:
ntfy, Telegram, Gotify, Discord, Slack, email, generic webhooks, and
[80+ others](https://github.com/caronc/apprise/wiki). MFB itself only builds
the message and hands it to Apprise — it never talks to a specific service's
API directly.

Notification channels are personal: each user manages their own, from their
own **Profile** page. There is no separate admin notifications screen.

## Add a channel

1. Go to **Profile** in the sidebar.
2. Scroll to **Notification channels**.
3. Give the channel a **Label** (e.g. "Phone (ntfy)") and paste an **Apprise
   URL** (e.g. `ntfy://ntfy.sh/your-topic`). The Profile page shows
   copy-to-clipboard examples for ntfy, Telegram, Gotify, and Discord, and
   links to the [full Apprise service list](https://github.com/caronc/apprise/wiki)
   for everything else, including Slack and generic webhooks.
4. Check the boxes for the events you want on this channel (see below).
5. Choose a **Format** — Text for a human-readable message, or JSON for a
   machine-readable body suited to webhooks and automations (see
   [JSON envelope](#json-envelope) below).
6. Click **Add channel**.

!!! note "The URL is stored encrypted"
    The Apprise URL is encrypted at rest with the same Fernet scheme used for
    IMAP credentials (see [Security Model](../architecture/security.md)). The
    Profile page only ever displays it masked as `scheme://…` — even to the
    owning user — and editing a channel requires pasting a new URL rather than
    revealing the old one.

Each existing channel can be tested (**Test** sends an immediate sample
notification), edited (label, URL, events, format), toggled on/off, or
deleted, all from its card on the Profile page.

## Events

Events are grouped into two categories in the UI. A channel only receives the
events its checkboxes select — a channel with no events checked never
notifies.

**Problems** — self-recovering states are included here too, so you can be
alerted the moment a mailbox needs a look, without waiting for it to become a
hard failure:

| Event key | Fires when |
|---|---|
| `needs_reauth` | The account's OAuth2 sign-in has expired and re-authentication is required before syncing can resume. |
| `sync_error` | A sync attempt failed with a real error (not a self-recovering pause). |
| `sync_paused` | Sync paused itself — daily budget reached or the provider throttled the connection. Resumes automatically. |
| `stale` | No successful sync has completed for the account in over a week. |

**Activity** — informational, not deduplicated, one notification per occurrence:

| Event key | Fires when |
|---|---|
| `sync_completed` | A regular sync finishes successfully. |
| `initial_sync_completed` | The account's first sync finishes successfully. |
| `restore_completed` | A mailbox-side restore job (IMAP→IMAP) finishes. |
| `backup_completed` | MFB's own configuration backup finishes. Sent to every admin, not tied to a mailbox. |
| `account_added` | A new mailbox is connected. |

These are the only event keys `notification_service.py` emits — there is no
generic "wildcard" subscription.

!!! note "Problem events are deduplicated, activity events are not"
    A problem event (e.g. `sync_error`) notifies once when the account enters
    that state, and stays quiet on every subsequent sync attempt that hits the
    same problem — it fires again only after the account recovers and then
    re-enters a (possibly different) problem state. Activity events have no
    such guard: every completed sync, restore, or configuration backup sends a
    fresh notification if a channel is subscribed to it.

## Who gets notified

- **Mailbox events** (`needs_reauth`, `sync_error`, `sync_paused`, `stale`,
  `sync_completed`, `initial_sync_completed`, `restore_completed`,
  `account_added`) go to every owner of that account — direct owners, not
  group members — on each owner's own enabled channels subscribed to that
  event.
- **`backup_completed`** is the one exception: it isn't tied to a mailbox at
  all. It notifies every admin user's own channels, because a configuration
  backup covers the whole installation, not a single account.

## JSON envelope

Channels set to the **JSON** format receive a single JSON object as the
notification body (the **Text** format instead sends a plain-language title
and message, with no envelope):

```json
{
  "event": "sync_completed",
  "title": "Personal Gmail: sync complete",
  "message": "4213 messages backed up",
  "timestamp": "2026-07-06T14:32:10.512+00:00",
  "account": {
    "id": "b6c1...",
    "name": "Personal Gmail",
    "email": "user@gmail.com",
    "provider": "gmail",
    "store": "default"
  },
  "details": {
    "messages": 4213,
    "unread": 12,
    "size_bytes": 8321654210,
    "duration_seconds": 42.7,
    "last_sync_at": "2026-07-06T14:32:10.500+00:00"
  }
}
```

Top-level fields are the same for every event:

| Field | Type | Notes |
|---|---|---|
| `event` | string | One of the event keys above. |
| `title` | string | Same title shown in the Text format. |
| `message` | string | Same one-line message shown in the Text format. |
| `timestamp` | string | UTC, ISO 8601, generated when the notification is sent. |
| `account` | object or `null` | Present for mailbox events; `null` for `backup_completed`, which has no account. |
| `details` | object | Event-specific fields, listed below. Empty object `{}` for the four problem events, which don't carry extra details. |

`account`, when present, is a plain snapshot with:

| Field | Notes |
|---|---|
| `id` | Account UUID. |
| `name` | Account's display name. |
| `email` | The mailbox's email address. |
| `provider` | e.g. `gmail`, `microsoft`, `generic`. |
| `store` | Mail store name — omitted if the store can't be resolved. |

`details` by event:

| Event | `details` fields |
|---|---|
| `account_added` | `email`, `provider`, `imap_host` |
| `initial_sync_completed` | `messages`, `folders`, `duration_seconds` |
| `sync_completed` | `messages`, `unread`, `size_bytes`, `duration_seconds`, `last_sync_at` (nullable) |
| `restore_completed` | `restored`, `skipped`, `failed`, `total`, `duration_seconds` |
| `backup_completed` | `repository`, `backend`, `duration_seconds`, `completed_at` (nullable) |
| `needs_reauth`, `sync_error`, `sync_paused`, `stale` | none — `details` is `{}` |

!!! tip "Wiring a webhook"
    If you're pointing a channel at a generic webhook target (Apprise's
    `json://` / `jsons://` scheme, or a service that accepts a raw JSON body),
    set the channel's Format to JSON and parse on `event` — the schema above
    is stable across all events of that kind.
