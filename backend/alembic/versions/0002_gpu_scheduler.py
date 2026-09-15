"""Add local GPU fleet and generation shard tracking.

Revision ID: 0002_gpu_scheduler
Revises: 0001_initial
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_gpu_scheduler"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

gpu_status = sa.Enum(
    "starting",
    "self_testing",
    "ready",
    "searching",
    "unhealthy",
    "offline",
    name="gpustatus",
    native_enum=False,
)
shard_status = sa.Enum(
    "assigned",
    "running",
    "exhausted",
    "found",
    "canceled",
    "failed",
    name="shardstatus",
    native_enum=False,
)


def upgrade() -> None:
    op.add_column("generation_jobs", sa.Column("search_origin", sa.String(64)))
    op.add_column("generation_jobs", sa.Column("next_offset", sa.String(64)))
    op.add_column("generation_jobs", sa.Column("deadline_at", sa.DateTime(timezone=True)))

    op.create_table(
        "gpu_devices",
        sa.Column("uuid", sa.String(96), nullable=False),
        sa.Column("device_index", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("memory_total_mb", sa.Integer(), nullable=False),
        sa.Column("status", gpu_status, nullable=False),
        sa.Column("kernel_version", sa.String(32), nullable=False),
        sa.Column("benchmark_rate", sa.BigInteger(), nullable=False),
        sa.Column("utilization_percent", sa.Integer(), nullable=False),
        sa.Column("temperature_c", sa.Integer()),
        sa.Column("worker_pid", sa.Integer()),
        sa.Column("current_job_id", sa.Uuid()),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["current_job_id"], ["generation_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("uuid"),
    )

    op.create_table(
        "generation_shards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("gpu_uuid", sa.String(96), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("range_start", sa.String(64), nullable=False),
        sa.Column("range_count", sa.BigInteger(), nullable=False),
        sa.Column("processed", sa.BigInteger(), nullable=False),
        sa.Column("status", shard_status, nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_message", sa.Text()),
        sa.CheckConstraint("processed >= 0", name="ck_shard_processed_nonnegative"),
        sa.CheckConstraint("range_count > 0", name="ck_shard_range_count_positive"),
        sa.ForeignKeyConstraint(["gpu_uuid"], ["gpu_devices.uuid"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "range_start", name="uq_shard_job_range_start"),
    )
    op.create_index(
        "ix_generation_shards_job_status",
        "generation_shards",
        ["job_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_shards_job_status", table_name="generation_shards")
    op.drop_table("generation_shards")
    op.drop_table("gpu_devices")
    op.drop_column("generation_jobs", "deadline_at")
    op.drop_column("generation_jobs", "next_offset")
    op.drop_column("generation_jobs", "search_origin")
