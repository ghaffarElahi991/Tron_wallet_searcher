from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

try:
    from app import local_constants
except ModuleNotFoundError as exc:
    if exc.name != "app.local_constants":
        raise
    local_constants = None

MAINNET_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def _local_constant_values() -> dict[str, object]:
    values: dict[str, object] = {
        "telegram_funding_enabled": True,
        "funding_node_url": "https://api.trongrid.io",
    }
    if local_constants is None:
        return values

    candidates = {
        "telegram_bot_token": getattr(local_constants, "TELEGRAM_BOT_TOKEN", ""),
        "telegram_allowed_user_id": getattr(
            local_constants, "TELEGRAM_ALLOWED_USER_ID", None
        ),
        "funding_node_url": getattr(
            local_constants, "FUNDING_NODE_URL", "https://api.trongrid.io"
        ),
        "funding_node_api_key": getattr(local_constants, "FUNDING_NODE_API_KEY", ""),
        "funding_master_private_key": getattr(
            local_constants, "FUNDING_MASTER_PRIVATE_KEY", ""
        ),
        "funding_master_address": getattr(local_constants, "FUNDING_MASTER_ADDRESS", ""),
        "telegram_funding_enabled": getattr(
            local_constants, "TELEGRAM_FUNDING_ENABLED", True
        ),
    }
    for field_name, value in candidates.items():
        if value is not None and (not isinstance(value, str) or value.strip()):
            values[field_name] = value
    return values


class LocalConstantsSettingsSource(PydanticBaseSettingsSource):
    def get_field_value(self, field, field_name: str) -> tuple[object, str, bool]:
        value = _local_constant_values().get(field_name)
        return value, field_name, False

    def __call__(self) -> dict[str, object]:
        return _local_constant_values()


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
    telegram_funding_enabled: bool = True
    telegram_api_url: str = "http://127.0.0.1:8000/api/v1"
    telegram_poll_interval_seconds: float = Field(default=3.0, ge=1.0, le=30.0)
    funding_mode: Literal["disabled", "simulator", "live"] = "disabled"
    funding_network: Literal["mainnet", "nile", "shasta"] = "nile"
    funding_allow_mainnet: bool = False
    funding_contract_address: str = MAINNET_USDT_CONTRACT
    funding_node_url: str = ""
    funding_node_api_key: SecretStr = SecretStr("")
    funding_master_private_key: SecretStr = SecretStr("")
    funding_master_private_key_file: str = ""
    funding_master_address: str = ""
    funding_fee_limit_sun: int = Field(default=100_000_000, ge=1_000_000, le=1_000_000_000)
    funding_min_available_energy: int = Field(default=65_000, ge=0)
    funding_min_available_bandwidth: int = Field(default=350, ge=0)
    funding_daily_limit_usdt: Decimal = Field(default=Decimal("1500"), ge=1, le=1_000_000)
    funding_poll_interval_seconds: float = Field(default=3.0, ge=0.25, le=60.0)
    funding_confirmation_timeout_seconds: int = Field(default=600, ge=30, le=86_400)
    funding_lock_file: str = "/tmp/tronforge-funding.lock"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRONFORGE_",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            LocalConstantsSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
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

    @model_validator(mode="after")
    def validate_funding_configuration(self) -> "Settings":
        if self.funding_mode != "live":
            return self
        if self.funding_network == "mainnet" and not self.funding_allow_mainnet:
            raise ValueError(
                "Live mainnet funding requires TRONFORGE_FUNDING_ALLOW_MAINNET=true."
            )
        if (
            self.funding_network == "mainnet"
            and self.funding_contract_address != MAINNET_USDT_CONTRACT
        ):
            raise ValueError("Mainnet funding is restricted to the configured USDT contract.")
        if (
            self.funding_network != "mainnet"
            and self.funding_contract_address == MAINNET_USDT_CONTRACT
        ):
            raise ValueError("Testnet funding requires a testnet TRC-20 contract address.")
        if not self.funding_node_url.strip():
            raise ValueError(
                "Live funding requires FUNDING_NODE_URL in backend/app/local_constants.py."
            )
        if (
            "trongrid" in self.funding_node_url.lower()
            and not self.funding_node_api_key.get_secret_value().strip()
        ):
            raise ValueError(
                "A TronGrid funding node requires FUNDING_NODE_API_KEY in "
                "backend/app/local_constants.py."
            )
        inline_key = self.funding_master_private_key.get_secret_value().strip()
        if not inline_key and not self.funding_master_private_key_file.strip():
            raise ValueError("Live funding requires a master private key or private-key file.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
