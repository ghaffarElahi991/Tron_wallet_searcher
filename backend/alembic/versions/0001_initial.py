"""Create authentication, generation job, and result tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

pattern_type = sa.Enum("3x4", "2x5", "4x3", name="patterntype", native_enum=False)
job_status = sa.Enum(
    "queued",
    "searching",
    "verifying",
    "ready",
    "ownership_verified",
    "failed",
    "canceled",
    "timed_out",
    name="jobstatus",
    native_enum=False,
)
verification_status = sa.Enum("verified", "rejected", name="verificationstatus", native_enum=False)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("pattern_type", pattern_type, nullable=False),
        sa.Column("prefix", sa.String(length=4), nullable=False),
        sa.Column("suffix", sa.String(length=5), nullable=False),
        sa.Column("client_public_key", sa.String(length=66), nullable=False),
        sa.Column("match_spec", sa.JSON(), nullable=False),
        sa.Column("status", job_status, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False),
        sa.Column("search_rate", sa.BigInteger(), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_job_attempts_nonnegative"),
        sa.CheckConstraint("search_rate >= 0", name="ck_job_search_rate_nonnegative"),
        sa.CheckConstraint(
            "pattern_type IN ('3x4', '2x5', '4x3')", name="ck_job_supported_pattern"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_job_user_idempotency"),
    )
    op.create_index(
        "ix_generation_jobs_queue",
        "generation_jobs",
        ["status", "priority", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_generation_jobs_user_created",
        "generation_jobs",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "generation_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("address", sa.String(length=34), nullable=False),
        sa.Column("encrypted_offset", sa.Text(), nullable=False),
        sa.Column("verification_status", verification_status, nullable=False),
        sa.Column("verifier_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        op.f("ix_generation_results_address"),
        "generation_results",
        ["address"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_generation_results_address"), table_name="generation_results")
    op.drop_table("generation_results")
    op.drop_index("ix_generation_jobs_user_created", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_queue", table_name="generation_jobs")
    op.drop_table("generation_jobs")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
