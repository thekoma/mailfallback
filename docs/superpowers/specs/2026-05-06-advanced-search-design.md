# Advanced Search — Design Spec

## Overview

Replace the basic text-only search in the restore "Search & pick" mode with a Roundcube-style advanced search panel. Collapsible by default — simple text search when collapsed, full filter panel when expanded. All filters map directly to IMAP SEARCH criteria.

## UI Layout

The search panel has two states:

**Collapsed (default):** Search text field + folder dropdown + "Advanced" toggle button + Search button. Same as current behavior but with the toggle added.

**Expanded:** Full filter panel below the text field:

```
┌──────────────────────────────────────────────────────┐
│ Folder [INBOX (695) ▼]  [Search text...] [▼Advanced] │
├──────────────────────────────────────────────────────┤
│ Search in:                                           │
│ ●─ Subject   ●─ Sender ▼    ○─ Recipient ▼   ○─ Body│
│                  ●─ From        ○─ To                │
│                  ○─ Reply-To    ○─ Cc                │
│                  ○─ Followup    ○─ Bcc               │
│                                                      │
│ ○─ Entire message                                    │
│                                                      │
│ Type [All ▼]   Scope [Current folder ▼]              │
│ Since [________]   Before [________]                 │
│                                                      │
│              [🔍 Search]                             │
└──────────────────────────────────────────────────────┘
```

### Toggle switches

Pico CSS `role="switch"` checkboxes for:
- **Subject** (default: on)
- **Sender** (default: on) — expandable chevron reveals sub-toggles: From (default: on), Reply-To, Followup-To
- **Recipient** — expandable chevron reveals: To, Cc, Bcc
- **Body**
- **Entire message** — mutually exclusive with all above. When activated, disables all other field toggles. Maps to IMAP `TEXT` (searches everywhere).

### Type dropdown

Options: All (default), Unread, Flagged, Unanswered, Deleted, Not deleted, With attachment.

IMAP mapping:
- All → no filter
- Unread → `UNSEEN`
- Flagged → `FLAGGED`
- Unanswered → `UNANSWERED`
- Deleted → `DELETED`
- Not deleted → `UNDELETED`
- With attachment → `HEADER Content-Type multipart/mixed`

### Date filters

Two `<input type="date">` fields: Since and Before. Both optional.

IMAP mapping: `SINCE 6-May-2026`, `BEFORE 7-May-2026`. Date format conversion from HTML date (YYYY-MM-DD) to IMAP date (D-Mon-YYYY) in the backend.

### Scope

Dropdown: Current folder (default), All folders.

- **Current folder**: SEARCH in the folder selected in the folder dropdown.
- **All folders**: iterate all folders for the account, SEARCH in each, aggregate results. Limit 100 total results. Results table shows an extra "Folder" column.

"This and subfolders" is omitted — Dovecot namespace doesn't support recursive IMAP SEARCH natively, and the sub-folder hierarchy in MFB is flat (no meaningful nesting beyond [Gmail]/).

## Search Feedback

- On Search click: button becomes disabled, text changes to "Searching...", spinner icon appears
- Results area shows HTMX loading indicator via `hx-indicator`
- On completion: button restores, results appear
- Prevents double-click (disabled button blocks multiple requests and avoids creating multiple temp Dovecot users)

## Backend — IMAP SEARCH Builder

### Endpoint

`GET /restore/partials/messages` — existing endpoint, extended with new query params:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source_account_id` | string | required | Account to search |
| `search_query` | string | required | Search text (multi-word) |
| `search_folder` | string | required | Folder name or "*" for all |
| `search_in` | string | `"text"` | Comma-separated field list |
| `type_filter` | string | `"all"` | Type filter key |
| `date_since` | string | `""` | ISO date YYYY-MM-DD |
| `date_before` | string | `""` | ISO date YYYY-MM-DD |

### `search_in` values

`subject`, `from`, `reply_to`, `followup_to`, `to`, `cc`, `bcc`, `body`, `text`

When `text` is present, all other fields are ignored — IMAP `TEXT` searches everywhere.

### IMAP SEARCH construction

For each word in the query, build an OR group across all selected fields:

- 1 field active: `SUBJECT "word"`
- 2 fields active: `OR SUBJECT "word" FROM "word"`
- 3 fields active: `OR SUBJECT "word" OR FROM "word" BODY "word"`
- N fields: nest ORs — `OR field1 OR field2 OR field3 fieldN`

Multiple words are AND-joined (IMAP implicit AND between criteria groups).

Type and date criteria are appended after the text criteria:
```
OR SUBJECT "fattura" FROM "fattura"  OR SUBJECT "google" FROM "google"  UNSEEN  SINCE 1-Jan-2024
```

### Scope "All folders"

When scope is all: get folder list, iterate each, SEARCH, collect results with folder name attached. Stop at 100 total results. Results include `folder` field in the response dict.

## Subject Encoding Fix

Decode MIME-encoded subjects (`=?UTF-8?Q?...?=`) using `email.header.decode_header()` before returning to the template. Applies to both the search results partial and the existing message listing.

## Files to modify

| Action | File |
|--------|------|
| Modify | `src/mailfallback/templates/restore.html` — replace search panel HTML |
| Modify | `src/mailfallback/static/js/app.js` — advanced toggle, mutual exclusion, param builder |
| Modify | `src/mailfallback/static/css/style.css` — search panel styles, dark mode |
| Modify | `src/mailfallback/routers/ui_restore.py` — extend messages partial with new params |
| Modify | `src/mailfallback/templates/partials/restore_messages.html` — add folder column, decode subjects |
