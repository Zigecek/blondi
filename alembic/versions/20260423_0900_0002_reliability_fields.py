"""reliability fields for validated maps and truthful run results

Revision ID: 0002_reliability_fields
Revises: 0001_initial
Create Date: 2026-04-23 09:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_reliability_fields"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "maps",
        sa.Column("metadata_version", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "maps",
        sa.Column(
            "archive_is_valid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "maps",
        sa.Column("archive_validation_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "spot_runs",
        sa.Column(
            "checkpoint_results_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "spot_runs",
        sa.Column(
            "return_home_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_requested",
        ),
    )
    op.add_column(
        "spot_runs",
        sa.Column("return_home_reason", sa.Text(), nullable=True),
    )

    op.alter_column("maps", "metadata_version", server_default=None)
    op.alter_column("maps", "archive_is_valid", server_default=None)
    op.alter_column("spot_runs", "checkpoint_results_json", server_default=None)
    op.alter_column("spot_runs", "return_home_status", server_default=None)


def downgrade() -> None:
    op.drop_column("spot_runs", "return_home_reason")
    op.drop_column("spot_runs", "return_home_status")
    op.drop_column("spot_runs", "checkpoint_results_json")
    op.drop_column("maps", "archive_validation_error")
    op.drop_column("maps", "archive_is_valid")
    op.drop_column("maps", "metadata_version")
