"""SQLAlchemy 2.0 modely — SPZ registr, mapy, běhy, fotky, detekce, credentials.

Používá Declarative + Mapped[]. Enumy v DB jako native PG ENUM typy.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from blondi.db.enums import FiducialSide, OcrStatus, PlateStatus, RunStatus


class Base(DeclarativeBase):
    """Kořenová deklarativní třída."""


# Sdílené PG enum typy — create_type=False v migracích, create_type=True zde pro metadata.
plate_status_pg = PgEnum(PlateStatus, name="plate_status", create_type=False, values_callable=lambda e: [m.value for m in e])
run_status_pg = PgEnum(RunStatus, name="run_status", create_type=False, values_callable=lambda e: [m.value for m in e])
ocr_status_pg = PgEnum(OcrStatus, name="ocr_status", create_type=False, values_callable=lambda e: [m.value for m in e])
fiducial_side_pg = PgEnum(FiducialSide, name="fiducial_side", create_type=False, values_callable=lambda e: [m.value for m in e])


class LicensePlate(Base):
    """Registr povolených / sledovaných SPZ."""

    __tablename__ = "license_plates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plate_text: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[PlateStatus] = mapped_column(
        plate_status_pg, nullable=False, default=PlateStatus.unknown, index=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<LicensePlate {self.plate_text!r} status={self.status.value}>"


class Map(Base):
    """GraphNav mapa — celá v DB jako ZIP archiv."""

    __tablename__ = "maps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    archive_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    archive_format: Mapped[str] = mapped_column(String(16), nullable=False, default="zip")
    archive_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fiducial_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_waypoint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_capture_sources: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    checkpoints_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metadata_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    archive_is_valid: Mapped[bool] = mapped_column(nullable=False, default=True)
    archive_validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    waypoints_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checkpoints_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by_operator: Mapped[str | None] = mapped_column(String(64), nullable=True)

    runs: Mapped[list["SpotRun"]] = relationship(back_populates="map_ref", lazy="select")

    def __repr__(self) -> str:
        return f"<Map {self.name!r} size={self.archive_size_bytes}B fiducial={self.fiducial_id}>"


class SpotRun(Base):
    """Jeden autonomní běh (playback mapy s focením)."""

    __tablename__ = "spot_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    map_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("maps.id", ondelete="SET NULL"), nullable=True, index=True
    )
    map_name_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[RunStatus] = mapped_column(
        run_status_pg, nullable=False, default=RunStatus.running, index=True
    )
    checkpoints_reached: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checkpoints_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operator_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_waypoint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    abort_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_results_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    return_home_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_requested"
    )
    return_home_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    map_ref: Mapped[Map | None] = relationship(back_populates="runs", lazy="select")
    photos: Mapped[list["Photo"]] = relationship(
        back_populates="run", lazy="select", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<SpotRun {self.run_code!r} status={self.status.value}>"


class Photo(Base):
    """Fotka zachycená Spotem. Ukládáme JPEG bytes přímo v DB."""

    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("spot_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checkpoint_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    camera_source: Mapped[str] = mapped_column(String(64), nullable=False)
    image_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    image_mime: Mapped[str] = mapped_column(String(32), nullable=False, default="image/jpeg")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    ocr_status: Mapped[OcrStatus] = mapped_column(
        ocr_status_pg, nullable=False, default=OcrStatus.pending
    )
    ocr_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ocr_locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ocr_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[SpotRun] = relationship(back_populates="photos", lazy="select")
    detections: Mapped[list["PlateDetection"]] = relationship(
        back_populates="photo", lazy="select", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Photo id={self.id} run={self.run_id} ocr={self.ocr_status.value}>"


class PlateDetection(Base):
    """Jedna detekce SPZ na fotce (YOLO box + OCR text + obě confidence)."""

    __tablename__ = "plate_detections"
    __table_args__ = (
        UniqueConstraint(
            "photo_id", "engine_name", "plate_text", name="ux_det_photo_engine_plate"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    photo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("photos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plate_text: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    detection_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    text_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    engine_name: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    photo: Mapped[Photo] = relationship(back_populates="detections", lazy="select")

    def __repr__(self) -> str:
        return f"<PlateDetection {self.plate_text!r} conf={self.text_confidence}>"


class SpotCredential(Base):
    """Metadata uložených Spot přihlášení. Samotné heslo je v Windows Credential Locker."""

    __tablename__ = "spot_credentials"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    label: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    keyring_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<SpotCredential label={self.label!r} host={self.hostname}>"


__all__ = [
    "Base",
    "LicensePlate",
    "Map",
    "SpotRun",
    "Photo",
    "PlateDetection",
    "SpotCredential",
]
