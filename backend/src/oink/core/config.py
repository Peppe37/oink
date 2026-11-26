"""Settings, all from the environment (prefix OINK_), so the app is configured like any
self-hosted service. AI keys use each provider's own variable (OPENAI_API_KEY,
ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, ...): LiteLLM reads them."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OINK_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("./data"), description="SQLite database and uploads.")
    public_url: str = Field(
        default="http://localhost:8080",
        description="Where the app is reached: bank authorisations come back here.",
    )
    admin_username: str = Field(
        default="admin",
        description="The service admin: manages people and settings, has no finances.",
    )
    admin_password: str = Field(
        default="",
        description="Password of the service admin when it is created. Empty = «oink», "
        "which must be changed at the first sign-in.",
    )
    signup: bool = Field(
        default=True,
        description="People can create their own account (the admin can turn it off in the app).",
    )
    secret_key: str = Field(
        default="", description="Encrypts API keys of linked accounts. Empty = generated."
    )
    currency: str = Field(default="EUR", description="Default base currency of new users.")
    timezone: str = "Europe/Rome"

    # AI: any LiteLLM model id, e.g. "openai/gpt-5.4-mini", "anthropic/claude-sonnet-5-5",
    # "deepseek/deepseek-chat", "ollama/llama3.1". Empty = no AI (everything else works).
    ai_model: str = ""
    ai_base_url: str = Field(default="", description="Custom endpoint (Ollama, a proxy...).")
    ai_send_descriptions: bool = Field(
        default=True,
        description="Send transaction descriptions to the AI provider (needed for categories "
        "and PDF reading). False = only aggregated numbers leave the server.",
    )

    # Enable Banking (PSD2, free for your own accounts): https://enablebanking.com
    # Can also be set by the admin in the app (Settings → Bank access).
    enable_banking_app_id: str = ""
    enable_banking_key_file: Path = Field(default=Path("./data/enablebanking.pem"))

    sync_hours: int = Field(default=6, ge=1, le=48, description="Linked accounts sync interval.")
    statement_day: int = Field(
        default=1, ge=1, le=28, description="Day of the month manual accounts ask a statement."
    )
    session_days: int = 30
    engine_timeout: int = Field(
        default=7200, ge=60, description="Seconds a document may take to be read."
    )

    # Single sign-on (OpenID Connect: Keycloak, Authentik, Google…). Empty issuer = off.
    oidc_issuer: str = Field(default="", description="e.g. https://sso.example.com/realms/home")
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_name: str = Field(default="SSO", description="Label of the sign-in button.")
    oidc_scopes: str = "openid profile email"
    oidc_admin_group: str = Field(default="", description="Group or role that makes admins.")
    oidc_auto_create: bool = Field(default=True, description="Create accounts on first login.")
    password_login: bool = Field(default=True, description="False = only SSO sign-in.")

    # Updates: the app checks GitHub releases; "Install and restart" needs the updater
    # sidecar (see docker-compose.yml).
    update_check: bool = True
    update_repo: str = "Peppe37/oink"

    # The documents engine and its stores, all optional (without them: SQLite full-text
    # search and the built-in readers). They can be the bundled ones or shared instances
    # (own collection / graph / key prefix), e.g. those of another app of the homelab.
    engine_url: str = Field(default="", description="Oink engine (Docling + BGE-M3).")
    engine_token: str = ""
    qdrant_url: str = Field(default="", description="Vector store for the documents.")
    qdrant_key: str = Field(default="", description="API key or a token for one collection.")
    qdrant_collection: str = "oink_documents"
    redis_url: str = Field(default="", description="Cache, e.g. redis://user:pass@host:6379/0")
    cache_prefix: str = "oink:"
    falkordb_url: str = Field(default="", description="Knowledge graph (FalkorDB).")
    graph_name: str = "oink"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'oink.db'}"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_model)

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def updater_dir(self) -> Path:
        return self.data_dir / "updater"


@lru_cache
def get_settings() -> Settings:
    return Settings()
