"""CRUD nad tabulkou spot_runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from blondi.db.enums import RunStatus
from blondi.db.models import SpotRun


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
    checkpoint_results_json: list[dict] | None = None,
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
        checkpoint_results_json=list(checkpoint_results_json or []),
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


def mark_progress(
    session: Session,
    run_id: int,
    checkpoints_reached: int,
    *,
    checkpoint_results_json: list[dict] | None = None,
) -> None:
    """Update progress pro ``run_id``. Raise pokud run neexistuje
    (PR-07 FIND-023).
    """
    values: dict = {"checkpoints_reached": checkpoints_reached}
    if checkpoint_results_json is not None:
        values["checkpoint_results_json"] = checkpoint_results_json
    result = session.execute(
        update(SpotRun).where(SpotRun.id == run_id).values(**values)
    )
    if (result.rowcount or 0) != 1:
        raise RuntimeError(
            f"mark_progress: run_id={run_id} nenalezen (rowcount={result.rowcount})."
        )


def finish(
    session: Session,
    run_id: int,
    *,
    status: RunStatus,
    checkpoints_reached: int | None = None,
    abort_reason: str | None = None,
    checkpoint_results_json: list[dict] | None = None,
) -> None:
    """Finalizuje run. Raise pokud run neexistuje (PR-07 FIND-023)."""
    values: dict = {
        "status": status,
        "end_time": datetime.now(timezone.utc),
    }
    if checkpoints_reached is not None:
        values["checkpoints_reached"] = checkpoints_reached
    if abort_reason is not None:
        values["abort_reason"] = abort_reason
    if checkpoint_results_json is not None:
        values["checkpoint_results_json"] = checkpoint_results_json
    result = session.execute(
        update(SpotRun).where(SpotRun.id == run_id).values(**values)
    )
    if (result.rowcount or 0) != 1:
        raise RuntimeError(
            f"finish: run_id={run_id} nenalezen (rowcount={result.rowcount})."
        )


def generate_run_code(now: datetime | None = None) -> str:
    """Vygeneruje lidský run_code typu `run_20260422_1530`."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("run_%Y%m%d_%H%M%S")


def generate_unique_run_code(
    session: Session, now: datetime | None = None, *, max_attempts: int = 20
) -> str:
    """Vygeneruje unikátní run_code přes check-then-act.

    Pozor: má TOCTOU race při paralelních volajících. Pro robustní insert
    použij ``create_run_with_unique_code`` který retry na IntegrityError.
    """
    base = generate_run_code(now=now)
    for attempt in range(max_attempts):
        candidate = base if attempt == 0 else f"{base}_{attempt:02d}"
        exists = session.execute(
            select(SpotRun.id).where(SpotRun.run_code == candidate).limit(1)
        ).scalar_one_or_none()
        if exists is None:
            return candidate
    raise RuntimeError("Could not generate a unique run_code.")


def create_run_with_unique_code(
    session: Session,
    *,
    map_id: int | None,
    map_name_snapshot: str | None,
    checkpoints_total: int,
    operator_label: str | None,
    start_waypoint_id: str | None,
    checkpoint_results_json: list[dict] | None = None,
    notes: str | None = None,
    now: datetime | None = None,
    max_attempts: int = 5,
) -> SpotRun:
    """Vytvoří run s atomic retry na IntegrityError (PR-07 FIND-024).

    Na rozdíl od ``generate_unique_run_code`` + ``create`` nemá TOCTOU race
    mezi dvěma paralelními volajícími — pokud INSERT spadne na UNIQUE
    (race mezi check a insert, nebo mezi dvěma thready), attempt se
    inkrementuje a zkusí další kód.
    """
    from sqlalchemy.exc import IntegrityError

    base = generate_run_code(now=now)
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        candidate = base if attempt == 0 else f"{base}_{attempt:02d}"
        try:
            savepoint = session.begin_nested()
            try:
                run = create(
                    session,
                    run_code=candidate,
                    map_id=map_id,
                    map_name_snapshot=map_name_snapshot,
                    checkpoints_total=checkpoints_total,
                    operator_label=operator_label,
                    start_waypoint_id=start_waypoint_id,
                    checkpoint_results_json=checkpoint_results_json,
                    notes=notes,
                )
                savepoint.commit()
                return run
            except IntegrityError as exc:
                savepoint.rollback()
                last_exc = exc
                continue
        except Exception:
            # Savepoint může selhat pokud session není v transakci (some DB
            # driver combos). Fallback — run a retry celý statement.
            try:
                run = create(
                    session,
                    run_code=candidate,
                    map_id=map_id,
                    map_name_snapshot=map_name_snapshot,
                    checkpoints_total=checkpoints_total,
                    operator_label=operator_label,
                    start_waypoint_id=start_waypoint_id,
                    checkpoint_results_json=checkpoint_results_json,
                    notes=notes,
                )
                return run
            except IntegrityError as exc:
                last_exc = exc
                continue
    raise RuntimeError(
        f"Could not generate a unique run_code after {max_attempts} attempts"
    ) from last_exc


def delete_cascade_batched(
    session: Session, run_id: int, *, photo_batch_size: int = 500
) -> None:
    """Smaže run s batch-deletu fotek (PR-07 FIND-016).

    Cascade DELETE v PG je atomic na DB straně — ale pro velký run
    (1000+ photos × 500 KB BYTEA) to může trvat desítky sekund a
    blokovat UI. Batched approach:

      1. Smaž detections (subquery přes photo_ids).
      2. Smaž photos po ``photo_batch_size``.
      3. Smaž run record.

    Volající commituje mezi kroky. Tato funkce sama commituje uvnitř
    pro postupný progress (UI worker thread vidí částečnou done state).
    """
    from sqlalchemy import delete as sqldelete

    from blondi.db.models import Photo
    from blondi.db.repositories import detections_repo

    # Krok 1: smaž detections všech photos tohoto runu.
    photo_ids_subq = select(Photo.id).where(Photo.run_id == run_id)
    session.execute(
        sqldelete(Photo.__mapper__.entity).where(False)  # noqa — placeholder
    )
    # Použij detections_repo pro smazání (single statement).
    detections_repo.delete_for_run(session, run_id)

    # Krok 2: smaž photos po batchech.
    while True:
        batch_ids = list(
            session.execute(
                select(Photo.id)
                .where(Photo.run_id == run_id)
                .limit(photo_batch_size)
            ).scalars().all()
        )
        if not batch_ids:
            break
        session.execute(
            sqldelete(Photo).where(Photo.id.in_(batch_ids))
        )
        session.commit()

    # Krok 3: smaž run.
    run = session.get(SpotRun, run_id)
    if run is not None:
        session.delete(run)
        session.commit()


def set_return_home(
    session: Session,
    run_id: int,
    *,
    status: str,
    reason: str | None = None,
) -> None:
    session.execute(
        update(SpotRun)
        .where(SpotRun.id == run_id)
        .values(return_home_status=status, return_home_reason=reason)
    )


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
    "generate_unique_run_code",
    "create_run_with_unique_code",
    "delete_cascade_batched",
    "set_return_home",
]
