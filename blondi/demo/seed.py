"""Demo seed — naplní demo DB bohatým datasetem pro screenshoty.

Volá se při startu aplikace v demo módu (z ``main.py`` po DB init). Idempotent:
pokud už je DB naplněná, skipne se. Pro forced reseed nastav
``BLONDI_DEMO_RESEED=1``.

Dataset:
- 5 map (české parkoviště názvy, různé fiducial_id)
- 10 runů (rozprostřených přes 14 dní, mix completed/partial/failed)
- 50 fotek (image_bytes z left.png/right.png placeholder)
- 30 SPZ (české formáty, mix valid/expired/unknown)
- ~70 plate_detections

Bezpečnostní pojistka: ``_assert_demo_database()`` raise pokud DB obsahuje
> 100 SPZ nebo > 1000 runů — ochrana před omylem na produkční DB.
"""

from __future__ import annotations

import hashlib
import io
import os
import random
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from blondi.config import AppConfig
from blondi.db.engine import Session
from blondi.db.enums import OcrStatus, PlateStatus, RunStatus
from blondi.db.models import LicensePlate, Map, Photo, PlateDetection, SpotRun
from blondi.logging_config import get_logger
from blondi.services.contracts import build_checkpoint_plan_payload

_log = get_logger(__name__)

_PROD_DATA_LIMIT_PLATES = 100
_PROD_DATA_LIMIT_RUNS = 1000

_DEMO_ASSETS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "demo"


