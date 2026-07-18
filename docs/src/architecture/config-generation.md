# Centralized Config Generation

MFB generates all configuration files for Dovecot and Roundcube at application startup. This eliminates the need to manually edit sidecar configs and ensures consistency between MFB settings and the services that depend on them.

## How It Works

During the FastAPI lifespan event, MFB calls `generate_all_configs(settings)`, which:

1. Writes Dovecot configuration files to `{confs_path}/dovecot/`
2. If webmail is enabled, writes Roundcube configuration to `{confs_path}/webmail/`
3. Returns the list of written files

The `confs_path` defaults to `/confs`, which maps to Docker shared volumes:

| Volume | Mount in MFB | Mount in Sidecar |
|--------|-------------|-----------------|
| `dovecot_confd` | `/confs/dovecot` (read-write) | `/etc/dovecot/conf.d` (read-only) |
| `webmail_conf` | `/confs/webmail` (read-write) | `/var/roundcube/config` (read-only) |

## Generated Dovecot Files

MFB generates these Dovecot configuration files:

### mfb-ssl.conf

SSL/TLS configuration. By default SSL is disabled (cleartext stays enabled for
in-cluster/internal-network access) and TLS is opt-in via `MAILFALLBACK_DOVECOT_TLS`:

```conf
ssl = no
auth_allow_cleartext = yes
```

When `MAILFALLBACK_DOVECOT_TLS=true`, MFB emits the TLS variant instead, pointing
at a mounted `kubernetes.io/tls` secret (cleartext stays on for in-cluster webmail):

```conf
ssl = yes
ssl_server_cert_file = /etc/dovecot/ssl/tls.crt
ssl_server_key_file = /etc/dovecot/ssl/tls.key
auth_allow_cleartext = yes
```

### mfb-service.conf

Dovecot service bindings. The IMAP listener uses the image default port; MFB only
overrides the doveadm HTTP API binding:

```conf
service doveadm {
  inet_listener http {
    port = 8080
    ssl = no
  }
}
```

### mfb-stats.conf

Prometheus metrics export binding:

```conf
service stats {
  inet_listener http {
    port = 9900
    ssl = no
  }
}
```

### mfb-mail.conf

Maildir layout configuration. Image mail defaults are cleared so the Lua userdb
can build every namespace dynamically, and the filesystem list layout is set:

```conf
# Override image defaults -- Lua userdb creates all namespaces dynamically.
mail_path =
mail_home =
mailbox_list_layout = fs
```

When `MAILFALLBACK_DOVECOT_NFS=true`, NFS-safe mail settings are appended:

```conf
mmap_disable = yes
mail_fsync = always
```

### mfb-acl.conf

ACL configuration for read-only access. Dovecot 2.4.3+ removed the standalone
global ACL file, so ACLs are expressed as settings blocks: a global `readonly`
rule makes every mailbox owner-read-only (`lrs`), and `Staging` mailbox blocks
grant write rights (`lrwstie`) on the restore-curation namespace:

```conf
mail_plugins = acl

protocol imap {
  mail_plugins = acl imap_acl fts fts_flatcurve
}

acl_driver = vfile
acl_globals_only = yes

acl readonly {
  acl_id = owner
  acl_rights = lrs
}

mailbox Staging {
  acl staging {
    acl_id = owner
    acl_rights = lrwstie
  }
}

mailbox Staging/* {
  acl staging_sub {
    acl_id = owner
    acl_rights = lrwstie
  }
}
```

The global `lrs` grant allows lookup (`l`), read (`r`), and write-seen (`s`)
everywhere; all other operations (delete, expunge, insert, write flags) are
denied outside `Staging`. (The legacy standalone `dovecot-acl` file is no longer
generated — MFB actively removes it from upgraded volumes.)

### mfb-fts.conf

Full-text search configuration using Flatcurve:

```conf
mail_plugins {
  fts = yes
  fts_flatcurve = yes
}
fts flatcurve {
  commit_limit = 500
  min_term_size = 2
  rotate_count = 5000
}
```

When Tika is enabled, the decoder is added:

```conf
fts_decoder_driver = tika
fts_decoder_tika_url = http://tika:9998/tika/
```

### mfb-auth.conf

SQL-based authentication with passdb and userdb:

- **passdb** queries the `users` table, checking `enabled = true` and `migrating = false`
- **userdb** uses a Lua script that calls MFB's HTTP API for dynamic namespace resolution

The SQL connection string is built from `MAILFALLBACK_DB_*` settings.

### mfb-lua-userdb.lua

A Lua script that implements Dovecot's userdb lookup:

1. On user login, Dovecot calls the Lua `auth_userdb_lookup` function
2. The script makes an HTTP GET to `http://mailfallback:8000/api/internal/dovecot/userdb/{username}`
3. MFB returns the user's store path, home directory, and a list of accessible accounts
4. The Lua script converts this into Dovecot namespace fields
5. The first account becomes the inbox namespace; others get prefixed namespaces

## Generated Roundcube Files

### custom.php

Roundcube configuration overrides:

- Database connection with `rc_` table prefix (shares PostgreSQL with MFB)
- IMAP connection to Dovecot (`dovecot:31143`)
- `use_subscriptions = false` (list all folders via IMAP LIST)
- OAuth2 configuration (if webmail SSO is enabled)
- Plugin list: `archive`, `zipdownload`, `subscriptions_option`

## FTS Config Change Detection

MFB tracks whether the FTS configuration has changed between boots. Specifically, it detects when Tika is toggled on or off:

1. At boot, `_check_fts_config_changed()` compares current settings with a marker file
2. If Tika was enabled or disabled since last boot, existing FTS indexes are purged
3. A `needs_fts_reindex` flag is set
4. The app lifespan detects this flag and submits a background FTS reindex task

This ensures that search indexes are rebuilt when the indexing pipeline changes.

## Volume Layout

```
/confs/
  dovecot/                  # Generated by MFB, mounted into Dovecot as /etc/dovecot/conf.d
    mfb-ssl.conf
    mfb-service.conf
    mfb-stats.conf
    mfb-mail.conf
    mfb-acl.conf
    mfb-fts.conf
    mfb-auth.conf
    mfb-lua-userdb.lua
  webmail/                  # Generated by MFB, mounted into Roundcube as /var/roundcube/config
    custom.php

/data/
  mailboxes/                # Primary mail store
    {account-uuid}/
      INBOX/
      Sent/
    .dovecot-home/
      {username}/
  mailboxes2/               # Secondary mail store (optional)
  logs/
    sync/                   # mbsync job logs
```
