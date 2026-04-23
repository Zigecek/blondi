"""Typed contracts for map metadata and checkpoint execution results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Sequence

MAP_METADATA_SCHEMA_VERSION = 2

CAPTURE_STATUS_NOT_APPLICABLE = "not_applicable"
CAPTURE_STATUS_OK = "ok"
CAPTURE_STATUS_PARTIAL = "partial"
CAPTURE_STATUS_FAILED = "failed"

RETURN_HOME_STATUS_NOT_REQUESTED = "not_requested"
RETURN_HOME_STATUS_IN_PROGRESS = "in_progress"
RETURN_HOME_STATUS_COMPLETED = "completed"
RETURN_HOME_STATUS_FAILED = "failed"


class CaptureNote(str, Enum):
    """Standardizované hodnoty pro ``RecordedCheckpoint.note`` a
    ``checkpoints_json[*].note``. Enum dává strong typing místo magic strings.
    """

    OK = ""
    CAPTURE_FAILED = "capture_failed"
    CAPTURE_PARTIAL = "capture_partial"


class CaptureFailedError(RuntimeError):
    """Raise když všechny image sources u checkpointu selžou.

    Operátor by měl dostat dialog s volbou retry / skip / abort. Recording
    service **nesmí** rozhodovat sám (dřívější silent demotion na waypoint).
    """

    def __init__(
        self,
        *,
        name: str,
        saved_sources: Sequence[str],
        failed_sources: Sequence[str],
    ):
        self.name = name
        self.saved_sources = tuple(saved_sources)
        self.failed_sources = tuple(failed_sources)
        super().__init__(
            f"Capture failed for {name!r}: 0 saved, {len(self.failed_sources)} failed"
        )


@dataclass(frozen=True, slots=True)
class MapCheckpoint:
    name: str
    waypoint_id: str
    kind: str
    capture_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MapPlan:
    schema_version: int
    map_name: str
    start_waypoint_id: str | None
    fiducial_id: int | None
    default_capture_sources: tuple[str, ...]
    checkpoints: tuple[MapCheckpoint, ...]


@dataclass(frozen=True, slots=True)
class CaptureSummary:
    status: str
    expected_sources: tuple[str, ...]
    saved_sources: tuple[str, ...]
    failed_sources: tuple[str, ...]
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.status in (CAPTURE_STATUS_OK, CAPTURE_STATUS_NOT_APPLICABLE)


@dataclass(frozen=True, slots=True)
class CheckpointResult:
    name: str
    waypoint_id: str
    nav_outcome: str
    capture_status: str
    expected_sources: tuple[str, ...]
    saved_sources: tuple[str, ...]
    failed_sources: tuple[str, ...]
    error: str | None
    started_at: str
    finished_at: str

    @property
    def is_complete(self) -> bool:
        return self.nav_outcome == "reached" and self.capture_status in (
            CAPTURE_STATUS_OK,
            CAPTURE_STATUS_NOT_APPLICABLE,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "waypoint_id": self.waypoint_id,
            "nav_outcome": self.nav_outcome,
            "capture_status": self.capture_status,
            "expected_sources": list(self.expected_sources),
            "saved_sources": list(self.saved_sources),
            "failed_sources": list(self.failed_sources),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def build_checkpoint_plan_payload(
    *,
    map_name: str,
    start_waypoint_id: str | None,
    fiducial_id: int | None,
    default_capture_sources: Sequence[str],
    checkpoints: Iterable[Any],
) -> dict[str, Any]:
    return {
        "schema_version": MAP_METADATA_SCHEMA_VERSION,
        "map_name": map_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fiducial": {"id": fiducial_id},
        "start_waypoint_id": start_waypoint_id,
        "default_capture_sources": list(_normalize_sources(default_capture_sources)),
        "checkpoints": [
            {
                "name": getattr(cp, "name"),
                "waypoint_id": getattr(cp, "waypoint_id"),
                "kind": getattr(cp, "kind"),
                "capture_sources": list(
                    _normalize_sources(getattr(cp, "capture_sources", ()))
                ),
                "capture_status": getattr(cp, "capture_status", None),
                "saved_sources": list(
                    _normalize_sources(getattr(cp, "saved_sources", ()))
                ),
                "failed_sources": list(
                    _normalize_sources(getattr(cp, "failed_sources", ()))
                ),
                "note": getattr(cp, "note", ""),
                "created_at": getattr(cp, "created_at", ""),
            }
            for cp in checkpoints
        ],
    }


def parse_checkpoint_plan(
    raw: dict[str, Any] | None,
    *,
    fallback_map_name: str,
    fallback_start_waypoint_id: str | None,
    fallback_default_capture_sources: Sequence[str],
    fallback_fiducial_id: int | None,
) -> MapPlan:
    from spot_operator.logging_config import get_logger
    _log_contracts = get_logger(__name__)

    payload = raw or {}
    if not isinstance(payload, dict):
        raise ValueError("Map checkpoints payload must be an object.")

    schema_version = _normalize_schema_version(payload.get("schema_version"))
    if schema_version > MAP_METADATA_SCHEMA_VERSION:
        # Forward-compat warning — zachovat import-time lazy log.
        _log_contracts.warning(
            "Map schema version %d is newer than supported %d; some fields may be ignored.",
            schema_version,
            MAP_METADATA_SCHEMA_VERSION,
        )
    map_name = _as_optional_str(payload.get("map_name")) or fallback_map_name
    start_waypoint_id = (
        _as_optional_str(payload.get("start_waypoint_id")) or fallback_start_waypoint_id
    )
    default_capture_sources = _normalize_sources(
        payload.get("default_capture_sources") or fallback_default_capture_sources
    )
    fiducial_id = _extract_fiducial_id(payload, fallback_fiducial_id)

    checkpoints_payload = payload.get("checkpoints") or []
    if not isinstance(checkpoints_payload, list):
        raise ValueError("Map checkpoints payload must contain a list of checkpoints.")

    checkpoints: list[MapCheckpoint] = []
    for idx, item in enumerate(checkpoints_payload):
        if not isinstance(item, dict):
            raise ValueError(f"Checkpoint #{idx + 1} is not an object.")
        name = _required_str(item.get("name"), f"checkpoints[{idx}].name")
        waypoint_id = _required_str(
            item.get("waypoint_id"), f"checkpoints[{idx}].waypoint_id"
        )
        kind = (_as_optional_str(item.get("kind")) or "checkpoint").strip().lower()
        if kind not in {"waypoint", "checkpoint"}:
            raise ValueError(f"Unsupported checkpoint kind: {kind!r}")
        capture_sources = _normalize_sources(
            item.get("capture_sources") or default_capture_sources
        )
        checkpoints.append(
            MapCheckpoint(
                name=name,
                waypoint_id=waypoint_id,
                kind=kind,
                capture_sources=capture_sources,
            )
        )

    return MapPlan(
        schema_version=schema_version,
        map_name=map_name,
        start_waypoint_id=start_waypoint_id,
        fiducial_id=fiducial_id,
        default_capture_sources=default_capture_sources,
        checkpoints=tuple(checkpoints),
    )


def build_checkpoint_result(
    *,
    name: str,
    waypoint_id: str,
    nav_outcome: str,
    capture_status: str,
    expected_sources: Sequence[str],
    saved_sources: Sequence[str],
    failed_sources: Sequence[str],
    error: str | None,
    started_at: datetime,
    finished_at: datetime,
) -> CheckpointResult:
    return CheckpointResult(
        name=name,
        waypoint_id=waypoint_id,
        nav_outcome=nav_outcome,
        capture_status=capture_status,
        expected_sources=tuple(expected_sources),
        saved_sources=tuple(saved_sources),
        failed_sources=tuple(failed_sources),
        error=error,
        started_at=started_at.astimezone(timezone.utc).isoformat(),
        finished_at=finished_at.astimezone(timezone.utc).isoformat(),
    )


def checkpoint_results_to_payload(
    checkpoint_results: Sequence[CheckpointResult],
) -> list[dict[str, Any]]:
    return [result.to_payload() for result in checkpoint_results]


def parse_checkpoint_results(raw: Sequence[dict[str, Any]] | None) -> tuple[CheckpointResult, ...]:
    if raw is None:
        return ()
    out: list[CheckpointResult] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Checkpoint result #{idx + 1} is not an object.")
        # Timestamps jsou tolerantní — legacy záznamy bez started_at/finished_at
        # se neprojeví jako hard fail při čtení (FIND-042). Při write cesta stále
        # vyžaduje datetime (build_checkpoint_result).
        out.append(
            CheckpointResult(
                name=_required_str(item.get("name"), f"checkpoint_results[{idx}].name"),
                waypoint_id=_required_str(
                    item.get("waypoint_id"),
                    f"checkpoint_results[{idx}].waypoint_id",
                ),
                nav_outcome=_required_str(
                    item.get("nav_outcome"),
                    f"checkpoint_results[{idx}].nav_outcome",
                ),
                capture_status=_required_str(
                    item.get("capture_status"),
                    f"checkpoint_results[{idx}].capture_status",
                ),
                expected_sources=_normalize_sources(item.get("expected_sources") or ()),
                saved_sources=_normalize_sources(item.get("saved_sources") or ()),
                failed_sources=_normalize_sources(item.get("failed_sources") or ()),
                error=_as_optional_str(item.get("error")),
                started_at=_as_optional_str(item.get("started_at")) or "",
                finished_at=_as_optional_str(item.get("finished_at")) or "",
            )
        )
    return tuple(out)


def validate_plan_invariants(plan: MapPlan) -> None:
    """Raise ValueError pokud ``plan`` porušuje logické invarianty.

    Kontroluje:
      1. ``start_waypoint_id`` existuje v ``plan.checkpoints[].waypoint_id``
         (pokud je nastavený — None je OK jen pro prázdné plány).
      2. Žádné duplikátní ``name`` napříč checkpointy.
      3. Žádné duplikátní ``waypoint_id``.
      4. Aspoň 1 checkpoint (prázdný plán je validní jen při save ještě
         ne-zaznamenaných map — save_map_to_db to další kontrolou filtruje).

    Volá se v ``save_map_to_db`` před INSERT, aby se sémanticky rozbitá mapa
    nikdy nedostala do DB.
    """
    if not plan.checkpoints:
        raise ValueError(
            "Mapa nemá žádné checkpointy — recording pravděpodobně skončil "
            "předčasně nebo operátor nekliknul ani jeden waypoint/checkpoint."
        )

    waypoint_ids: set[str] = set()
    names: set[str] = set()
    for idx, cp in enumerate(plan.checkpoints):
        if cp.name in names:
            raise ValueError(
                f"Checkpoint #{idx + 1}: duplikátní jméno {cp.name!r}."
            )
        names.add(cp.name)
        if cp.waypoint_id in waypoint_ids:
            raise ValueError(
                f"Checkpoint #{idx + 1} ({cp.name!r}): duplikátní waypoint_id "
                f"{cp.waypoint_id!r}."
            )
        waypoint_ids.add(cp.waypoint_id)

    if plan.start_waypoint_id and plan.start_waypoint_id not in waypoint_ids:
        raise ValueError(
            f"start_waypoint_id {plan.start_waypoint_id!r} není v seznamu "
            "checkpoint waypoint_ids — mapa je nekonzistentní."
        )


def validate_sources_known(
    sources: Sequence[str], available_sources: Sequence[str], *, context: str
) -> list[str]:
    available = {src for src in available_sources if isinstance(src, str) and src}
    normalized = list(_normalize_sources(sources))
    if not available:
        return normalized
    unknown = [src for src in normalized if src not in available]
    if unknown:
        raise ValueError(
            f"{context}: unknown capture source(s): {', '.join(sorted(unknown))}"
        )
    return normalized


def _extract_fiducial_id(payload: dict[str, Any], fallback: int | None) -> int | None:
    if "fiducial" in payload:
        fiducial = payload.get("fiducial") or {}
        if fiducial is not None and not isinstance(fiducial, dict):
            # Legacy / manually edited payload může mít "fiducial": <int>
            # místo "fiducial": {"id": <int>}. Tolerujeme jako shortcut.
            if isinstance(fiducial, int) and not isinstance(fiducial, bool):
                from spot_operator.logging_config import get_logger
                get_logger(__name__).warning(
                    "Legacy fiducial block as scalar int (%d) — interpreted as {'id': %d}.",
                    fiducial, fiducial,
                )
                return fiducial
            raise ValueError("Map fiducial block must be an object or integer.")
        return _as_optional_int((fiducial or {}).get("id"), fallback)
    return _as_optional_int(payload.get("fiducial_id"), fallback)


def _normalize_schema_version(value: Any) -> int:
    normalized = _as_optional_int(value, 1)
    return max(normalized or 1, 1)


def _normalize_sources(value: Sequence[str] | Any) -> tuple[str, ...]:
    if value is None:
        return ()
    # Legacy: některé starší záznamy mohou mít capture_sources jako scalar
    # string místo list (PR-03 FIND-040).
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("Capture sources must be a list of strings.")
    out: list[str] = []
    for item in value:
        text = _required_str(item, "capture_sources item")
        if text not in out:
            out.append(text)
    return tuple(out)


def _required_str(value: Any, field_name: str) -> str:
    text = _as_optional_str(value)
    if not text:
        raise ValueError(f"Missing or invalid {field_name}.")
    return text


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected string, got {type(value).__name__}.")
    text = value.strip()
    return text or None


def _as_optional_int(value: Any, fallback: int | None = None) -> int | None:
    if value is None:
        return fallback
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid integer value.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            # Prázdný string == hodnota nezadaná (PR-03 FIND-039).
            return fallback
        try:
            return int(stripped)
        except ValueError as exc:
            raise ValueError(f"Expected integer, got empty or non-numeric string: {value!r}") from exc
    raise ValueError(f"Expected integer, got {type(value).__name__}.")


__all__ = [
    "MAP_METADATA_SCHEMA_VERSION",
    "CAPTURE_STATUS_NOT_APPLICABLE",
    "CAPTURE_STATUS_OK",
    "CAPTURE_STATUS_PARTIAL",
    "CAPTURE_STATUS_FAILED",
    "RETURN_HOME_STATUS_NOT_REQUESTED",
    "RETURN_HOME_STATUS_IN_PROGRESS",
    "RETURN_HOME_STATUS_COMPLETED",
    "RETURN_HOME_STATUS_FAILED",
    "CaptureNote",
    "CaptureFailedError",
    "MapCheckpoint",
    "MapPlan",
    "CaptureSummary",
    "CheckpointResult",
    "build_checkpoint_plan_payload",
    "parse_checkpoint_plan",
    "build_checkpoint_result",
    "checkpoint_results_to_payload",
    "parse_checkpoint_results",
    "validate_plan_invariants",
    "validate_sources_known",
]
