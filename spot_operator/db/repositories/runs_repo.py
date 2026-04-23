"""CRUD nad tabulkou spot_runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from spot_operator.db.enums import RunStatus
from spot_operator.db.models import SpotRun


# --- DTO pro CRUD ---

@dataclass(frozen=True)
class RunRow:
    """DTO pro řádek v tabulce Běhy."""

    id: int
    run_code: str
    map_name_snapshot: str | None
    start_time: datetime | None
    end_time: datetime | None
    status: str
    checkpoints_reached: int
    checkpoints_total: int


@dataclass(frozen=True)
class RunSummary:
    """DTO pro detail běhu (RunDetailDialog)."""

    id: int
    run_code: str
    map_name_snapshot: str | None
    start_time: datetime | None
    end_time: datetime | None
    status: str
    checkpoints_reached: int
    checkpoints_total: int
    operator_label: str | None
    notes: str | None
    abort_reason: str | None


_SORTABLE_RUN_COLS: frozenset[str] = frozenset(
    {"id", "run_code", "start_time", "end_time", "status"}
)


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


def _to_run_row(r: SpotRun) -> RunRow:
    return RunRow(
        id=r.id,
        run_code=r.run_code,
        map_name_snapshot=r.map_name_snapshot,
        start_time=r.start_time,
        end_time=r.end_time,
        status=r.status.value,
        checkpoints_reached=r.checkpoints_reached,
        checkpoints_total=r.checkpoints_total,
    )


def list_page(
    session: Session,
    *,
    offset: int = 0,
    limit: int = 100,
    sort_by: str = "start_time",
    sort_desc: bool = True,
) -> list[RunRow]:
    """Stránka běhů jako lightweight DTO."""
    if sort_by not in _SORTABLE_RUN_COLS:
        sort_by = "start_time"
    col = getattr(SpotRun, sort_by)
    order = col.desc() if sort_desc else col.asc()

    stmt = (
        select(SpotRun)
        .order_by(order, SpotRun.id.desc())
        .offset(max(offset, 0))
        .limit(max(limit, 1))
    )
    return [_to_run_row(r) for r in session.execute(stmt).scalars().all()]


def count(session: Session) -> int:
    """Celkový počet běhů."""
    return int(session.execute(select(func.count(SpotRun.id))).scalar_one() or 0)


def get_summary(session: Session, run_id: int) -> RunSummary | None:
    """Detail jednoho běhu (včetně operator/notes/abort_reason) jako DTO."""
    r = session.get(SpotRun, run_id)
    if r is None:
        return None
    return RunSummary(
        id=r.id,
        run_code=r.run_code,
        map_name_snapshot=r.map_name_snapshot,
        start_time=r.start_time,
        end_time=r.end_time,
        status=r.status.value,
        checkpoints_reached=r.checkpoints_reached,
        checkpoints_total=r.checkpoints_total,
        operator_label=r.operator_label,
        notes=r.notes,
        abort_reason=r.abort_reason,
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
    "RunRow",
    "RunSummary",
    "create",
    "get",
    "list_recent",
    "list_page",
    "count",
    "get_summary",
    "mark_progress",
    "finish",
    "generate_run_code",
]
