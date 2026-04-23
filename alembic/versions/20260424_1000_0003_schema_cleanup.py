"""schema cleanup — server_defaults restored + NULLS NOT DISTINCT

Revision ID: 0003_schema_cleanup
Revises: 0002_reliability_fields
Create Date: 2026-04-24 10:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_schema_cleanup"
down_revision: Union[str, None] = "0002_reliability_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Migrace 0002 nejdřív nastavila server_default, pak ho ALTER COLUMN
    # uklidila na None. Pozdější INSERT z raw SQL by padl na NOT NULL. Teď
    # obnovíme defaults — ORM vlastní Python default zachováme.
    # PR-07 FIND-011, FIND-019.
    op.alter_column(
        "spot_runs",
        "checkpoint_results_json",
        server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "spot_runs",
        "return_home_status",
        server_default="not_requested",
    )
    op.alter_column(
        "maps",
        "metadata_version",
        server_default="2",
    )
    op.alter_column(
        "maps",
        "archive_is_valid",
        server_default=sa.text("true"),
    )
    op.alter_column(
        "maps",
        "default_capture_sources",
        server_default=sa.text("'[]'::jsonb"),
    )

    # NULLS NOT DISTINCT pro plate_detections unique (PG 15+).
    # Fallback: CREATE UNIQUE INDEX s COALESCE pokud NULLS NOT DISTINCT
    # není dostupné. Zjistíme dynamicky přes SELECT current_setting.
    # PR-07 FIND-017.
    conn = op.get_bind()
    pg_version_raw = conn.execute(
        sa.text("SHOW server_version_num")
    ).scalar_one()
    try:
        pg_version_num = int(pg_version_raw)
    except (TypeError, ValueError):
        pg_version_num = 0

    if pg_version_num >= 150000:
        # PG 15+: DROP + ADD s NULLS NOT DISTINCT.
        op.execute(
            'ALTER TABLE plate_detections '
            'DROP CONSTRAINT IF EXISTS ux_det_photo_engine_plate'
        )
        op.execute(
            'ALTER TABLE plate_detections '
            'ADD CONSTRAINT ux_det_photo_engine_plate '
            'UNIQUE NULLS NOT DISTINCT (photo_id, engine_name, plate_text)'
        )
    else:
        # Fallback: expression unique index přes COALESCE (starší PG).
        op.execute(
            'ALTER TABLE plate_detections '
            'DROP CONSTRAINT IF EXISTS ux_det_photo_engine_plate'
        )
        op.execute(
            "CREATE UNIQUE INDEX ux_det_photo_engine_plate "
            "ON plate_detections (photo_id, engine_name, COALESCE(plate_text, ''))"
        )


def downgrade() -> None:
    # Reverse: zrušit server_defaults a obnovit původní UNIQUE constraint.
    op.alter_column(
        "spot_runs", "checkpoint_results_json", server_default=None
    )
    op.alter_column("spot_runs", "return_home_status", server_default=None)
    op.alter_column("maps", "metadata_version", server_default=None)
    op.alter_column("maps", "archive_is_valid", server_default=None)
    op.alter_column("maps", "default_capture_sources", server_default=None)

    # Obnov původní UNIQUE constraint.
    conn = op.get_bind()
    pg_version_raw = conn.execute(
        sa.text("SHOW server_version_num")
    ).scalar_one()
    try:
        pg_version_num = int(pg_version_raw)
    except (TypeError, ValueError):
        pg_version_num = 0

    if pg_version_num >= 150000:
        op.execute(
            'ALTER TABLE plate_detections '
            'DROP CONSTRAINT IF EXISTS ux_det_photo_engine_plate'
        )
        op.execute(
            'ALTER TABLE plate_detections '
            'ADD CONSTRAINT ux_det_photo_engine_plate '
            'UNIQUE (photo_id, engine_name, plate_text)'
        )
    else:
        op.execute(
            'DROP INDEX IF EXISTS ux_det_photo_engine_plate'
        )
        op.execute(
            'ALTER TABLE plate_detections '
            'ADD CONSTRAINT ux_det_photo_engine_plate '
            'UNIQUE (photo_id, engine_name, plate_text)'
        )
