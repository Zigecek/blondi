"""CRUD nad tabulkou spot_runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from spot_operator.db.enums import RunStatus
from spot_operator.db.models import SpotRun


def create(
    session: Session,
    *,
    run_code: str,
    map_id: int | None,
    map_name_snapshot: str | None,
    checkpoints_total: int,
    operator_label: str | None,
    start_waypoint_id: str | None,
    notes: str | None = None,
) -> SpotRun:
    run = SpotRun(
        run_code=run_code,
        map_id=map_id,
        map_name_snapshot=map_name_snapshot,
        checkpoints_total=checkpoints_total,
        operator_label=operator_label,
        start_waypoint_id=start_waypoint_id,
        notes=notes,
        status=RunStatus.running,
    )
    session.add(run)
    session.flush()
    return run


def get(session: Session, run_id: int) -> SpotRun | None:
    return session.get(SpotRun, run_id)


def list_recent(session: Session, limit: int = 100) -> Sequence[SpotRun]:
    return (
        session.execute(
            select(SpotRun).order_by(SpotRun.start_time.desc()).limit(limit)
        )
        .scalars()
        .all()
    )


def mark_progress(session: Session, run_id: int, checkpoints_reached: int) -> None:
    session.execute(
        update(SpotRun)
        .where(SpotRun.id == run_id)
        .values(checkpoints_reached=checkpoints_reached)
    )


def finish(
    session: Session,
    run_id: int,
    *,
    status: RunStatus,
    checkpoints_reached: int | None = None,
    abort_reason: str | None = None,
) -> None:
    values: dict = {
        "status": status,
        "end_time": datetime.now(timezone.utc),
    }
    if checkpoints_reached is not None:
        values["checkpoints_reached"] = checkpoints_reached
    if abort_reason is not None:
        values["abort_reason"] = abort_reason
    session.execute(update(SpotRun).where(SpotRun.id == run_id).values(**values))


def generate_run_code(now: datetime | None = None) -> str:
    """Vygeneruje lidský run_code typu `run_20260422_1530`."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("run_%Y%m%d_%H%M%S")


__all__ = [
    "create",
    "get",
    "list_recent",
    "mark_progress",
    "finish",
    "generate_run_code",
]
