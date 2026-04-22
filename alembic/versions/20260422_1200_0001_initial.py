"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-22 12:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    plate_status = postgresql.ENUM(
        "active", "expired", "banned", "unknown", name="plate_status"
    )
    run_status = postgresql.ENUM(
        "running", "completed", "aborted", "failed", "partial", name="run_status"
    )
    ocr_status = postgresql.ENUM(
        "pending", "processing", "done", "failed", name="ocr_status"
    )
    fiducial_side = postgresql.ENUM(
        "left", "right", "both", name="fiducial_side"
    )
    for e in (plate_status, run_status, ocr_status, fiducial_side):
        e.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "license_plates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("plate_text", sa.String(16), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="plate_status", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("plate_text", name="ux_plates_text"),
    )
    op.create_index("ix_plates_status", "license_plates", ["status"])

    op.create_table(
        "maps",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("archive_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("archive_format", sa.String(16), nullable=False, server_default="zip"),
        sa.Column("archive_sha256", sa.String(64), nullable=False),
        sa.Column("archive_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("fiducial_id", sa.Integer(), nullable=True),
        sa.Column("start_waypoint_id", sa.String(64), nullable=True),
        sa.Column("default_capture_sources", postgresql.JSONB(), nullable=False),
        sa.Column("checkpoints_json", postgresql.JSONB(), nullable=True),
        sa.Column("waypoints_count", sa.Integer(), nullable=True),
        sa.Column("checkpoints_count", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_operator", sa.String(64), nullable=True),
        sa.UniqueConstraint("name", name="ux_maps_name"),
    )
    op.execute("ALTER TABLE maps ALTER COLUMN archive_bytes SET STORAGE EXTERNAL")

    op.create_table(
        "spot_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_code", sa.String(32), nullable=False),
        sa.Column(
            "map_id",
            sa.BigInteger(),
            sa.ForeignKey("maps.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("map_name_snapshot", sa.String(128), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="run_status", create_type=False),
            nullable=False,
            server_default="running",
        ),
        sa.Column("checkpoints_reached", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checkpoints_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operator_label", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("start_waypoint_id", sa.String(64), nullable=True),
        sa.Column("abort_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("run_code", name="ux_runs_code"),
    )
    op.create_index("ix_runs_status", "spot_runs", ["status"])
    op.create_index("ix_runs_map_id", "spot_runs", ["map_id"])
    op.create_index(
        "ix_runs_start_time", "spot_runs", [sa.text("start_time DESC")]
    )

    op.create_table(
        "photos",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("spot_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoint_name", sa.String(64), nullable=True),
        sa.Column("camera_source", sa.String(64), nullable=False),
        sa.Column("image_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("image_mime", sa.String(32), nullable=False, server_default="image/jpeg"),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "ocr_status",
            postgresql.ENUM(name="ocr_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("ocr_processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ocr_locked_by", sa.String(64), nullable=True),
        sa.Column("ocr_locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("ALTER TABLE photos ALTER COLUMN image_bytes SET STORAGE EXTERNAL")
    op.create_index("ix_photos_run_id", "photos", ["run_id"])
    op.create_index(
        "ix_photos_captured_at", "photos", [sa.text("captured_at DESC")]
    )
    op.create_index(
        "ix_photos_pending",
        "photos",
        ["captured_at"],
        postgresql_where=sa.text("ocr_status = 'pending'"),
    )

    op.create_table(
        "plate_detections",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "photo_id",
            sa.BigInteger(),
            sa.ForeignKey("photos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plate_text", sa.String(16), nullable=True),
        sa.Column("detection_confidence", sa.Float(), nullable=True),
        sa.Column("text_confidence", sa.Float(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(), nullable=True),
        sa.Column("engine_name", sa.String(32), nullable=False),
        sa.Column("engine_version", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "photo_id", "engine_name", "plate_text", name="ux_det_photo_engine_plate"
        ),
    )
    op.create_index("ix_det_photo_id", "plate_detections", ["photo_id"])
    op.create_index("ix_det_plate_text", "plate_detections", ["plate_text"])

    op.create_table(
        "spot_credentials",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("hostname", sa.String(64), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("keyring_ref", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("label", name="ux_credentials_label"),
    )


def downgrade() -> None:
    op.drop_table("spot_credentials")
    op.drop_index("ix_det_plate_text", table_name="plate_detections")
    op.drop_index("ix_det_photo_id", table_name="plate_detections")
    op.drop_table("plate_detections")
    op.drop_index("ix_photos_pending", table_name="photos")
    op.drop_index("ix_photos_captured_at", table_name="photos")
    op.drop_index("ix_photos_run_id", table_name="photos")
    op.drop_table("photos")
    op.drop_index("ix_runs_start_time", table_name="spot_runs")
    op.drop_index("ix_runs_map_id", table_name="spot_runs")
    op.drop_index("ix_runs_status", table_name="spot_runs")
    op.drop_table("spot_runs")
    op.drop_table("maps")
    op.drop_index("ix_plates_status", table_name="license_plates")
    op.drop_table("license_plates")
    for name in ("fiducial_side", "ocr_status", "run_status", "plate_status"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
