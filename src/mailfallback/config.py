from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "MAILFALLBACK_"}

    database_url: str = "sqlite:////data/config/mailfallback.db"
    secret_key: str = "change-me-in-production"
    session_secret: str = "change-me-session-secret"

    mbsync_binary: str = "mbsync"
    maildir_base_path: str = "/data/mailboxes"
    config_path: str = "/data/config"

    oidc_enabled: bool = False
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_discovery_url: str = ""
    oidc_admin_group: str = "mailfallback-admin"
    oidc_user_group: str = "mailfallback-user"

    google_client_id: str = ""
    google_client_secret: str = ""

    dovecot_config_path: str = "/data/config/dovecot"


settings = Settings()
