import uuid as uuid_pkg
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class PatternType(StrEnum):
    THREE_BY_FOUR = "3x4"
    TWO_BY_FIVE = "2x5"
    FOUR_BY_THREE = "4x3"
    TWO_BY_TWO = "2x2"


class JobStatus(StrEnum):
    QUEUED = "queued"
    SEARCHING = "searching"
    VERIFYING = "verifying"
    READY = "ready"
    OWNERSHIP_VERIFIED = "ownership_verified"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    REJECTED = "rejected"


class GpuStatus(StrEnum):
    STARTING = "starting"
    SELF_TESTING = "self_testing"
    READY = "ready"
    SEARCHING = "searching"
    UNHEALTHY = "unhealthy"
    OFFLINE = "offline"


class ShardStatus(StrEnum):
    ASSIGNED = "assigned"
    RUNNING = "running"
    EXHAUSTED = "exhausted"
    FOUND = "found"
    CANCELED = "canceled"
    FAILED = "failed"


class FundingStatus(StrEnum):
    REQUESTED = "requested"
    PREPARING = "preparing"
    SIGNED = "signed"
    BROADCAST = "broadcast"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid, primary_key=True, default=uuid_pkg.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    jobs: Mapped[list["GenerationJob"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    auth_sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid, primary_key=True, default=uuid_pkg.uuid4)
    user_id: Mapped[uuid_pkg.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="auth_sessions")


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_job_user_idempotency"),
        CheckConstraint(
            "pattern_type IN ('3x4', '2x5', '4x3', '2x2')",
            name="ck_job_supported_pattern",
        ),
        CheckConstraint("attempts >= 0", name="ck_job_attempts_nonnegative"),
        CheckConstraint("search_rate >= 0", name="ck_job_search_rate_nonnegative"),
        Index("ix_generation_jobs_queue", "status", "priority", "created_at"),
        Index("ix_generation_jobs_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid, primary_key=True, default=uuid_pkg.uuid4)
    user_id: Mapped[uuid_pkg.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    pattern_type: Mapped[PatternType] = mapped_column(
        Enum(
            PatternType,
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        nullable=False,
    )
    prefix: Mapped[str] = mapped_column(String(5), nullable=False)
    suffix: Mapped[str] = mapped_column(String(5), nullable=False)
    client_public_key: Mapped[str] = mapped_column(String(66), nullable=False)
    encrypted_base_private_key: Mapped[str | None] = mapped_column(Text)
    match_spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        default=JobStatus.QUEUED,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    search_rate: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    search_origin: Mapped[str | None] = mapped_column(String(64))
    next_offset: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="jobs")
    result: Mapped["GenerationResult | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False, lazy="raise"
    )
    shards: Mapped[list["GenerationShard"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    funding: Mapped["FundingTransaction | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False, lazy="raise"
    )


class GenerationResult(Base):
    __tablename__ = "generation_results"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid, primary_key=True, default=uuid_pkg.uuid4)
    job_id: Mapped[uuid_pkg.UUID] = mapped_column(
        Uuid, ForeignKey("generation_jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    address: Mapped[str] = mapped_column(String(34), unique=True, index=True, nullable=False)
    encrypted_offset: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_private_key: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(
            VerificationStatus,
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        nullable=False,
    )
    verifier_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[GenerationJob] = relationship(back_populates="result")


class FundingTransaction(Base):
    __tablename__ = "funding_transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_funding_user_idempotency"),
        CheckConstraint(
            "amount_micro_usdt BETWEEN 1000000 AND 1500000000",
            name="ck_funding_amount_range",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_funding_attempt_count_nonnegative"),
        Index("ix_funding_status_updated", "status", "updated_at"),
        Index("ix_funding_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid, primary_key=True, default=uuid_pkg.uuid4)
    user_id: Mapped[uuid_pkg.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid_pkg.UUID] = mapped_column(
        Uuid,
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_address: Mapped[str | None] = mapped_column(String(34))
    destination_address: Mapped[str] = mapped_column(String(34), nullable=False)
    amount_micro_usdt: Mapped[int] = mapped_column(BigInteger, nullable=False)
    network: Mapped[str] = mapped_column(String(16), nullable=False)
    contract_address: Mapped[str] = mapped_column(String(34), nullable=False)
    status: Mapped[FundingStatus] = mapped_column(
        Enum(
            FundingStatus,
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        default=FundingStatus.REQUESTED,
        nullable=False,
    )
    txid: Mapped[str | None] = mapped_column(String(64), unique=True)
    encrypted_signed_transaction: Mapped[str | None] = mapped_column(Text)
    transaction_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    broadcast_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[GenerationJob] = relationship(back_populates="funding")


class GpuDevice(Base):
    __tablename__ = "gpu_devices"

    uuid: Mapped[str] = mapped_column(String(96), primary_key=True)
    device_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_total_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[GpuStatus] = mapped_column(
        Enum(
            GpuStatus,
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        default=GpuStatus.OFFLINE,
        nullable=False,
    )
    kernel_version: Mapped[str] = mapped_column(String(32), nullable=False)
    benchmark_rate: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    utilization_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    temperature_c: Mapped[int | None] = mapped_column(Integer)
    worker_pid: Mapped[int | None] = mapped_column(Integer)
    current_job_id: Mapped[uuid_pkg.UUID | None] = mapped_column(
        Uuid, ForeignKey("generation_jobs.id", ondelete="SET NULL")
    )
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    shards: Mapped[list["GenerationShard"]] = relationship(back_populates="gpu")


class GenerationShard(Base):
    __tablename__ = "generation_shards"
    __table_args__ = (
        UniqueConstraint("job_id", "range_start", name="uq_shard_job_range_start"),
        CheckConstraint("range_count > 0", name="ck_shard_range_count_positive"),
        CheckConstraint("processed >= 0", name="ck_shard_processed_nonnegative"),
        Index("ix_generation_shards_job_status", "job_id", "status"),
    )

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid, primary_key=True, default=uuid_pkg.uuid4)
    job_id: Mapped[uuid_pkg.UUID] = mapped_column(
        Uuid, ForeignKey("generation_jobs.id", ondelete="CASCADE"), nullable=False
    )
    gpu_uuid: Mapped[str] = mapped_column(
        String(96), ForeignKey("gpu_devices.uuid", ondelete="CASCADE"), nullable=False
    )
    lease_token: Mapped[uuid_pkg.UUID] = mapped_column(Uuid, default=uuid_pkg.uuid4, nullable=False)
    range_start: Mapped[str] = mapped_column(String(64), nullable=False)
    range_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    processed: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[ShardStatus] = mapped_column(
        Enum(
            ShardStatus,
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        default=ShardStatus.ASSIGNED,
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_message: Mapped[str | None] = mapped_column(Text)

    job: Mapped[GenerationJob] = relationship(back_populates="shards")
    gpu: Mapped[GpuDevice] = relationship(back_populates="shards")
