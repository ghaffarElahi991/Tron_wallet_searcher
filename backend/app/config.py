from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TronForge API"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://tronforge:tronforge@localhost:5432/tronforge"
    jwt_secret: SecretStr = SecretStr("development-jwt-secret-change-me")
    worker_api_key: SecretStr = SecretStr("development-worker-key-change-me")
    result_encryption_key: SecretStr = SecretStr("development-result-key-change-me")
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("ChangeThisAdminPassword123")
    access_token_minutes: int = 30
    browser_session_days: int = Field(default=30, ge=1, le=90)
    cors_origins: str = "http://localhost:3000"
    generator_mode: Literal["simulator", "cuda"] = "simulator"
    generator_native_binary: str = "../native/build-cuda/tronforge-generator"
    generator_cuda_benchmark_candidates: int = Field(default=67_108_864, ge=1, le=67_108_864)
    simulated_gpu_count: int = Field(default=0, ge=0, le=32)
    simulated_gpu_rate: int = Field(default=1_000_000, gt=0)
    generator_poll_interval_ms: int = Field(default=100, ge=10, le=5_000)
    generator_shard_seconds: int = Field(default=10, ge=1, le=3_600)
    generator_kernel_batch_ms: int = Field(default=500, ge=10, le=10_000)
    generator_job_timeout_seconds: int = Field(default=86_400, ge=1)
    generator_lock_file: str = "/tmp/tronforge-generator.lock"
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_allowed_user_id: int = Field(default=0, ge=0)
    telegram_restrict_user_id: bool = True
    telegram_allowed_group_id: int = 0
    telegram_public_access: bool = False
    telegram_api_url: str = "http://127.0.0.1:8000/api/v1"
    telegram_poll_interval_seconds: float = Field(default=3.0, ge=1.0, le=30.0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRONFORGE_",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @field_validator("admin_username")
    @classmethod
    def normalize_admin_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) < 3:
            raise ValueError("admin_username must contain at least 3 characters.")
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()
