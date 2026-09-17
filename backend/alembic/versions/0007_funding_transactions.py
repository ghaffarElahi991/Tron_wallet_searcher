"""Add idempotent USDT funding transaction tracking.

Revision ID: 0007_funding_transactions
Revises: 0006_browser_sessions
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_funding_transactions"
down_revision: str | None = "0006_browser_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

funding_status = sa.Enum(
    "requested",
    "preparing",
    "signed",
    "broadcast",
    "confirmed",
    "failed",
    "unknown",
    name="fundingstatus",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "funding_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("source_address", sa.String(length=34)),
        sa.Column("destination_address", sa.String(length=34), nullable=False),
        sa.Column("amount_micro_usdt", sa.BigInteger(), nullable=False),
        sa.Column("network", sa.String(length=16), nullable=False),
        sa.Column("contract_address", sa.String(length=34), nullable=False),
        sa.Column("status", funding_status, nullable=False),
        sa.Column("txid", sa.String(length=64)),
        sa.Column("encrypted_signed_transaction", sa.Text()),
        sa.Column("transaction_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=64)),
        sa.Column("failure_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("broadcast_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "amount_micro_usdt BETWEEN 1000000 AND 1500000000",
            name="ck_funding_amount_range",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_funding_attempt_count_nonnegative"),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
        sa.UniqueConstraint("txid"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_funding_user_idempotency"),
    )
    op.create_index(
        "ix_funding_status_updated",
        "funding_transactions",
        ["status", "updated_at"],
    )
    op.create_index(
        "ix_funding_user_created",
        "funding_transactions",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_funding_user_created", table_name="funding_transactions")
    op.drop_index("ix_funding_status_updated", table_name="funding_transactions")
    op.drop_table("funding_transactions")
