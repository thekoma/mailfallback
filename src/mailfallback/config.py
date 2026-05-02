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

    google_client_id: str = ""
    google_client_secret: str = ""

    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "consumers"

    sync_max_workers: int = 4
    sync_log_dir: str = "/data/logs/sync"

    dovecot_enabled: bool = False
    dovecot_api_url: str = "http://dovecot:8080"
    dovecot_api_key: str = ""
    webmail_url: str = ""


settings = Settings()
