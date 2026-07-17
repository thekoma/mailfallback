from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "MAILFALLBACK_"}

    database_url: str = "postgresql://mailfallback:mailfallback@db:5432/mailfallback"
    secret_key: str = "change-me-in-production"
    session_secret: str = "change-me-session-secret"
    session_https_only: bool = False

    debug: bool = False
    mbsync_binary: str = "mbsync"
    bootstrap_store_path: str = "/data/mailboxes"

    oidc_enabled: bool = False
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_discovery_url: str = ""
    oidc_admin_group: str = "mailfallback-admin"
    oidc_user_group: str = "mailfallback-user"
    oidc_userinfo_url: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""

    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "consumers"

    sync_max_workers: int = 4
    sync_log_dir: str = "/data/logs/sync"
    # Watchdog / runtime guards (env: MAILFALLBACK_SYNC_*). See
    # docs/superpowers/specs/2026-06-22-sync-watchdog-and-reauth-design.md.
    sync_stall_grace_s: int = 900  # min age before a running job is reapable
    sync_stall_threshold_s: int = 600  # no sampler tick for this long ⇒ stalled
    sync_watchdog_interval_s: int = 60  # reaper cadence
    sync_job_max_runtime_s: int = 21600  # hard wall-clock cap per mbsync invocation (6h)

    recovery_ephemeral_ttl_minutes: int = 30
    recovery_max_parallel_mounts: int = 5
    recovery_backend: str = "restore"  # "restore" (default) | "fuse" (future)

    metrics_api_key: str = ""

    dovecot_api_url: str = "http://dovecot:8080"
    dovecot_api_key: str = ""
    dovecot_imap_host: str = "dovecot"
    dovecot_imap_port: int = 31143
    dovecot_nfs: bool = False  # emit NFS-safe mail settings (mmap_disable, mail_fsync)
    dovecot_tls: bool = False  # emit ssl=yes + cert paths (mounted kubernetes.io/tls secret)

    webmail_enabled: bool = False
    webmail_url: str = ""
    webmail_oauth_client_id: str = ""
    webmail_oauth_client_secret: str = ""
    webmail_oauth_auth_uri: str = ""
    webmail_oauth_token_uri: str = ""
    webmail_oauth_identity_uri: str = ""

    tika_enabled: bool = False
    tika_url: str = "http://tika:9998"

    confs_path: str = "/confs"

    deep_search_timeout_seconds: int = 10
    use_index_search: bool = True

    staging_ttl_minutes: int = 10080  # 7 days; staging areas expire and get purged
    staging_max_bytes: int = 0  # 0 = unlimited; per-user staging quota

    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "mailfallback"
    db_user: str = "mailfallback"
    db_password: str = "mailfallback"


settings = Settings()
