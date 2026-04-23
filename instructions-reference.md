---
version: 1.3.0
last_updated: 2026-04-23
next_review: 2026-07-22
applies_to:
  python: "3.10"
  bosdyn: "5.1.x"
  pyside: "6.7+"
  sqlalchemy: "2.0.x"
document_role: reference
---

# Spot Operator — Implementační reference

Tento dokument je **implementační reference** pro AI agenty a vývojáře.
Normativní pravidla (co se smí/nesmí) najdeš v [`instructions.md`](instructions.md).
Operátorský návod je v [`README.md`](README.md).

Agent si tento dokument **načte až když potřebuje konkrétní detail** — API signaturu,
DB sloupec, code sample. Pro pochopení kontextu a pravidel stačí `instructions.md`.

---

## Obsah

1. [Adresářový layout](#1-adresářový-layout)
2. [DB schéma](#2-db-schéma)
3. [API autonomy + ocr (signatury)](#3-api-autonomy--ocr-signatury)
4. [Code samples — 9 kritických pasáží](#4-code-samples--9-kritických-pasáží)
5. [Implementační pořadí](#5-implementační-pořadí)
6. [Styl kódu](#6-styl-kódu)
7. [Jak pinout verze závislostí](#7-jak-pinout-verze-závislostí)

---

## 1. Adresářový layout

```text
c:\Users\zige\spot\
│
├── main.py                         # entry: bootstrap → migrate → QApplication → MainWindow
├── launch.bat                      # setup (pokud treba) + run
├── setup_venv.bat                  # py -3.10 -m venv .venv + pip install
├── run_app.bat                     # aktivace venv + python main.py
├── activate_venv.bat
├── deactivate_venv.txt
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md                       # operátorský návod
├── instructions.md                 # normativní pravidla pro AI/dev
├── instructions-reference.md       # tento soubor
├── CHANGELOG.md                    # historie verzí dokumentace
├── alembic.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── *_0001_initial.py
│
├── spot_operator/                  # hlavní balíček (unique název, NIKDY `app`)
│   ├── __init__.py                 # __version__
│   ├── bootstrap.py                # sys.path injekce autonomy/ + ocr/
│   ├── config.py                   # AppConfig.load_from_env()
│   ├── logging_config.py           # RotatingFileHandler + Qt forward
│   ├── constants.py                # CAMERA_*, OCR_ENGINE_*, threshold, regex
│   │
│   ├── db/
│   │   ├── engine.py               # create_engine + scoped_session(scopefunc=get_ident)
│   │   ├── enums.py                # PlateStatus, RunStatus, OcrStatus, FiducialSide
│   │   ├── models.py               # SQLAlchemy 2.0 Mapped[]
│   │   ├── migrations.py           # alembic.command.upgrade(cfg, "head") programově
│   │   └── repositories/
│   │       ├── plates_repo.py
│   │       ├── maps_repo.py
│   │       ├── runs_repo.py
│   │       ├── photos_repo.py      # včetně claim_next_pending + sweep_zombies
│   │       ├── detections_repo.py
│   │       └── credentials_repo.py
│   │
│   ├── robot/                      # wrappery a session factory nad autonomy
│   │   ├── session_factory.py      # SpotBundle (session + estop + lease + power + move_dispatcher)
│   │   └── dual_side_capture.py    # tolerantní capture z více sources
│   │
│   ├── ocr/
│   │   ├── dtos.py                 # Detection, BoundingBox
│   │   ├── detector.py             # YoloDetector (ultralytics YOLO)
│   │   ├── reader.py               # FastPlateReader (ONNX)
│   │   ├── pipeline.py             # OcrPipeline.process(bytes) -> list[Detection]
│   │   └── fallback.py             # nomeroff v subprocessu (reprocess_bytes)
│   │
│   ├── services/
│   │   ├── map_archiver.py         # zip_map_dir + extract_map_archive + sha256
│   │   ├── map_storage.py          # save_map_to_db + map_extracted context manager
│   │   ├── ocr_worker.py           # QThread polling pending fotek
│   │   ├── photo_sink.py           # encode_bgr_to_jpeg + save_photo_to_db
│   │   ├── zip_exporter.py         # build_run_zip(run_id) z DB
│   │   ├── spot_wifi.py            # ping + TCP check + SSID readout
│   │   ├── credentials_service.py  # save/load/delete přes keyring
│   │   ├── recording_service.py    # obálka GraphNavRecorder + WaypointNamer
│   │   └── playback_service.py     # obálka GraphNavNavigator + Qt signály
│   │
│   └── ui/
│       ├── main_window.py          # launcher se 3 tlačítky + DB status
│       ├── common/
│       │   ├── estop_floating.py   # vždy viditelný červený E-STOP widget
│       │   ├── dialogs.py          # confirm/info/error/warning (česky)
│       │   └── workers.py          # FunctionWorker (QThread + signály)
│       ├── wizards/
│       │   ├── base_wizard.py      # SpotWizard (F1, close-guard, set_bundle)
│       │   ├── recording_wizard.py # 5 kroků
│       │   ├── playback_wizard.py  # 6 kroků
│       │   └── pages/
│       │       ├── wifi_page.py             # sdílený: Wi-Fi check (ping + TCP)
│       │       ├── login_page.py            # sdílený: login + keyring profily
│       │       ├── fiducial_page.py         # sdílený: live view + WASD teleop + power-on + fiducial check
│       │       ├── teleop_record_page.py    # WASD + per-checkpoint foto vlevo/vpravo/obě + waypointy
│       │       ├── save_map_page.py         # re-check fiducial + uložit do DB
│       │       ├── map_select_page.py       # seznam map z DB
│       │       ├── playback_run_page.py     # START + E-Stop + stop s návratem
│       │       └── playback_result_page.py  # shrnutí + export ZIP
│       └── crud/                   # !!! ODSTRANITELNÉ !!!
│           ├── __init__.py
│           ├── crud_window.py      # QTabWidget: SPZ + Běhy + Fotky
│           ├── spz_tab.py          # CRUD registru SPZ
│           ├── runs_tab.py         # tabulka běhů + export ZIP
│           └── photos_tab.py       # galerie + detail + re-OCR Nomeroffem
│
├── logs/                           # rotující log
├── temp/                           # dočasné extract cíle pro playback (cleanup při startu)
├── tests/
│   ├── conftest.py                 # bootstrap sys.path
│   ├── unit/
│   │   ├── test_waypoint_namer.py
│   │   ├── test_map_archiver.py
│   │   ├── test_plates_repo.py
│   │   └── test_ocr_normalize.py
│   └── integration/
│       └── test_spot_connect.py    # @skipif SPOT_INTEGRATION_TESTS != 1
│
├── autonomy/                       # PODPROJEKT — NEMĚNIT (kromě additive modulů)
│   └── app/robot/
│       ├── fiducial_check.py       # NEW additive
│       ├── return_home.py          # NEW additive
│       └── waypoint_namer.py       # NEW additive
│
└── ocr/                            # PODPROJEKT — NEMĚNIT VŮBEC
```

---

## 2. DB schéma

### Enumy (PG native ENUM)

```sql
plate_status   : active | expired | banned | unknown
run_status     : running | completed | aborted | failed | partial
ocr_status     : pending | processing | done | failed
fiducial_side  : left | right | both
```

### Tabulka `license_plates`

Registr schválených / sledovaných SPZ.

| Sloupec | Typ | Poznámka |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `plate_text` | `VARCHAR(16) UNIQUE NOT NULL` | normalizovaný (uppercase, bez mezer) |
| `valid_until` | `DATE NULL` | null = neomezená |
| `status` | `plate_status NOT NULL DEFAULT 'unknown'` | |
| `note` | `TEXT` | |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ DEFAULT now()` | |

Indexy: `ix_plates_status`, unique `ux_plates_text`.

### Tabulka `maps`

Celá GraphNav mapa jako ZIP v DB.

| Sloupec | Typ | Poznámka |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `name` | `VARCHAR(128) UNIQUE NOT NULL` | regex `^[A-Za-z0-9_-]{3,40}$` |
| `archive_bytes` | `BYTEA NOT NULL` | ZIP obsahující `graph/`, `waypoint_snapshots/`, `edge_snapshots/`, `checkpoints.json` |
| `archive_format` | `VARCHAR(16) DEFAULT 'zip'` | |
| `archive_sha256` | `VARCHAR(64) NOT NULL` | ověří se při extract |
| `archive_size_bytes` | `BIGINT NOT NULL` | audit |
| `fiducial_id` | `INT NULL` | startovací AprilTag ID |
| `start_waypoint_id` | `VARCHAR(64) NULL` | pro return-home |
| `default_capture_sources` | `JSONB NOT NULL` | např. `["left_fisheye_image","right_fisheye_image"]` |
| `checkpoints_json` | `JSONB` | snapshot `checkpoints.json` pro rychlý read bez extract |
| `waypoints_count` | `INT` | audit |
| `checkpoints_count` | `INT` | audit |
| `note` | `TEXT` | |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |
| `created_by_operator` | `VARCHAR(64)` | |

**Storage:** `ALTER TABLE maps ALTER COLUMN archive_bytes SET STORAGE EXTERNAL` (ZIP už je komprimovaný; TOAST-komprese plýtvá CPU).

### Tabulka `spot_runs`

Jedno spuštění playbacku.

| Sloupec | Typ | Poznámka |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `run_code` | `VARCHAR(32) UNIQUE NOT NULL` | lidský, např. `run_20260422_1530` |
| `map_id` | `BIGINT FK maps.id ON DELETE SET NULL` | |
| `map_name_snapshot` | `VARCHAR(128)` | denormalizace pro případ smazání mapy |
| `start_time` | `TIMESTAMPTZ DEFAULT now()` | |
| `end_time` | `TIMESTAMPTZ NULL` | |
| `status` | `run_status DEFAULT 'running'` | |
| `checkpoints_reached` | `INT DEFAULT 0` | |
| `checkpoints_total` | `INT DEFAULT 0` | |
| `operator_label` | `VARCHAR(64)` | |
| `notes` | `TEXT` | |
| `start_waypoint_id` | `VARCHAR(64)` | denormalizováno pro return-home |
| `abort_reason` | `TEXT` | |

Indexy: `ix_runs_status`, `ix_runs_map_id`, `ix_runs_start_time DESC`.

### Tabulka `photos`

Fotky pořízené během playbacku. JPEG bytes přímo v DB.

| Sloupec | Typ | Poznámka |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `run_id` | `BIGINT FK spot_runs.id ON DELETE CASCADE` | |
| `checkpoint_name` | `VARCHAR(64)` | např. `CP_001` |
| `camera_source` | `VARCHAR(64) NOT NULL` | `left_fisheye_image`, ... |
| `image_bytes` | `BYTEA NOT NULL` | JPEG q=85 (~150 KB) |
| `image_mime` | `VARCHAR(32) DEFAULT 'image/jpeg'` | |
| `width` | `INT` | |
| `height` | `INT` | |
| `captured_at` | `TIMESTAMPTZ DEFAULT now()` | |
| `ocr_status` | `ocr_status DEFAULT 'pending'` | |
| `ocr_processed_at` | `TIMESTAMPTZ` | |
| `ocr_locked_by` | `VARCHAR(64)` | worker ID |
| `ocr_locked_at` | `TIMESTAMPTZ` | pro zombie recovery |

**Storage:** `ALTER TABLE photos ALTER COLUMN image_bytes SET STORAGE EXTERNAL`.

Indexy: `ix_photos_run_id`, `ix_photos_captured_at DESC`, partial `ix_photos_pending WHERE ocr_status='pending'`.

### Tabulka `plate_detections`

Jedna detekce SPZ na fotce jedním OCR engine. Jedna fotka má 0..n detekcí na engine.

| Sloupec | Typ | Poznámka |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `photo_id` | `BIGINT FK photos.id ON DELETE CASCADE` | |
| `plate_text` | `VARCHAR(16)` | normalizovaný |
| `detection_confidence` | `REAL` | z YOLO |
| `text_confidence` | `REAL` | z fast-plate-ocr (může být null u Nomeroff fallback) |
| `bbox` | `JSONB` | `{"x1":..,"y1":..,"x2":..,"y2":..}` |
| `engine_name` | `VARCHAR(32) NOT NULL` | `yolo_v1m+fastplate` / `yolo_v1m+nomeroff` |
| `engine_version` | `VARCHAR(16)` | |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |

Unique constraint: `(photo_id, engine_name, plate_text)` — idempotentní re-run.
Indexy: `ix_det_photo_id`, `ix_det_plate_text`.

### Tabulka `spot_credentials`

Jen metadata; samotné heslo je v Windows Credential Locker.

| Sloupec | Typ | Poznámka |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `label` | `VARCHAR(64) UNIQUE NOT NULL` | např. `lab-robot` |
| `hostname` | `VARCHAR(64) NOT NULL` | |
| `username` | `VARCHAR(64) NOT NULL` | |
| `keyring_ref` | `VARCHAR(128) NOT NULL` | klíč v OS trezoru |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |
| `last_used_at` | `TIMESTAMPTZ` | |

---

## 3. API autonomy + ocr (signatury)

### Z `autonomy/app/` (přes `sys.path` injekci)

```python
# Session + robot control
from app.robot.sdk_session import SpotSession
# - SpotSession() -> instance
# - .connect(hostname: str, username: str, password: str) -> None  [blokuje]
# - .disconnect() -> None
# - .is_connected: bool (property)
# - .robot (property), .robot_command_client, .image_client, .lease_client,
#   .estop_client, .graph_nav_client, .recording_client, .robot_state_client

from app.robot.estop import EstopManager
# - EstopManager(session)
# - .start() -> None            # registrace endpointu + keep-alive thread
# - .trigger() -> None          # okamžitý cut motorů
# - .release() -> None
# - .shutdown() -> None         # zastaví keep-alive
# - .is_active: bool

from app.robot.lease import LeaseManager
# - LeaseManager(session)
# - .acquire() -> None
# - .release() -> None
# - .has_lease: bool

from app.robot.power import PowerManager
# - PowerManager(session)
# - .power_on() -> None         # blokující ~20s
# - .power_off() -> None
# - .stand() / .lower() / .sit() -> None

from app.robot.commands import MoveCommandManager, MoveCommandDispatcher
# - MoveCommandManager(session)
#   .send_velocity(vx, vy, vyaw, body_height=..., avoidance_*=...) -> None
#   .stop() -> None
# - MoveCommandDispatcher(manager)
#   .start() -> None             # spustí background thread
#   .send(vx, vy, vyaw) -> None  # non-blocking (queue)
#   .stop() -> None              # zastaví thread

from app.robot.images import ImagePoller
# - ImagePoller(session, status_callback=None)
# - .list_sources() -> list[str]
# - .capture(source: str) -> Optional[np.ndarray]     # BGR
# - .capture_and_save(source, path, also_rectified=False) -> bool
# - .capture_many(sources: list[str]) -> dict[str, np.ndarray]

from app.robot.health import HealthMonitor
# - HealthMonitor(session)
# - .get_battery_percentage() -> float
# - .get_robot_state_summary() -> dict

# GraphNav recording
from app.robot.graphnav_recording import GraphNavRecorder
# - GraphNavRecorder(session)
# - .start_recording(name_prefix=None, session_name=None) -> None
# - .create_waypoint(waypoint_name: str) -> str  (vrací waypoint_id)
# - .stop_recording() -> None
# - .download_map(save_dir: Path) -> None
# - .is_recording: bool (property)

# GraphNav navigation
from app.robot.graphnav_navigation import GraphNavNavigator, NavigationResult
# - GraphNavNavigator(session)
# - .upload_map(map_dir: Path) -> None
# - .get_waypoint_ids() -> list[str]
# - .localize(strategy, waypoint_id=None, fiducial_id=None) -> bool
# - .navigate_to(waypoint_id, timeout: float = 30.0) -> NavigationResult
# - .relocalize_nearest_fiducial() -> bool
# - .request_abort() -> None
# - NavigationResult: outcome (NavigationOutcome enum), message, .ok property

# UI reuse
from app.image_pipeline import ImagePipeline
# - ImagePipeline(session) (QThread)
# - .frame_ready signal -> QPixmap
# - .set_source(name), .set_recording(bool), .set_autonomous(bool), .set_overlay(text), .set_crosshair(bool)
# - .start() / .stop()

from app.ui.live_view_widget import LiveViewWidget
# - LiveViewWidget(parent)
# - .update_frame(pixmap: QPixmap) -> None
# - .clear() -> None

# Modely
from app.models import Checkpoint, MapMeta, RunMeta, LocalizationStrategy, NavigationOutcome
# LocalizationStrategy: FIDUCIAL_NEAREST | FIDUCIAL_INIT_NEAREST | NEAR_WAYPOINT | SPECIFIC_FIDUCIAL
# NavigationOutcome: REACHED | LOST | STUCK | NO_ROUTE | TIMEOUT | ABORTED | NOT_LOCALIZED | ROBOT_IMPAIRED | ERROR

# Store
from app.checkpoint_store import save_checkpoints, load_checkpoints, list_maps
```

### Additive moduly v `autonomy/app/robot/`

```python
from app.robot.fiducial_check import (
    FiducialObservation,   # dataclass(tag_id, distance_m, frame_name)
    visible_fiducials,     # (session, *, required_id=None, max_distance_m=2.0) -> list[FiducialObservation]
    is_fiducial_available, # bool wrapper
)

from app.robot.return_home import return_home
# return_home(navigator, start_waypoint_id, *, timeout_s=180, progress=None) -> NavigationResult

from app.robot.waypoint_namer import WaypointNameGenerator
# WaypointNameGenerator(waypoint_prefix="WP", checkpoint_prefix="CP")
# .next_waypoint() -> "WP_001"
# .next_checkpoint() -> "CP_001"
# .reset() / .waypoint_count / .checkpoint_count
```

### OCR projekt (subprocess only)

`ocr/ocrtest.py::CzechPlateRecognizer` není přímo importovatelný; voláme ho **jen ze subprocessu** (viz `spot_operator/ocr/fallback.py`). API:

```python
# Subprocess volání:
recognizer = CzechPlateRecognizer(yolo_model_path)
result = recognizer.process_image(image_path)
# -> list[dict]: {"plate": str, "bbox": [x1,y1,x2,y2], "is_fallback": bool}
# Nevrací confidence score.
```

---

## 4. Code samples — 9 kritických pasáží

### 4.1 `map_archiver.zip_map_dir` + `extract_map_archive`

Zipuje GraphNav adresář do bytes, ověřuje SHA-256 při extract. Čistá funkce bez DB.

```python
import hashlib
import io
import zipfile
from pathlib import Path


def zip_map_dir(map_dir: Path) -> tuple[bytes, str]:
    """Zipne map_dir rekurzivně, vrátí (bytes, sha256_hex)."""
    if not map_dir.is_dir():
        raise NotADirectoryError(f"Map dir does not exist: {map_dir}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        files = sorted(p for p in map_dir.rglob("*") if p.is_file())
        if not files:
            raise ValueError(f"Map dir is empty: {map_dir}")
        for path in files:
            zf.write(path, arcname=str(path.relative_to(map_dir)).replace("\\", "/"))
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def extract_map_archive(data: bytes, expected_sha256: str, target_dir: Path) -> Path:
    """Ověří SHA-256 a vyextrahuje ZIP do target_dir."""
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"Map archive corrupted: sha256 mismatch")
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            safe = Path(member)
            if safe.is_absolute() or ".." in safe.parts:
                raise ValueError(f"Suspicious ZIP member: {member}")
        zf.extractall(target_dir)
    return target_dir
```

### 4.2 `map_storage.map_extracted` context manager + `save_map_to_db`

Most mezi DB a GraphNav soubory. Playback používá context manager; cleanup je automatický.

```python
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MapMetadata:
    id: int
    name: str
    fiducial_id: int | None
    start_waypoint_id: str | None
    default_capture_sources: list[str]
    checkpoints_json: dict | None
    archive_size_bytes: int


def save_map_to_db(*, name, source_dir: Path, fiducial_id, start_waypoint_id,
                   default_capture_sources, checkpoints_json, checkpoints_count,
                   note=None, created_by_operator=None) -> int:
    """Zipne source_dir, spočítá SHA, uloží do DB. Vrátí map.id."""
    archive, sha = zip_map_dir(source_dir)
    with Session() as s:
        if maps_repo.exists_by_name(s, name):
            raise ValueError(f"Mapa '{name}' už existuje.")
        m = maps_repo.create(
            s, name=name, archive_bytes=archive, archive_sha256=sha,
            archive_size_bytes=len(archive), fiducial_id=fiducial_id,
            start_waypoint_id=start_waypoint_id,
            default_capture_sources=default_capture_sources,
            checkpoints_json=checkpoints_json,
            checkpoints_count=checkpoints_count,
            note=note, created_by_operator=created_by_operator,
        )
        s.commit()
        return m.id


@contextmanager
def map_extracted(map_id: int, temp_root: Path):
    """Extract mapy z DB + automatický cleanup.

    Usage:
        with map_extracted(map_id, TEMP_ROOT) as (map_dir, meta):
            navigator.upload_map(map_dir)
            ...
    """
    target = None
    try:
        with Session() as s:
            m = s.get(Map, map_id)
            if m is None:
                raise KeyError(f"Map {map_id} not found")
            target = temp_root / f"map_{map_id}_{uuid4().hex}"
            extract_map_archive(m.archive_bytes, m.archive_sha256, target)
            meta = _to_metadata(m)
        yield target, meta
    finally:
        if target and target.exists():
            shutil.rmtree(target, ignore_errors=True)
```

### 4.3 `OcrPipeline.process` — detector + reader v jednom

```python
import cv2
import numpy as np
import threading
from pathlib import Path


class OcrPipeline:
    def __init__(self, *, yolo_model_path: Path,
                 text_engine: str = "european-plates-mobile-vit-v2-model",
                 min_detection_confidence: float = 0.5):
        self._detector = YoloDetector(yolo_model_path, min_confidence=min_detection_confidence)
        self._reader = FastPlateReader(text_engine)
        self._lock = threading.Lock()

    def warmup(self) -> None:
        """Explicitně načte modely (~2 s)."""
        with self._lock:
            self._detector._ensure_loaded()
            self._reader._ensure_loaded()

    def process(self, image_bytes: bytes) -> list[Detection]:
        if not image_bytes:
            return []
        arr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            return []
        with self._lock:
            boxes = self._detector.detect(image)
            detections = []
            for bbox, det_conf in boxes:
                crop = image[bbox.y1:bbox.y2, bbox.x1:bbox.x2]
                if crop.size == 0:
                    continue
                text, text_conf = self._reader.read(crop)
                if not text:
                    continue
                detections.append(Detection(
                    plate=text, detection_confidence=det_conf,
                    text_confidence=text_conf, bbox=bbox,
                    engine_name="yolo_v1m+fastplate",
                ))
            return detections
```

### 4.4 `OcrWorker.run` loop — `SELECT FOR UPDATE SKIP LOCKED`

Background QThread s commit hygienou. Více workerů bezpečně paralelně.

```python
import time
from datetime import datetime, timezone
from PySide6.QtCore import QThread, Signal
from sqlalchemy import select, update


class OcrWorker(QThread):
    photo_processed = Signal(int, int)  # photo_id, počet detekcí
    photo_failed = Signal(int, str)

    def __init__(self, pipeline: OcrPipeline, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._stop = False
        self._worker_id = f"ocr-worker-{os.getpid()}"

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            self._pipeline.warmup()
        except Exception:
            pass
        self._sweep_zombies()

        while not self._stop:
            photo_id, image_bytes = None, b""
            # Claim transaction
            with Session() as s:
                photo = s.execute(
                    select(Photo).where(Photo.ocr_status == OcrStatus.pending)
                    .order_by(Photo.captured_at).limit(1)
                    .with_for_update(skip_locked=True)
                ).scalar_one_or_none()
                if photo is None:
                    s.rollback()
                    time.sleep(1.0)
                    continue
                photo.ocr_status = OcrStatus.processing
                photo.ocr_locked_by = self._worker_id
                photo.ocr_locked_at = datetime.now(timezone.utc)
                photo_id, image_bytes = photo.id, photo.image_bytes
                s.commit()

            # Process (outside transaction — může trvat sekundy)
            try:
                detections = self._pipeline.process(image_bytes)
                with Session() as s:
                    if detections:
                        s.add_all([
                            PlateDetection(**d.to_db_row(photo_id)) for d in detections
                        ])
                    s.execute(update(Photo).where(Photo.id == photo_id).values(
                        ocr_status=OcrStatus.done,
                        ocr_processed_at=datetime.now(timezone.utc),
                    ))
                    s.commit()
                self.photo_processed.emit(photo_id, len(detections))
            except Exception as exc:
                with Session() as s:
                    s.execute(update(Photo).where(Photo.id == photo_id).values(
                        ocr_status=OcrStatus.failed,
                    ))
                    s.commit()
                self.photo_failed.emit(photo_id, str(exc))
```

### 4.5 `visible_fiducials` — WorldObjectClient + distance

Additive modul v `autonomy/app/robot/fiducial_check.py`. Nezávisí na UI.

```python
import math
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class FiducialObservation:
    tag_id: int
    distance_m: float
    frame_name: str


def visible_fiducials(
    session: Any,
    *,
    required_id: Optional[int] = None,
    max_distance_m: float = 2.0,
) -> list[FiducialObservation]:
    """Vrátí fiducialy viditelné Spotem v dané vzdálenosti."""
    robot = getattr(session, "robot", None)
    if robot is None or not getattr(session, "is_connected", False):
        raise RuntimeError("Spot not connected.")

    from bosdyn.api import world_object_pb2
    from bosdyn.client.world_object import WorldObjectClient
    from bosdyn.client.frame_helpers import BODY_FRAME_NAME, get_a_tform_b

    wc = robot.ensure_client(WorldObjectClient.default_service_name)
    resp = wc.list_world_objects(object_type=[world_object_pb2.WORLD_OBJECT_APRILTAG])

    out = []
    for obj in resp.world_objects:
        props = obj.apriltag_properties
        tag_id = int(props.tag_id)
        if required_id is not None and tag_id != required_id:
            continue
        # Zkus frame names postupně
        for name in (props.frame_name_fiducial, f"fiducial_{tag_id}"):
            if not name:
                continue
            try:
                tf = get_a_tform_b(obj.transforms_snapshot, BODY_FRAME_NAME, name)
                dist = math.sqrt(tf.x**2 + tf.y**2 + tf.z**2)
            except Exception:
                continue
            if dist <= max_distance_m:
                out.append(FiducialObservation(tag_id, dist, name))
            break
    out.sort(key=lambda f: f.distance_m)
    return out
```

### 4.6 `return_home` — abort + relocalize + navigate_to

Additive modul v `autonomy/app/robot/return_home.py`. Spouští se v QThread z UI.

```python
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def return_home(
    navigator: Any,
    start_waypoint_id: str,
    *,
    timeout_s: float = 180.0,
    progress: Optional[Callable[[str], None]] = None,
) -> Any:
    """Přeruší běžící navigaci a vrátí Spota k start waypointu."""
    def emit(msg: str) -> None:
        logger.info(msg)
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    emit("Přerušuji běžící navigaci...")
    try:
        navigator.request_abort()
    except Exception as exc:
        logger.warning("request_abort failed: %s", exc)

    emit("Obnovuji lokalizaci nejbližším fiducialem...")
    try:
        navigator.relocalize_nearest_fiducial()
    except Exception as exc:
        logger.warning("relocalize failed: %s", exc)

    # Reset abort flag — jinak navigate_to vrátí ABORTED hned
    try:
        navigator._abort = False
    except Exception:
        pass

    emit(f"Navigate to {start_waypoint_id}...")
    result = navigator.navigate_to(start_waypoint_id, timeout=timeout_s)
    emit(f"Result: {result.outcome.name} — {result.message}")
    return result
```

### 4.7 `FastPlateReader._unpack_result` — tolerance 4 tvarů

`fast-plate-ocr` mezi verzemi mění tvar výstupu. Wrapper musí zvládnout všechno.

```python
from typing import Any


def _unpack_result(result: Any) -> tuple[str, float | None]:
    """Různé verze fast-plate-ocr vrací různé tvary:
    - (text, conf) tuple
    - {"plate": str, "confidence": float|list[float]} dict
    - list of (text, conf) batch
    - plain str
    """
    if result is None:
        return "", None

    if isinstance(result, tuple) and len(result) == 2:
        text, conf = result
        return _stringify(text), _floatify(conf)

    if isinstance(result, dict):
        text = result.get("plate") or result.get("text") or ""
        conf = result.get("confidence")
        return _stringify(text), _floatify(conf)

    if isinstance(result, list) and result:
        return _unpack_result(result[0])

    if isinstance(result, str):
        return result, None

    return "", None


def _floatify(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, list) and value:
        # Per-char conf → průměr
        try:
            return float(sum(value) / len(value))
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None
```

### 4.8 `RecordingService.stop_and_archive_to_db` — recording → ZIP → DB flow

```python
import shutil
import tempfile
from pathlib import Path


def stop_and_archive_to_db(
    self,
    *,
    map_name: str,
    note: str,
    operator_label: str | None,
    end_fiducial_id: int | None,
) -> int:
    """Zastaví recording, stáhne mapu, zipne ji, INSERT do DB, smaže temp.

    Returns: map.id v DB.
    """
    if not self._recorder.is_recording:
        raise RuntimeError("No recording in progress.")

    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="rec_", dir=str(TEMP_ROOT)))
    try:
        self._recorder.stop_recording()
        self._recorder.download_map(tmp_root)

        checkpoints_json = self._build_checkpoints_json(map_name)

        map_id = save_map_to_db(
            name=map_name,
            source_dir=tmp_root,
            fiducial_id=self._fiducial_id or end_fiducial_id,
            start_waypoint_id=self._start_waypoint_id,
            default_capture_sources=self._default_capture_sources,
            checkpoints_json=checkpoints_json,
            checkpoints_count=self.checkpoint_count,
            note=note or None,
            created_by_operator=operator_label or None,
        )
        return map_id
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
```

### 4.9 Alembic migrace — `STORAGE EXTERNAL` pro BYTEA

Proč: TOAST-komprese u už-komprimovaných dat (JPEG, ZIP) **plýtvá CPU** při každém read/write. `EXTERNAL` ji vypne.

```python
from alembic import op


def upgrade() -> None:
    # ... create_table(maps, photos, ...) ...

    # Vypni TOAST kompresi pro BYTEA sloupce obsahující již-komprimovaná data.
    # ZIP a JPEG mají entropii blízkou maximu — další deflate kompresi nezmenší,
    # jen zpomalí INSERT/SELECT.
    op.execute("ALTER TABLE maps ALTER COLUMN archive_bytes SET STORAGE EXTERNAL")
    op.execute("ALTER TABLE photos ALTER COLUMN image_bytes SET STORAGE EXTERNAL")


def downgrade() -> None:
    # Reset na default (EXTENDED — TOAST komprese + out-of-line).
    op.execute("ALTER TABLE photos ALTER COLUMN image_bytes SET STORAGE EXTENDED")
    op.execute("ALTER TABLE maps ALTER COLUMN archive_bytes SET STORAGE EXTENDED")
    # ... drop_table ...
```

---

## 5. Implementační pořadí

Postupuj po vrstvách. Po každé vrstvě ověř (`python -c "import ..."`, `pytest tests/unit/`).

1. **Layout** — adresáře, `launch.bat`, `setup_venv.bat`, `run_app.bat`, `.env.example`, `.gitignore`, `requirements.txt`. Ověř `setup_venv.bat` projde.
2. **Bootstrap** — `spot_operator/__init__.py`, `bootstrap.py`, `constants.py`, `config.py`, `logging_config.py`.
3. **DB vrstva** — `enums.py`, `models.py`, `engine.py`, `migrations.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, všechny repositories. Ověř migraci na čistou DB.
4. **Map archiver + storage** — `map_archiver.py`, `map_storage.py`. Pytest `test_map_archiver.py`.
5. **OCR pipeline** — `dtos.py`, `detector.py`, `reader.py`, `pipeline.py`, `fallback.py`. Test normalizace.
6. **OCR worker + photo sink** — `ocr_worker.py`, `photo_sink.py`. Manuální test: vlož blob do DB → worker ho zpracuje.
7. **Additive moduly v autonomy** — `fiducial_check.py`, `return_home.py`, `waypoint_namer.py`. Pytest `test_waypoint_namer.py`. **Ověř: `cd autonomy && launch.bat` stále funguje.**
8. **Robot wrappery** — `session_factory.py`, `dual_side_capture.py`.
9. **ZIP exporter + další services** — `zip_exporter.py`, `spot_wifi.py`, `credentials_service.py`, `recording_service.py`, `playback_service.py`.
10. **Base wizard + sdílené stránky** — `base_wizard.py`, `estop_floating.py`, `dialogs.py`, `workers.py`, `wifi_page.py`, `login_page.py`, `fiducial_page.py`.
11. **Recording wizard** (5 kroků) — `teleop_record_page.py`, `save_map_page.py`, `recording_wizard.py`. FiducialPage (krok 3) je sdílená s playbackem.
12. **Playback wizard** — `map_select_page.py`, `playback_run_page.py`, `playback_result_page.py`, `playback_wizard.py`.
13. **MainWindow + main.py** — `main_window.py`, `main.py`.
14. **CRUD modul** — `ui/crud/*`.
15. **README.md** (česky, 15 sekcí) + integration test skeleton.
16. **E2E test s reálným Spotem v labu** — scénář "nahrát mapu na PC-A, spustit playback z PC-B proti stejné DB".

---

## 6. Styl kódu

- **Python type hints** všude.
- **`from __future__ import annotations`** na začátku modulů (deferred evaluation).
- **Žádné zbytečné globální proměnné**.
- **Rozumné oddělení odpovědností** (db/services/ui/robot/ocr). Žádné cross-layer volání (UI nepíše SQL, services nedělají Qt — kromě `ocr_worker` a `playback_service`, kde je Qt signál nutný).
- **Názvy tříd a funkcí anglicky**; **uživatelské texty a README česky**; **log hlášky anglicky** (pro dev support) — konzistentně.
- **Docstringy** u veřejných tříd a netriviálních metod.
- **`@dataclass(frozen=True, slots=True)`** pro DTO (`MapMetadata`, `Detection`, `BoundingBox`, `WifiCheckResult`, `CheckpointRef`).
- **`pathlib.Path`** místo `os.path`.
- **`scoped_session(scopefunc=threading.get_ident)`** — per-thread DB session.
- **Dlouhé operace v QThread** (connect, capture, upload map, navigate, OCR pipeline). Nikdy neblokovat Qt main thread.
- **Chyby chytat nahoře** v UI kódu a zobrazovat přes `error_dialog`; knihovní vrstva vyhazuje, nepolyká.

---

## 7. Jak pinout verze závislostí

`requirements.txt` má po počátečním setupu volné ranges (`>=`). Pro reprodukovatelnost dalších instalací doporučuji **po prvním úspěšném setupu** zafixovat konkrétní verze.

### Postup prvního pinutí

1. Spustit `setup_venv.bat` na čisté mašině. Ověřit, že `python main.py --diag` projde a klíčové balíčky se načetly.
2. Vygenerovat lock soubor:

   ```bat
   .venv\Scripts\pip freeze > requirements.lock.txt
   ```

3. Z `requirements.lock.txt` vybrat **klíčové balíčky** (PySide6, SQLAlchemy, alembic, psycopg, bosdyn-*, ultralytics, onnxruntime, fast-plate-ocr, keyring, numpy, opencv-python, Pillow) a překlopit do `requirements.txt` s přesnou verzí (`==X.Y.Z`).
4. **Ostatní (tranzitivní) balíčky ponechat s `>=`** — pip resolver jejich verze odvodí z klíčových.
5. Smazat `.venv`, spustit `setup_venv.bat` znovu — musí projít beze změny.
6. Aktualizovat `CHANGELOG.md` (bump PATCH) a `last_updated` v hlavičce obou `instructions-*.md`.

### Upgrade verze v budoucnu

1. V novém venv zvedni jednu verzi (typicky MINOR) v `requirements.txt`.
2. `setup_venv.bat` + E2E (recording + playback s reálným Spotem nebo alespoň `python main.py --diag`).
3. Ověř, že `cd autonomy && launch.bat` pořád startuje autonomy.
4. Ověř, že `cd ocr && python ocrtest.py` pořád běží.
5. Aktualizovat `CHANGELOG.md` (bump MINOR).
6. Aktualizovat `last_updated` a `applies_to` v hlavičce `instructions-*.md`.

### Co nepinout

- Tranzitivní balíčky, které pip resolver umí odvodit.
- `pytest` a jeho pluginy — tam stačí major pin (`pytest>=8.0,<9.0`).

### Co pinout vždy přesně

- **bosdyn-*** — SDK verze musí sedět, jinak protokol konflikt s robotem.
- **numpy** — kvůli ABI s opencv a torch.
- **fast-plate-ocr + onnxruntime** — model formát se mezi verzemi mění.
