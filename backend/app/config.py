from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: float = Field(default=8, gt=0, le=60)
    ekt_username: str = ""
    ekt_password: SecretStr = SecretStr("")
    ekt_timeout_seconds: float = Field(default=5, gt=0, le=60)
    ekt_live_enabled: bool = False
    catalog_db: Path = Path("data/catalog.sqlite3")
    catalog_stale_seconds: int = Field(default=300, ge=0)
    purchase_terms_path: Path = Path("data/purchase_terms.json")
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    session_ttl_seconds: int = Field(default=3600, ge=60)
    max_sessions: int = Field(default=1000, ge=1)
    chat_requests_per_minute: int = Field(default=30, ge=1, le=300)
    demo_cart_enabled: bool = False
    cart_proposal_ttl_seconds: int = Field(default=300, ge=1, le=900)
    cart_cookie_secure: bool = False
    cart_requests_per_minute: int = Field(default=60, ge=1, le=300)
    public_demo: bool = False
    public_demo_username: SecretStr = SecretStr("")
    public_demo_password: SecretStr = SecretStr("")
    public_demo_requests_per_minute: int = Field(default=120, ge=1)
    public_demo_max_requests: int = Field(default=1000, ge=1)
    public_demo_cookie_secure: bool = True
    public_demo_cookie_ttl_seconds: int = Field(default=28800, ge=1)
    public_demo_max_body_bytes: int = Field(default=65536, ge=1)

    @property
    def ai_configured(self) -> bool:
        return bool(self.openai_api_key.get_secret_value())
