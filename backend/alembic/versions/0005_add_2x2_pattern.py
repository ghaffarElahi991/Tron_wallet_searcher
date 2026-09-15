"""Add the 2x2 vanity pattern.

Revision ID: 0005_add_2x2_pattern
Revises: 0004_exclude_t_from_prefix_count
Create Date: 2026-09-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_add_2x2_pattern"
down_revision: str | None = "0004_exclude_t_from_prefix_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_job_supported_pattern", "generation_jobs", type_="check")
    op.create_check_constraint(
        "ck_job_supported_pattern",
        "generation_jobs",
        "pattern_type IN ('3x4', '2x5', '4x3', '2x2')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_supported_pattern", "generation_jobs", type_="check")
    op.create_check_constraint(
        "ck_job_supported_pattern",
        "generation_jobs",
        "pattern_type IN ('3x4', '2x5', '4x3')",
    )
