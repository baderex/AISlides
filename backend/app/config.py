from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"), extra="ignore")
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: Path = Path("./data/uploads")
    job_backend: Literal["local", "redis"] = "local"
    redis_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"
    s3_bucket: str = ""
    s3_endpoint: str | None = None
    public_origin: str | None = None
    local_session: bool = True
    allow_signup: bool = Field(default_factory=lambda data: data["app_env"] != "production")
    llm_api_url: str = ""
    llm_model_id: str = ""
    llm_api_key: str = ""
    llm_vision_enabled: bool = False
    image_provider: Literal["none", "openai", "openrouter"] = "none"
    image_model: str = ""
    image_gen_url: str = ""
    image_gen_api_key: str = Field(default="", repr=False)
    image_openai_api_key: str = Field(default="", repr=False)  # Backward compatibility for the OpenAI provider.
    image_limit_per_job: int = 3
    image_cost_usd: float = Field(default=0.10, ge=0)
    compatible_structured: bool = False
    research_cost_usd: float = Field(default=0.01, ge=0)
    research_api_key: str = ""
    research_api_url: str = Field(
        default="https://api.search.brave.com/res/v1/web/search",
        validation_alias=AliasChoices("research_api_url", "brave_web_search"),
    )
    research_context_api_url: str = ""
    research_image_api_url: str = "https://api.search.brave.com/res/v1/images/search"
    ai_timeout_seconds: int = 90
    ai_job_token_budget: int = 90000
    ai_job_cost_budget: float = 10
    ai_input_usd_per_million: float = Field(default=2, ge=0)
    ai_output_usd_per_million: float = Field(default=8, ge=0)
    asset_limit_bytes: int = 12 * 1024 * 1024
    artifact_retention_days: int = 30
    powerpoint_renderer: str = ""
    html_layout_browser: str = ""
    powerpoint_review_timeout: int = Field(default=45, ge=5, le=90)
    lease_seconds: int = Field(default=300, ge=2)
    account_ai_requests_daily: int = Field(default=100, ge=1)
    account_ai_requests_per_minute: int = Field(default=12, ge=1)
    account_ai_concurrency: int = Field(default=4, ge=1)
    account_daily_budget_usd: float = Field(default=25, gt=0)
    provider_daily_budget_usd: float = Field(default=100, gt=0)
    account_asset_limit_bytes: int = Field(default=512 * 1024 * 1024, ge=1)
    orphan_grace_hours: int = Field(default=24, ge=1)
    event_replay_limit: int = Field(default=100, ge=1, le=1000)
    event_retention_days: int = Field(default=30, ge=1)
    export_queue_slide_threshold: int = Field(default=8, ge=1)
    artifact_queue_capacity: int = Field(default=32, ge=1)
    artifact_timeout_seconds: int = Field(default=180, ge=15)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        def process_settings():
            values = env_settings()
            return {name: value for name, value in values.items() if name not in {"llm_api_url", "llm_model_id"}}

        return init_settings, process_settings, dotenv_settings, file_secret_settings

    @property
    def implicit_session(self) -> bool:
        return self.app_env != "production" and self.local_session

    @property
    def queue_uses_redis(self) -> bool:
        return self.job_backend == "redis" or self.redis_enabled


@lru_cache
def settings() -> Settings:
    return Settings()
