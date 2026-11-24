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
    admin_username: str = "admin"
    admin_password: str = Field(
        default="", description="First run only: creates the admin (or do it in the browser)."
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

    # Updates: the app checks GitHub releases; "Install and restart" needs the updater
    # sidecar (see docker-compose.yml).
    update_check: bool = True
    update_repo: str = "Peppe37/oink"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'oink.db'}"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_model)

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def updater_dir(self) -> Path:
        return self.data_dir / "updater"


@lru_cache
def get_settings() -> Settings:
    return Settings()