def seed_database(config: AppConfig) -> None:
    """Idempotent seed. Bezpečně skipne pokud DB má reálná data.

    Raise ``RuntimeError`` pokud ``demo_mode=False`` (defense in depth).
    """
    if not config.demo_mode:
        raise RuntimeError("seed_database lze volat jen v demo módu.")

    reseed = os.environ.get("BLONDI_DEMO_RESEED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

    with Session() as s:
        _assert_demo_database(s)
        plate_count = s.query(LicensePlate).count()
        if plate_count > 0 and not reseed:
            _log.info(
                "Demo seed: DB už obsahuje %d SPZ — skipuji (BLONDI_DEMO_RESEED=1 pro forced reseed).",
                plate_count,
            )
            return

        if reseed and plate_count > 0:
            _log.info("Demo seed: BLONDI_DEMO_RESEED=1 → mažu existující demo data.")
            _wipe_demo_data(s)
            s.commit()

        _log.info("Demo seed: vytvářím dataset (5 map, 10 runů, 50 fotek, 30 SPZ)...")
        # Commit po každé sekci — kdyby pozdější část seedu padla, předchozí
        # data už jsou bezpečně v DB (žádné rollback).
        plates = _seed_plates(s)
        s.commit()
        _log.info("Demo seed: %d SPZ uloženo.", len(plates))

        maps = _seed_maps(s)
        s.commit()
        _log.info("Demo seed: %d map uloženo.", len(maps))

        runs = _seed_runs(s, maps)
        s.commit()
        _log.info("Demo seed: %d runů uloženo.", len(runs))

        photos = _seed_photos(s, runs)
        s.commit()
        _log.info("Demo seed: %d fotek uloženo.", len(photos))

        _seed_detections(s, photos, plates)
        s.commit()
        _log.info("Demo seed: detekce uloženy.")

    _log.info(
        "Demo seed dokončen: %d SPZ, %d map, %d runů, %d fotek.",
        len(plates),
        len(maps),
        len(runs),
        len(photos),
    )


def _assert_demo_database(session) -> None:
    """Hard check že nejsme na produkční DB.

    Kontroluje že počet SPZ je < 100 a počet runs < 1000. Tyto limity
    jsou *velkorysé* aby skutečné demo iterace prošly, ale zabraňují
    omylu kdy by někdo zapomněl ``BLONDI_DEMO_DATABASE_URL``.
    """
    plate_count = session.query(LicensePlate).count()
    run_count = session.query(SpotRun).count()
    if plate_count > _PROD_DATA_LIMIT_PLATES or run_count > _PROD_DATA_LIMIT_RUNS:
        raise RuntimeError(
            f"DB obsahuje příliš mnoho záznamů ({plate_count} SPZ, {run_count} runů) — "
            "vypadá to jako produkční DB. Zkontroluj BLONDI_DEMO_DATABASE_URL "
            "a použij prázdnou demo DB."
        )


def _wipe_demo_data(session) -> None:
    """Smaže demo data — pro reseed. Pořadí kvůli FK."""
    session.query(PlateDetection).delete()
    session.query(Photo).delete()
    session.query(SpotRun).delete()
    session.query(Map).delete()
    session.query(LicensePlate).delete()


# ---- Plates ----

# Realistické české SPZ formáty: ČČ X NNNN (od 2001). V DB jsou ukládány bez
# mezery (přes normalize_plate_text), ale display value v UI ukazuje s mezerou.
_DEMO_PLATE_TEXTS = [
    "1A1 2345", "2B2 3456", "3C3 4567", "4D4 5678", "5E5 6789",
    "6F6 7890", "7G7 8901", "8H8 9012", "9J9 0123", "1K1 1234",
    "2L2 2345", "3M3 3456", "4N4 4567", "5O5 5678", "6P6 6789",
    "7R7 7890", "8S8 8901", "9T9 9012", "1U1 0123", "2V2 1234",
    "3X3 2345", "4Y4 3456", "5Z5 4567", "6AB 5678", "7CD 6789",
    "8EF 7890", "9GH 8901", "1JK 9012", "2LM 0123", "3NP 1234",
]

_DEMO_PLATE_NOTES = {
    "1A1 2345": "Servisní vozidlo recepce",
    "2B2 3456": "Návštěva — IT support",
    "5E5 6789": "Vyhrazené místo pro vedoucího",
    "9J9 0123": "BLACKLIST — neplatí kartu",
    "3M3 3456": "VIP — manažerský parking",
    "7R7 7890": "Dlouhodobé parkování (květen 2026)",
    "1U1 0123": "Sezónní permit (jaro 2026)",
    "6AB 5678": "Externí dodavatel — kuchyně",
    "8EF 7890": "Lifegard parking — denní",
    "3NP 1234": "EXPIRED — vypršela platba 04/2026",
}


def _seed_plates(session) -> list[LicensePlate]:
    today = date.today()
    plates: list[LicensePlate] = []
    for idx, text in enumerate(_DEMO_PLATE_TEXTS):
        # Mix statusů podle indexu.
        if idx % 7 == 0:
            status = PlateStatus.banned
            valid_until = None
        elif idx % 5 == 0:
            status = PlateStatus.expired
            valid_until = today - timedelta(days=30 + idx)
        elif idx % 3 == 0:
            status = PlateStatus.unknown
            valid_until = None
        else:
            status = PlateStatus.active
            valid_until = today + timedelta(days=180 + idx * 5)

        plate = LicensePlate(
            plate_text=text,
            status=status,
            valid_until=valid_until,
            note=_DEMO_PLATE_NOTES.get(text),
        )
        session.add(plate)
        plates.append(plate)
    return plates


# ---- Maps ----

_DEMO_MAP_NAMES = [
    "parkoviste_sever_2026",
    "garaz_b1_2026",
    "areal_zapad_2026",
    "recepce_2026",
    "vychod_dvur_2026",
]

_DEMO_MAP_NOTES = [
    "Hlavní parkoviště před recepcí — 12 stání.",
    "Podzemní garáž B1, smíšený provoz.",
    "Západní areál vč. nakládacích ramp.",
    "Krátká testovací trasa kolem recepce.",
    "Východní dvůr, pouze zaměstnanci.",
]

_DEMO_MAP_FIDUCIAL_IDS = [42, 43, 44, 45, 46]


def _make_dummy_zip() -> tuple[bytes, str]:
    """Vyrobí malý dummy ZIP pro Map.archive_bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("graph", b"demo_graph_placeholder")
        zf.writestr("metadata.json", '{"demo": true}')
        zf.writestr("waypoint_snapshots/wp_demo_0.snapshot", b"demo_wp_0")
        zf.writestr("waypoint_snapshots/wp_demo_1.snapshot", b"demo_wp_1")
    archive = buf.getvalue()
    sha = hashlib.sha256(archive).hexdigest()
    return archive, sha


def _seed_maps(session) -> list[Map]:
    archive, sha = _make_dummy_zip()
    base_time = datetime.now(timezone.utc) - timedelta(days=30)
    maps: list[Map] = []
    for idx, name in enumerate(_DEMO_MAP_NAMES):
        fid = _DEMO_MAP_FIDUCIAL_IDS[idx]
        sources = ["left_fisheye_image", "right_fisheye_image"]
        cp_count = 5 + idx * 2
        wp_count = cp_count + 5
        # Vyrob fake checkpoints_json.
        checkpoints = [
            type(
                "_CP",
                (),
                {
                    "name": f"CP_{i:03d}",
                    "waypoint_id": f"demo_cp_{idx}_{i:03d}",
                    "kind": "checkpoint",
                    "capture_sources": sources,
                    "capture_status": "ok",
                    "saved_sources": sources,
                    "failed_sources": [],
                    "note": "",
                    "created_at": base_time.isoformat(),
                },
            )()
            for i in range(1, cp_count + 1)
        ]
        plan = build_checkpoint_plan_payload(
            map_name=name,
            start_waypoint_id=f"demo_wp_{idx}_start",
            fiducial_id=fid,
            default_capture_sources=sources,
            checkpoints=checkpoints,
        )
        # start_waypoint musí být v checkpoints — aby validace prošla,
        # použijeme první checkpoint waypoint_id.
        first_wp = checkpoints[0].waypoint_id if checkpoints else None

        m = Map(
            name=name,
            archive_bytes=archive,
            archive_format="zip",
            archive_sha256=sha,
            archive_size_bytes=len(archive),
            fiducial_id=fid,
            start_waypoint_id=first_wp,
            default_capture_sources=sources,
            checkpoints_json=plan,
            metadata_version=2,
            archive_is_valid=True,
            archive_validation_error=None,
            waypoints_count=wp_count,
            checkpoints_count=cp_count,
            note=_DEMO_MAP_NOTES[idx],
            created_by_operator="demo",
        )
        session.add(m)
        maps.append(m)
    return maps


# ---- Runs ----


def _seed_runs(session, maps: list[Map]) -> list[SpotRun]:
    statuses = [
        RunStatus.completed,
        RunStatus.completed,
        RunStatus.completed,
        RunStatus.completed,
        RunStatus.partial,
        RunStatus.partial,
        RunStatus.failed,
        RunStatus.aborted,
        RunStatus.completed,
        RunStatus.completed,
    ]
    runs: list[SpotRun] = []
    base_time = datetime.now(timezone.utc) - timedelta(days=14)
    for idx, status in enumerate(statuses):
        m = maps[idx % len(maps)]
        start = base_time + timedelta(hours=idx * 30)
        end = start + timedelta(minutes=8 + idx)
        cp_total = m.checkpoints_count or 5
        if status == RunStatus.completed:
            cp_reached = cp_total
        elif status == RunStatus.partial:
            cp_reached = max(1, cp_total - 2)
        elif status == RunStatus.failed:
            cp_reached = 0
        else:  # aborted
            cp_reached = max(1, cp_total // 2)

        abort_reason: Optional[str] = None
        if status == RunStatus.failed:
            abort_reason = "RobotLostError: ztratil GraphNav lokalizaci po 18 m."
        elif status == RunStatus.aborted:
            abort_reason = "Aborted by user"

        run = SpotRun(
            run_code=f"RUN-2026-{idx + 1:03d}",
            map_id=m.id,
            map_name_snapshot=m.name,
            status=status,
            start_time=start,
            end_time=end,
            checkpoints_reached=cp_reached,
            checkpoints_total=cp_total,
            operator_label="demo",
            start_waypoint_id=m.start_waypoint_id,
            abort_reason=abort_reason,
            checkpoint_results_json=[],
            return_home_status="not_requested",
        )
        session.add(run)
        runs.append(run)
    return runs


# ---- Photos + Detections ----


def _load_demo_image_bytes() -> tuple[bytes, int, int]:
    """Vrátí JPEG bytes z ``demo/left.jpg`` (reused jako image_bytes pro fotky).

    Pokud asset chybí, vyrobí minimální placeholder JPEG.
    """
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QPixmap

    left_jpg = _DEMO_ASSETS_DIR / "left.jpg"
    if left_jpg.is_file():
        # Surové JPEG bytes můžeme přečíst přímo (bez dekódovat/enkódovat).
        raw = left_jpg.read_bytes()
        pix = QPixmap(str(left_jpg))
        if not pix.isNull() and raw:
            return raw, pix.width(), pix.height()
    # Fallback — generuj minimální placeholder.
    pix = QPixmap(640, 480)
    pix.fill()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    pix.save(buf, "JPEG", 70)
    buf.close()
    return bytes(ba.data()), 640, 480


def _seed_photos(session, runs: list[SpotRun]) -> list[Photo]:
    image_bytes, width, height = _load_demo_image_bytes()
    photos: list[Photo] = []
    sources_cycle = ["left_fisheye_image", "right_fisheye_image"]
    photos_per_run_distribution = [10, 8, 7, 6, 5, 4, 3, 3, 2, 2]  # = 50
    for run, target in zip(runs, photos_per_run_distribution):
        for i in range(target):
            cp_name = f"CP_{(i % 8) + 1:03d}"
            src = sources_cycle[i % 2]
            captured = (run.start_time or datetime.now(timezone.utc)) + timedelta(
                minutes=i
            )
            photo = Photo(
                run_id=run.id,
                checkpoint_name=cp_name,
                camera_source=src,
                image_bytes=image_bytes,
                image_mime="image/jpeg",
                width=width,
                height=height,
                captured_at=captured,
                ocr_status=OcrStatus.done if i % 7 != 0 else OcrStatus.pending,
            )
            session.add(photo)
            photos.append(photo)
    return photos


def _seed_detections(
    session, photos: list[Photo], plates: list[LicensePlate]
) -> None:
    rng = random.Random(42)
    plate_pool = [p.plate_text for p in plates]
    for photo in photos:
        if photo.ocr_status != OcrStatus.done:
            continue
        # 1-2 detekce per fotka, **různé** plate_text (unique constraint).
        n_detections = rng.choice([1, 1, 1, 2])
        chosen = rng.sample(plate_pool, n_detections)
        for plate_text in chosen:
            text_conf = round(rng.uniform(0.65, 0.95), 3)
            det_conf = round(rng.uniform(0.55, 0.92), 3)
            bbox = {
                "x": rng.randint(50, 200),
                "y": rng.randint(80, 250),
                "w": rng.randint(180, 260),
                "h": rng.randint(50, 80),
            }
            detection = PlateDetection(
                photo_id=photo.id,
                plate_text=plate_text,
                text_confidence=text_conf,
                detection_confidence=det_conf,
                bbox=bbox,
                engine_name="european-plates-mobile-vit-v2",
                engine_version="1.0",
            )
            session.add(detection)


__all__ = ["seed_database"]
