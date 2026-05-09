---
name: Next Session
description: Session 11 priorities — Dovecot admin tools, FTS reindex, restore separator warning, backlog
type: project
---

## Session 11 Priorities

### 1. Admin: Dovecot mailbox health check
- Button to run `doveadm force-resync -u {username} "*"` from UI
- Show corrupted cache errors (like the UID 97 broken virtual size issue found in session 10)
- Accessible from admin panel or account detail page

### 2. Admin: FTS reindex trigger
- Button to run `doveadm fts rescan -u {username}` and `doveadm index -u {username} "*"` from UI
- Currently no way to trigger reindex without CLI access
- Show progress/status of indexing

### 3. Restore: separator collision warning
- When target IMAP uses `.` as separator, show warning before restore starts
- List folders that will be renamed (e.g., `unroll.me` → `unroll_me`)
- Input field to choose escape character (default `_`)
- Needs new lightweight endpoint to query target separator

### 4. Restore: end-to-end test
- Full restore Live→Molotov with the reconnect and separator fixes in place
- Verify skip duplicates works correctly
- Test folder mode and selection mode

## Feature Backlog
1. Tika attachment search (container decision pending)
2. Export mbox/EML
3. Import mbox/EML
4. Retention policies
5. i18n
6. Sender analysis / stats
