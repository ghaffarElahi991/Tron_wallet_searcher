import re
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.models import FundingStatus, GpuStatus, JobStatus, PatternType
from app.services.patterns import BASE58_ALPHABET, PATTERN_LENGTHS, SECP256K1_ORDER

FIRST_CUSTOM_CHARACTER = re.compile(r"^[9A-HJ-NP-Z]$")
HEX_64 = re.compile(r"^[0-9a-fA-F]{64}$")


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class GenerationJobCreate(BaseModel):
    pattern: PatternType
    prefix: str
    suffix: str

    @model_validator(mode="after")
    def validate_pattern(self) -> "GenerationJobCreate":
        custom_prefix_length, suffix_length = PATTERN_LENGTHS[self.pattern]
        if (
            len(self.prefix) != custom_prefix_length + 1
            or len(self.suffix) != suffix_length
        ):
            raise ValueError(
                f"Pattern {self.pattern.value} requires {custom_prefix_length} prefix "
                f"characters after the fixed T and {suffix_length} suffix characters."
            )
        if not self.prefix.startswith("T"):
            raise ValueError("The prefix must begin with the fixed uppercase T.")
        invalid = [
            character for character in self.prefix + self.suffix if character not in BASE58_ALPHABET
        ]
        if invalid:
            raise ValueError("Prefix and suffix must contain only valid TRON Base58 characters.")
        if not FIRST_CUSTOM_CHARACTER.fullmatch(self.prefix[1]):
            raise ValueError("The first character after T must be an uppercase Base58 letter or 9.")
        return self


class GenerationResultRead(BaseModel):
    address: str
    private_key: str
    verified_at: datetime


class GenerationJobRead(BaseModel):
    id: uuid.UUID
    pattern: PatternType
    prefix: str
    suffix: str
    status: JobStatus
    attempts: int
    search_rate: int
    observed_rate: int | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None
    failure_message: str | None
    result: GenerationResultRead | None


class JobListResponse(BaseModel):
    items: list[GenerationJobRead]
    total: int
    limit: int
    offset: int


class CandidateResultCreate(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    address: str = Field(min_length=34, max_length=34)
    offset: str

    @field_validator("offset")
    @classmethod
    def validate_offset(cls, value: str) -> str:
        if not HEX_64.fullmatch(value):
            raise ValueError("offset must be a 32-byte hexadecimal scalar.")
        normalized = value.lower()
        scalar = int(normalized, 16)
        if not 1 <= scalar < SECP256K1_ORDER:
            raise ValueError("offset is outside the valid secp256k1 scalar range.")
        return normalized


class CandidateAccepted(BaseModel):
    accepted: bool
    job_id: uuid.UUID
    status: JobStatus
    address: str


class GpuDeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: str
    device_index: int
    name: str
    memory_total_mb: int
    status: GpuStatus
    kernel_version: str
    benchmark_rate: int
    utilization_percent: int
    temperature_c: int | None
    current_job_id: uuid.UUID | None
    last_heartbeat: datetime | None
    last_error: str | None


class GpuFleetRead(BaseModel):
    mode: str
    total: int
    ready: int
    searching: int
    unhealthy: int
    combined_benchmark_rate: int
    devices: list[GpuDeviceRead]


class FundingCreate(BaseModel):
    amount: Decimal = Field(ge=Decimal("1"), le=Decimal("1500"))

    @field_validator("amount")
    @classmethod
    def validate_amount_precision(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Funding amount must be a finite decimal value.")
        if value.as_tuple().exponent < -6:
            raise ValueError("Funding amount supports at most six decimal places.")
        return value

    @property
    def amount_micro_usdt(self) -> int:
        return int(self.amount * 1_000_000)


class FundingRead(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    source_address: str | None
    destination_address: str
    amount_usdt: str
    network: str
    contract_address: str
    status: FundingStatus
    txid: str | None
    explorer_url: str | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime
    broadcast_at: datetime | None
    confirmed_at: datetime | None


class FundingConfigRead(BaseModel):
    enabled: bool
    mode: str
    network: str
    contract_address: str
    minimum_usdt: str = "1"
    maximum_usdt: str = "1500"
