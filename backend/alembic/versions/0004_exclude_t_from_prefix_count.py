"""Exclude the fixed TRON T from vanity prefix lengths.

Revision ID: 0004_exclude_t_from_prefix_count
Revises: 0003_server_wallet_custody
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_exclude_t_from_prefix_count"
down_revision: str | None = "0003_server_wallet_custody"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "generation_jobs",
        "prefix",
        existing_type=sa.String(length=4),
        type_=sa.String(length=5),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "generation_jobs",
        "prefix",
        existing_type=sa.String(length=5),
        type_=sa.String(length=4),
        existing_nullable=False,
        postgresql_using="left(prefix, 4)",
    )
