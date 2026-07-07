# Runtime Flows

Sequence and flow diagrams for the main runtime paths. For the static
component/dependency view, see [Overview](overview.md).

## Backup sync

How a scheduled or manual sync pulls mail from upstream into local Maildir.

```mermaid
flowchart TD
    SCHED["APScheduler<br/>(periodic) / manual trigger"] --> JOB["create sync_job"]
    JOB --> GATE{"budget / pause<br/>gate ok?"}
    GATE -- "paused" --> STOP["skip — self-recovering"]
    GATE -- "ok" --> CFG["generate .mbsyncrc"]
    CFG --> MBSYNC["mbsync subprocess"]
    MBSYNC --> UPSTREAM["Upstream IMAP<br/>(Gmail / Outlook / ...)"]
    MBSYNC --> MAILDIR["/data/mailboxes/{uuid}/"]
    MBSYNC --> METER["byte meter → daily ledger"]
    MAILDIR --> INDEX["Dovecot FTS index<br/>(+ Tika for attachments)"]
```

## Restore & staging

How backed-up mail is pushed back to a live server, optionally curated in a
staging workspace first.

```mermaid
flowchart TD
    SRC["Backed-up Maildir<br/>or off-site repository"] --> SEARCH["search / browse<br/>(FTS + Tika)"]
    SEARCH --> STAGE["staging workspace<br/>(curate selection)"]
    STAGE --> JOB["restore job"]
    JOB --> MAP["folder mapping +<br/>duplicate detection"]
    MAP --> PUSH["push via IMAP APPEND"]
    PUSH --> TARGET["Target IMAP server"]
```

## Off-site backup

How local Maildir is copied to an off-site restic repository.

```mermaid
flowchart TD
    MAILDIR["/data/mailboxes/{uuid}/"] --> RESTIC["restic backup"]
    POLICY["BackupPolicy<br/>(schedule, retention)"] --> RESTIC
    RESTIC --> REPO["Repository<br/>(S3 / local restic repo)"]
    REPO --> SNAP["snapshots<br/>(retention pruned)"]
```

## Authentication

The three ways a user authenticates to MFB.

```mermaid
flowchart TD
    USER["User"] --> CH{"auth method"}
    CH -- "password" --> SQL["SQL passdb<br/>(bcrypt)"]
    CH -- "OIDC SSO" --> OIDC["OIDC provider<br/>+ group sync"]
    CH -- "account OAuth2" --> OAUTH["Google / Microsoft<br/>OAuth2 token"]
    SQL --> SESSION["session"]
    OIDC --> SESSION
    OAUTH --> ACCT["stored per-account<br/>(encrypted token)"]
```
