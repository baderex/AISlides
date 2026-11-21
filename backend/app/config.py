from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")
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
    allow_signup: bool = True
    ai_provider: str = "openai"
    ai_model: str = ""
    planning_model: str = ""
    research_model: str = ""
    slide_model: str = ""
    vision_model: str = ""
    image_provider: Literal["none", "openai"] = "none"
    image_model: str = "gpt-image-1.5"
    image_openai_api_key: str = ""
    image_limit_per_job: int = 3
    image_cost_usd: float = 0.0
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    compatible_api_key: str = ""
    compatible_base_url: str = ""
    compatible_structured: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434"
    research_api_key: str = ""
    ai_timeout_seconds: int = 90
    ai_job_token_budget: int = 90000
    ai_job_cost_budget: float = 10
    ai_input_usd_per_million: float = 0
    ai_output_usd_per_million: float = 0
    asset_limit_bytes: int = 12 * 1024 * 1024
    artifact_retention_days: int = 30
    powerpoint_renderer: str = ""
    powerpoint_review_timeout: int = Field(default=45, ge=5, le=90)
    lease_seconds: int = 300

    @property
    def implicit_session(self) -> bool:
        return self.app_env != "production" and self.local_session

    @property
    def queue_uses_redis(self) -> bool:
        return self.job_backend == "redis" or self.redis_enabled


@lru_cache
def settings() -> Settings:
    return Settings()
