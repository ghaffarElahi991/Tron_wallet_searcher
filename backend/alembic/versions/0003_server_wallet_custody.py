"""Add encrypted server-generated wallet key material.

Revision ID: 0003_server_wallet_custody
Revises: 0002_gpu_scheduler
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_server_wallet_custody"
down_revision: str | None = "0002_gpu_scheduler"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generation_jobs", sa.Column("encrypted_base_private_key", sa.Text()))
    op.add_column("generation_results", sa.Column("encrypted_private_key", sa.Text()))
    op.execute(
        sa.text(
            "UPDATE generation_jobs "
            "SET status = 'failed', "
            "failure_code = 'legacy_key_mode', "
            "failure_message = 'Create a new request after the server-custody upgrade.', "
            "completed_at = CURRENT_TIMESTAMP "
            "WHERE encrypted_base_private_key IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("generation_results", "encrypted_private_key")
    op.drop_column("generation_jobs", "encrypted_base_private_key")
