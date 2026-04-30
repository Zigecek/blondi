# Blondi — technická prezentace projektu

> **Robotická detekce SPZ na parkovišti.** Desktopová aplikace nad robotem
> Boston Dynamics **Spot**, která sjednocuje teleop, GraphNav mapování, autonomní
> průjezd a real-time OCR rozpoznávání českých registračních značek do
> jediného nástroje pro neтехnického operátora.

---

## 1. Co projekt řeší

Operátor parkoviště potřebuje robota, který:

1. Si nechá **nahrát mapu** pomocí klávesnice (WASD teleop přímo z PC).
2. Pak na povel **autonomně projede** uložené checkpointy.
3. Na každém checkpointu **vyfotí auto** (z levé / pravé / obou bočních kamer).
4. Ze snímků **automaticky rozpozná SPZ** přes YOLO + OCR pipeline.
5. Vše zapíše do PostgreSQL pro pozdější export / audit.

Projekt je tedy **proof-of-concept robotický systém SPZ kontroly** se třemi
tvářemi: knihovna pro Spot (autonomy), knihovna pro OCR (ocr) a sjednocená
operátorská aplikace (blondi).

---

## 2. Architektura na první pohled

```
┌─────────────────────────────────────────────────────────────────┐
│                    Blondi (PySide6 GUI)                  │
│   MainWindow ──► RecordingWizard ──► Mapa do PostgreSQL         │
│             └──► PlaybackWizard  ──► Run + fotky + OCR          │
│             └──► WalkWizard      ──► volná chůze                │
│             └──► CRUD okno       ──► SPZ registr / re-OCR       │
└────────────┬───────────────────────────────────┬────────────────┘
             │ python imports                     │ SQLAlchemy 2.0
             ▼                                    ▼
   ┌──────────────────────┐             ┌─────────────────────┐
   │  autonomy/ (submodul)│             │  PostgreSQL 14+     │
   │  Spot SDK 5.1.4      │             │  - maps (BYTEA ZIP) │
   │  GraphNav rec/play   │             │  - photos (BYTEA)   │
   │  Teleop, E-Stop      │             │  - plate_detections │
   │  AprilTag fiducial   │             │  - spot_runs        │
   └──────────────────────┘             │  - license_plates   │
             │                          │  - spot_credentials │
             │ TCP/RPC over Wi-Fi       └─────────────────────┘
             ▼                                    ▲
   ┌──────────────────────┐                       │
   │   Boston Dynamics    │                       │
   │   Spot robot         │                       │ async OCR worker
   │   192.168.80.3       │                       │ (QThread)
   └──────────────────────┘             ┌─────────┴──────────┐
                                        │ ocr/ (knihovna)    │
                                        │ YOLOv8 + fast-plate│
                                        │ Nomeroff fallback  │
                                        └────────────────────┘
```

**Princip „tří krabic":**

| Krabice           | Co je                                                 | Samostatně spustitelné? |
|-------------------|-------------------------------------------------------|-------------------------|
| `autonomy/`       | git submodul — kompletní Spot desktop app             | **Ano** (`launch.bat`)  |
| `ocr/`            | YOLO + Nomeroff CLI script + natrénovaný model        | **Ano** (`ocrtest.py`)  |
| `blondi/`  | nadřazená app která je obě používá jako knihovny      | **Ano** (hlavní app)    |

Sjednocená aplikace nikdy **neduplikuje logiku**, jen ji volá. `autonomy/` se
přidává na `sys.path` přes `blondi/bootstrap.py` a importuje se jako
balíček `app.*`.

---

## 3. Použité technologie

### 3.1 Jazyk a runtime

| Technologie | Verze | Proč |
|---|---|---|
| **Python** | **3.10.20** (x64) | Spot SDK podporuje pouze 3.7–3.10 |
| **venv** | std-lib | Izolace závislostí, deterministický build |
| **Windows 10/11** | — | Cílová platforma operátora; Credential Locker |

### 3.2 GUI vrstva

| Knihovna | Verze | Použití |
|---|---|---|
| **PySide6** | 6.7.3 | Qt6 desktop GUI (oficiální Qt for Python) |
| Qt **QWizard** | součást Qt6 | step-by-step flow pro nahrávání i playback |
| **QThread / QTimer** | součást Qt6 | non-blocking IO + OCR worker + DB ping |
| **QShortcut** | součást Qt6 | F1 shortcut na E-Stop (window-scoped) |
| **QLockFile** | součást Qt6 | Single-instance lock per Windows uživatel |

### 3.3 Databáze

| Knihovna | Verze | Použití |
|---|---|---|
| **PostgreSQL** | 14+ | Storage pro mapy, fotky, detekce, registr SPZ |
| **SQLAlchemy** | 2.0.49 | Modern Declarative + Mapped[] ORM |
| **Alembic** | 1.18.4 | DB migrace (auto-run při startu) |
| **psycopg** (binary) | 3.3.3 | PostgreSQL driver (psycopg3, ne dvojka) |
| `BYTEA` + `STORAGE EXTERNAL` | — | Mapy a JPEGy přímo v DB (TOAST optim.) |
| **JSONB** | — | `checkpoints_json`, `bbox`, `default_capture_sources` |
| Native PG **ENUMs** | — | `plate_status`, `run_status`, `ocr_status` |
| `FOR UPDATE SKIP LOCKED` | — | Bezpečné claimování fotek pro OCR worker |

### 3.4 Boston Dynamics Spot SDK

| Balíček | Verze | Co dělá |
|---|---|---|
| **bosdyn-client** | 5.1.4 | RPC klient (Lease, E-Stop, RobotCommand, Image, GraphNav, Recording, RobotState, WorldObject) |
| **bosdyn-core** | 5.1.4 | Společné protobuf typy |
| **bosdyn-api** | 5.1.4 | Generated protobuf API |
| **bosdyn-mission** | 5.1.4 | Mission service (reservováno pro budoucí) |

Klíčové SDK abstrakce, které projekt používá:

- `EstopEndpoint` + `EstopKeepAlive` — hardware emergency stop s timeout 9 s
- `LeaseClient` + `LeaseKeepAlive` — exclusive ownership robotí controll
- `GraphNavClient` — `set_localization`, `navigate_to`, `upload_graph`, `download_graph`
- `GraphNavRecordingServiceClient` — `start_recording`, `create_waypoint`, `stop_recording`
- `ImageClient` — fisheye + composite kamery (`frontleft_fisheye_image`, atd.)
- `WorldObjectClient` — detekce **AprilTag fiducialů** s `apriltag_properties.tag_id`
- `RobotCommandBuilder.synchro_velocity_command` — WASD teleop
- `RobotStateClient` — battery, fault state, motor power
- `frame_helpers.get_a_tform_b` — body→fiducial transformace

### 3.5 Computer vision a OCR

| Knihovna | Verze | Použití |
|---|---|---|
| **OpenCV** (opencv-python) | 4.11.0.86 | Dekódování JPEG, color-space convert, crop |
| **NumPy** | 1.26.4 | Tensor data exchange |
| **Pillow** | 12.2.0 | JPEG encode (BGR → RGB → JPEG) |
| **Ultralytics YOLO** | 8.4.41 | YOLOv8 detekce SPZ (custom fine-tune) |
| **fast-plate-ocr** | 1.1.0 | ONNX-based reader, model `european-plates-mobile-vit-v2-model` |
| **onnxruntime** | 1.23.2 | Backend pro fast-plate-ocr (žádná torch ↔ bosdyn protobuf kolize) |
| **nomeroff_net** | 4.0.1 | Fallback OCR engine — torch-based, izolovaný v subprocesu |

**Vlastní natrénovaný model** je `ocr/license-plate-finetune-v1m.pt` (~40 MB,
YOLOv8-medium fine-tunutý na české SPZ).

### 3.6 Bezpečnost a auth

| Knihovna | Verze | Použití |
|---|---|---|
| **keyring** | 25.7.0 | Windows Credential Locker (Spot heslo + DB heslo) |
| **python-dotenv** | 1.2.2 | `.env` načítání (s `override=False` — env shellu má přednost) |

### 3.7 Distribuce a testy

| Knihovna | Verze | Použití |
|---|---|---|
| **PyInstaller** | 6.19.0 | Frozen Windows EXE build |
| **pytest** | 9.0.3 | Test framework |
| **pytest-postgresql** | 8.0.0 | Ephemerní PG instance pro integration testy |
| **pytest-qt** | 4.5.0 | Qt event-loop testy |

---

## 4. Strukturní přehled repozitáře

```
c:\Users\user\spot\
├── main.py                        # entry point — 10-step bootstrap
├── launch.bat / setup_venv.bat    # Windows wrappery
├── requirements.txt               # pinned verze (snapshot 2026-04-22)
├── .env / .env.example            # DATABASE_URL, SPOT_DEFAULT_IP, atd.
├── alembic.ini + alembic/         # 3 migrace, auto-run při startu
│
├── blondi/                 # ─── HLAVNÍ BALÍČEK ───────────────
│   ├── bootstrap.py               # sys.path injection pro autonomy/ocr
│   ├── config.py                  # AppConfig (frozen dataclass)
│   ├── constants.py               # camera names, OCR engine IDs, regex
│   ├── logging_config.py          # rotující file + console + Qt handler
│   │
│   ├── db/                        # SQLAlchemy 2.0
│   │   ├── models.py              # 6 entit (LicensePlate, Map, ... )
│   │   ├── enums.py               # 4 enumy mapované na PG ENUM
│   │   ├── engine.py              # scoped_session per thread
│   │   ├── migrations.py          # programatic alembic upgrade head
│   │   └── repositories/          # plates, maps, runs, photos, detections
│   │
│   ├── robot/                     # tenké wrappery nad autonomy
│   │   ├── session_factory.py     # SpotBundle (session+estop+lease+power)
│   │   ├── localize_strict.py     # přísná lokalizace s SPECIFIC_FIDUCIAL
│   │   ├── power_state.py         # power-on detection + retry
│   │   ├── dual_side_capture.py   # capture multi-source v jednom volání
│   │   └── graphnav_fiducial.py   # extrakce observed fiducial IDs z mapy
│   │
│   ├── ocr/                       # OCR pipeline
│   │   ├── pipeline.py            # YoloDetector + FastPlateReader v jednom
│   │   ├── detector.py            # ultralytics YOLO wrapper
│   │   ├── reader.py              # fast-plate-ocr wrapper, normalize
│   │   ├── fallback.py            # nomeroff_net subprocess izolace
│   │   └── dtos.py                # BoundingBox, Detection (frozen)
│   │
│   ├── services/                  # business logika (žádné Qt importy)
│   │   ├── recording_service.py   # GraphNav recording + WP/CP creation
│   │   ├── playback_service.py    # 1100+ řádků orchestrace runu
│   │   ├── map_storage.py         # save/load/extract ZIP <-> DB
│   │   ├── map_archiver.py        # zip/unzip + SHA256 + validate
│   │   ├── photo_sink.py          # encode + insert do photos
│   │   ├── ocr_worker.py          # QThread, FOR UPDATE SKIP LOCKED
│   │   ├── credentials_service.py # keyring + DB metadata transakčně
│   │   ├── spot_wifi.py           # ping + TCP connect (locale-indep.)
│   │   ├── zip_exporter.py        # run -> ZIP s photos/ + run.json
│   │   └── contracts.py           # MapPlan, CheckpointResult DTOs
│   │
│   └── ui/                        # PySide6 vrstva
│       ├── main_window.py         # launcher + DB ping + status
│       ├── common/                # ConnectDialog, EstopFloating, dialogy
│       ├── wizards/               # 3 QWizard a 9 stránek
│       │   ├── base_wizard.py     # F1 shortcut, safe_abort, close-guard
│       │   ├── recording_wizard.py        # 4 kroky
│       │   ├── playback_wizard.py         # 5 kroků
│       │   ├── walk_wizard.py             # 1 krok
│       │   └── pages/             # connect, fiducial, teleop_record, ...
│       └── crud/                  # ★ ODSTRANITELNÝ DEV NÁSTROJ
│           ├── crud_window.py     # 3 záložky (SPZ, Běhy, Fotky)
│           ├── spz_tab.py / spz_detail_dialog.py
│           ├── runs_tab.py / run_detail_dialog.py
│           └── photos_tab.py / photo_detail_dialog.py  # re-OCR Nomeroffem
│
├── autonomy/                       # ─── GIT SUBMODUL ────────────────
│   ├── main.py                    # samostatná Spot Desktop App
│   ├── instructions.md            # 750-řádkový spec / pravidla AI agenta
│   └── app/
│       ├── robot/                 # SDK volání (sdk_session, estop, ...)
│       │   ├── sdk_session.py     # connect + auth + time-sync
│       │   ├── estop.py           # EstopEndpoint + KeepAlive
│       │   ├── lease.py           # LeaseKeepAlive
│       │   ├── commands.py        # synchro_velocity_command, mobility
│       │   ├── images.py          # ImagePoller + FrontComposite stitch
│       │   ├── graphnav_recording.py  # start/stop/download mapy
│       │   ├── graphnav_navigation.py # upload/localize/navigate_to
│       │   ├── fiducial_check.py  # AprilTag visibility (additivní)
│       │   ├── return_home.py     # autonomní návrat k start waypointu
│       │   └── waypoint_namer.py  # WP_001 / CP_001 generátor
│       └── ui/                    # vlastní QMainWindow (zachováno)
│
├── ocr/                            # ─── KNIHOVNA ────────────────────
│   ├── ocrtest.py                 # CzechPlateRecognizer (Nomeroff CLI)
│   ├── license-plate-finetune-v1m.pt   # ★ vlastní YOLO model 40 MB
│   └── anpr_ocr_eu_2-cpu.pb       # alternativní TF model (zatím nepoužit)
│
├── tests/
│   ├── unit/                      # 17 testů (no Spot, no PG)
│   └── integration/               # smoke testy s pytest-postgresql
└── logs/ / temp/                   # rotace logů, dočasné map extrakce
```

---

## 5. DB schéma (PostgreSQL)

6 tabulek, 4 nativní ENUMy, BYTEA + JSONB hybrid:

```
┌─────────────────────┐        ┌──────────────────────┐
│ license_plates      │        │ maps                 │
│---------------------│        │----------------------│
│ id  PK              │        │ id  PK               │
│ plate_text  UQ      │        │ name  UQ             │
│ valid_until         │        │ archive_bytes BYTEA  │ ← celá GraphNav
│ status  ENUM        │        │ archive_sha256       │   mapa zazipovaná
│ note                │        │ fiducial_id          │
│ created/updated_at  │        │ start_waypoint_id    │
└─────────────────────┘        │ default_capture_src  │ JSONB
                               │ checkpoints_json     │ JSONB schema_v=2
                               │ archive_is_valid     │
                               │ waypoints_count      │
                               └──────────┬───────────┘
                                          │ FK SET NULL
                                          ▼
┌──────────────────────┐        ┌──────────────────────┐
│ spot_credentials     │        │ spot_runs            │
│----------------------│        │----------------------│
│ id  PK               │        │ id  PK               │
│ label  UQ            │        │ run_code  UQ         │
│ hostname             │        │ map_id  FK           │
│ username             │        │ map_name_snapshot    │
│ keyring_ref          │ ★      │ start/end_time       │
│   = keyring lookup   │        │ status  ENUM         │
│   key, NE heslo      │        │ checkpoints_reached  │
└──────────────────────┘        │ checkpoint_results   │ JSONB
                                │ return_home_status   │
                                │ abort_reason         │
                                └──────────┬───────────┘
                                           │ FK CASCADE
                                           ▼
┌──────────────────────┐        ┌──────────────────────┐
│ plate_detections     │ ◄──────│ photos               │
│----------------------│   FK   │----------------------│
│ id  PK               │ CASC.  │ id  PK               │
│ photo_id  FK         │        │ run_id  FK           │
│ plate_text  IDX      │        │ checkpoint_name      │
│ detection_confidence │        │ camera_source        │
│ text_confidence      │        │ image_bytes BYTEA    │ ← JPEG ~80 KB
│ bbox  JSONB          │        │ width, height        │
│ engine_name          │        │ ocr_status  ENUM     │ ← state machine
│ engine_version       │        │ ocr_locked_by/_at    │ ← worker claim
│ created_at           │        │ ocr_processed_at     │
│ UNIQUE(photo,        │        │ partial INDEX        │ WHERE pending
│   engine, plate)     │        │   ix_photos_pending  │
└──────────────────────┘        └──────────────────────┘
```

**Klíčové designové rozhodnutí: mapy a fotky v DB**

- ZIP mapy **přímo v `BYTEA`** + SHA-256 hash + `STORAGE EXTERNAL` (PG TOAST,
  bez kompresního overheadu).
- Důsledek: spuštění playbacku **z libovolného PC** — stačí stejná DB.
- Nevýhoda: pro >100 MB mapy je doporučen externí storage (známé omezení).

**OCR worker state machine:**

```
pending ──claim──► processing ──ok──► done
                       │
                       ├──fail──► failed
                       │
                       └──zombie (>5 min)──► pending  (sweep_zombies_now)
```

Worker používá `SELECT ... FOR UPDATE SKIP LOCKED` pro bezpečné claimování,
heartbeat thread každých 60 s obnovuje `ocr_locked_at`, periodický sweep
recovery zombie záznamů (např. po pádu workera).

---

## 6. Hlavní user-facing flow

### 6.1 Nahrávání nové mapy (RecordingWizard)

```
1. Připojit se ke Spotovi (ConnectDialog v MainWindow nebo wizardem)
   │     - Wi-Fi check (ping + TCP 443)
   │     - Login s keyring (uložené profily)
   │     - SDK session, time-sync, E-Stop endpoint, lease acquire
   │
2. Fiducial Page (sdílená napříč 3 wizardy)
   │     - Live view z přední kamery
   │     - Power on, Stand
   │     - WASD teleop přivede Spota k AprilTagu
   │     - WorldObjectClient ověří viditelnost (max 2 m)
   │
3. Teleop + Recording Page
   │     - GraphNav recording běží od vstupu na stránku
   │     - Klávesy:
   │         W/A/S/D, Q/E      = pohyb
   │         V / N / B          = foto vlevo / vpravo / obě strany
   │         C                  = waypoint bez fotky
   │         Space              = soft stop
   │         F1                 = HARDWARE E-STOP
   │     - Photo confirm overlay = preview + ✓ uložit / ✗ zrušit
   │     - Recording service drží sirotčí RecordedCheckpoint dataclassy
   │
4. Save Map Page
        - Re-check fiducial
        - Validate map name (regex ^[A-Za-z0-9_-]{3,40}$)
        - 2-fázový save:
            Fáze 1: stop_and_export → temp/rec_*.zip + RecordingSnapshot
            Fáze 2: save_snapshot_to_db (idempotent retry-safe)
        - Auto-extract observed fiducial IDs ze snapshot protobufů
          (KRITICKÉ pro playback SPECIFIC_FIDUCIAL localize)
```

### 6.2 Autonomní průjezd (PlaybackWizard)

```
1. ConnectPage (skip pokud bundle už existuje v MainWindow)
2. MapSelectPage   - tabulka map z DB s metadaty + náhledem
3. FiducialPage    - musí vidět TÝŽ fiducial co je v `maps.fiducial_id`
4. PlaybackRunPage - hlavní obrazovka:
        ┌──────────────────────────┬──────────────────┐
        │ Live view                │ Progress         │
        │ (frontleft_fisheye)      │ checkpoint 3/12  │
        │                          ├──────────────────┤
        │                          │ Log událostí     │
        │                          │ (PlaybackService │
        │                          │  signály)        │
        ├──────────────────────────┴──────────────────┤
        │ [F1] HARDWARE E-STOP                        │
        │ [STOP s návratem domů]                      │
        └─────────────────────────────────────────────┘

   Behind the scenes (PlaybackService.run):
     1) load_map_to_temp(map_id, temp/)
     2) navigator.upload_graph + waypoint_snapshots
     3) navigator.localize(SPECIFIC_FIDUCIAL, fiducial_id)
     4) for each checkpoint:
          - navigate_to(waypoint_id, timeout=60s)
          - on STUCK/TIMEOUT/NO_ROUTE → emit obstacle_detected(),
            UI nabízí "pokračovat / zrušit"
          - dual_side_capture → photo_sink.save_photo_to_db
            (ocr_status='pending')
          - OCR worker (běží paralelně) ji vyzvedne FOR UPDATE SKIP LOCKED
     5) status = completed | aborted | failed | partial
     6) Volitelně Return Home (návrat k start_waypoint)

5. PlaybackResultPage  - tabulka přečtených SPZ + tlačítko Stáhnout ZIP
```

### 6.3 Volná chůze (WalkWizard)

Jediný krok — `FiducialPage` v režimu „bez požadovaného ID". Užitečné pro
zaškolení nových operátorů, kteří chtějí vyzkoušet teleop a ověřit Wi-Fi
spojení bez závazku nahrávat mapu.

### 6.4 CRUD okno (volitelný DEV nástroj)

3 záložky:

- **SPZ** — registr povolených / zakázaných značek (CRUD nad `license_plates`)
- **Běhy** — historie runs s přechodem na detail + ZIP export
- **Fotky** — galerie všech fotek s detail dialogem:
  - Bbox overlay přes všechny enginy
  - **Re-OCR Nomeroffem** (subprocess, kvůli torch/protobuf izolaci)

**Klíčové:** celá složka `blondi/ui/crud/` je opatřena
`try: from blondi.ui.crud import …` — když ji v produkčním buildu
smažeš, tlačítko v MainWindow se prostě skryje. Žádné refactory potřeba.

---

## 7. Bezpečnost — E-Stop a další ochrany

### 7.1 Vícevrstvý stop model

| Vrstva | Trigger | Co se stane | Recovery |
|---|---|---|---|
| **Soft stop** | Space (jen v teleop) | Přestaneme posílat velocity command | Stiskni W znovu |
| **Stop s návratem** | žluté tlačítko v playbacku | request_abort + autonomní return_home | běží dál |
| **Hardware E-Stop** | **F1** kdykoliv / červený floating widget | `EstopEndpoint.stop()` — motory se okamžitě odpojí | Release → Power On → Stand |
| **OS kill** | Ctrl+C / X | aboutToQuit → bundle.disconnect (timeout per-step 3 s) | restart aplikace |

### 7.2 E-Stop floating widget

`blondi/ui/common/estop_floating.py` — semi-transparentní červené
tlačítko, vždy ve spodním rohu aktivních robot-stránek. F1 deleguje na něj
přes `trigger_from_shortcut()` aby zachoval stav (triggered ↔ released).

### 7.3 Single-instance lock

`%LOCALAPPDATA%/blondi/blondi_<user>.lock` (`QLockFile`,
stale-time 10 s) — druhá instance v tomtéž Windows účtu raise. Druhý
Windows uživatel má vlastní lock (per-user safe).

### 7.4 Lease + time-sync správnost

`SpotSession.connect` provede ve fixním pořadí:

1. `create_robot(hostname)`
2. `authenticate(user, password)`
3. `time_sync.wait_for_sync()` — bez tohoto by RPC selhávaly s clock-skew
4. `_sync_directory_with_retry()` — některé firmware advertisí služby
   pod jinými jmény, fallback přes `service_type`
5. `ensure_client(...)` na Estop, Lease, RobotCommand, Image, RobotState,
   GraphNav, Recording (poslední dvě non-critical kvůli licenci)

### 7.5 Validace na hraně

- Wi-Fi check je **locale-independent** — N×single ping s `returncode == 0`
  místo parsování textu (CZ Windows vrací „Přijato = N").
- TCP/443 je autoritativní (Spot může blokovat ICMP firewallem).
- `archive_sha256` se ověřuje při každém extract z DB.
- Map metadata mají `schema_version` (aktuálně v2) — forward compat warning.
- `validate_plan_invariants` raise při duplikátních waypoint_ids,
  chybějícím start_waypoint_id, prázdném plánu.

---

## 8. OCR pipeline detailně

### 8.1 Dvouvrstvý detect-then-read přístup

```
JPEG bytes (z DB)
    │ cv2.imdecode
    ▼
BGR ndarray (H, W, 3)
    │ YoloDetector.detect (ultralytics YOLO)
    ▼
List[(BoundingBox, det_confidence)]    ← min_confidence 0.5 (env tunable)
    │ pro každý box:
    │     image[y1:y2, x1:x2] crop
    │     cv2.cvtColor BGR2GRAY
    │ FastPlateReader.read (onnxruntime)
    ▼
(text_raw, text_confidence)
    │ _normalize_plate:
    │     uppercase + alphanumeric only
    │     check ^[A-Z0-9]{1,16}$ regex
    │     reject pokud neprošla
    ▼
List[Detection(plate, det_conf, text_conf, bbox, engine_name, engine_version)]
    │
    ▼
INSERT plate_detections (UNIQUE photo_id+engine+plate)
UPDATE photos.ocr_status = 'done'
```

### 8.2 Engine identifikátory

Každá detekce má `engine_name`, takže pro stejnou fotku můžeš mít:

- `yolo_v1m+fastplate` — primární pipeline (rychlá, ONNX)
- `yolo_v1m+nomeroff` — fallback z CRUD okna (přesnější, pomalá, torch)

Re-OCR z CRUD detail dialogu otevře nomeroff **v subprocesu** (`fallback.py`),
protože:

- `nomeroff_net` táhne torch + jeho protobuf, který koliduje s `bosdyn.api`
  v hlavním procesu.
- Subprocess izoluje crash při OCR runtime erroru — hlavní app nespadne.

### 8.3 Async worker pattern

`OcrWorker` je **QThread** se striktním lifecyklem:

```python
on app start:
    pipeline = create_default_pipeline(config)    # YOLO + fast-plate
    worker = OcrWorker(pipeline)
    worker.start()  # warmup -> sweep_zombies -> infinite loop

worker.run() loop:
    photo = claim_next_pending(SELECT FOR UPDATE SKIP LOCKED + UPDATE)
    if photo is None: sleep(1s)
    else:
        spawn heartbeat_thread (UPDATE ocr_locked_at every 60s)
        try:
            detections = pipeline.process(photo.image_bytes)
            store_results(photo_id, detections)  # mark_done
            emit photo_processed(photo_id, len(detections))
        except PermanentOcrError:  # FileNotFoundError, ModuleNotFoundError
            emit worker_disabled(reason)  # UI ukáže status
            return  # END worker

on app shutdown:
    worker.request_stop()
    worker.wait(30000)  # 30 s — YOLO warmup může 5-10 s na slabším CPU
```

**Robustnost:**

- Zombie sweep při startu + každých 10 min
- Exponential backoff při DB chybách (2s → 4s → 8s → max 60s)
- Dedup logování (Wi-Fi switch ztratí DNS pro DB, log se nezahltí)
- Oddělené sessiony pro claim / write / mark_failed (FOR UPDATE SKIP LOCKED
  semantika)

---

## 9. Robotická stránka — co Spot reálně dělá

### 9.1 Teleop velocity loop

Klávesa W stisknutá → `KeyboardController` v Qt event filteru → každých
~150 ms `MoveCommandManager.send_velocity(vx, vy, vyaw)` →
`RobotCommandBuilder.synchro_velocity_command(...)` →
`robot_command_client.robot_command(...)` po Wi-Fi.

`MobilityParams`:

- `body_height`, `footprint_R_body` (yaw/roll/pitch z UI sliderů)
- `obstacle_avoidance_padding` = `strength/100 * 0.35 m`
  - WASD recording: `strength=10` → padding 0.035 m (agresivní, projeď úzké)
  - Playback: `strength=20` → padding 0.070 m (konzervativní)
- `disable_vision_*_obstacle_avoidance` flags z `AvoidanceSettings`
- `hazard_detection_mode` — off / on / cost
- `disable_nearmap_cliff_avoidance`, `disable_vision_negative_obstacles`

### 9.2 GraphNav recording / playback

Recording session na robotovi:

```python
client.start_recording(RecordingEnvironment(
    name_prefix="parkoviste_sever",
    waypoint_environment=Annotations(client_metadata=ClientMetadata(
        session_name="blondi_parkoviste_sever",
        client_software_version="spot-desktop-app",
        client_username=os.getlogin(),
        client_id=socket.gethostname(),
    )),
))
# ... operátor jezdí ...
client.create_waypoint("CP_001")  # vrátí waypoint_id (UUID)
# ... další pohyb ...
client.create_waypoint("WP_002")
# ...
client.stop_recording()  # retry na NotReadyYetError do 20 s
client.download_graph()         # → graph proto
client.download_waypoint_snapshot(snap_id)  # každý zvlášť
client.download_edge_snapshot(snap_id)      # každý zvlášť
```

Stažená mapa = adresář `graph/`, `waypoint_snapshots/`, `edge_snapshots/` →
zip → BYTEA do PG.

Playback opačně: BYTEA → unzip do `temp/map_<id>_<uuid>/` →
`upload_graph` + `upload_waypoint_snapshot` × N + `upload_edge_snapshot` × N
→ `set_localization(SPECIFIC_FIDUCIAL, use_fiducial_id=meta.fiducial_id)`
→ smyčka `navigate_to(waypoint_id, timeout=60)` → `navigation_feedback` poll
do terminálního statusu (REACHED / STUCK / LOST / NO_ROUTE / TIMEOUT / ...).

### 9.3 Workaround na bosdyn 4.1 streaming bug

`safe_download_graph()` dočasně přepne `_use_streaming_graph_upload = False`,
protože SDK 4.1 odkazuje na funkci `_get_streamed_download_graph` která
v balíčku **neexistuje** — přepnutí na non-streaming download je oprava.

### 9.4 Fiducial-anchored lokalizace

Při recordingu se z `WaypointSnapshot` protobufů čtou pozorované AprilTag IDs
(`apriltag_properties.tag_id`) → `read_observed_fiducial_ids(map_dir)`.

Priorita volby `effective_fiducial_id`:

1. UI fiducial (z fiducial-page) **pokud je v observed_list** ← user fyzicky
   ověřil + robot ho má v mapě
2. End fiducial (z save-page re-check) pokud je v observed_list
3. První observed (sortvaný)
4. Fallback na UI hodnotu (i když není v mapě)

Bez tohoto postupu by playback byl náchylný k mis-lokalizaci.

---

## 10. Provozní vlastnosti

### 10.1 Bootstrap (10 kroků v main.py)

```
1. inject_paths()         autonomy/ + ocr/ na sys.path
2. AppConfig.load_from_env()
3. setup logging (file rotující + console + Qt handler)
4. Python verze guard (musí být 3.10)
5. Single-instance QLockFile
6. Alembic upgrade head (migrace)
7. init_engine + DB ping
8. cleanup_temp_root (smaž zaseklé temp/map_* z předchozího běhu)
9. QApplication + OCR worker.start()
10. MainWindow.showMaximized() + app.exec()
```

### 10.2 Logging

- File: `logs/blondi.log` — rotující po 10 MB × 5 záloh
- Console: stdout
- Qt handler: zachycuje Qt warnings (otherwise tichá smrt)
- `--diag` CLI flag → vypíše balíčky a verze a skončí

### 10.3 Konfigurace přes `.env`

```env
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db
DATABASE_URL_TEMPLATE=...{password}...   # alt: heslo z keyringu
SPOT_DEFAULT_IP=192.168.80.3
SPOT_TIMEOUT_SECONDS=15
FIDUCIAL_DISTANCE_THRESHOLD_M=2.0
OCR_YOLO_MODEL=ocr/license-plate-finetune-v1m.pt
OCR_TEXT_ENGINE=european-plates-mobile-vit-v2-model
OCR_DETECTION_MIN_CONFIDENCE=0.5
KEYRING_SERVICE=blondi.spot
OPERATOR_LABEL=
LOG_LEVEL=INFO
```

`AppConfig` je `@dataclass(frozen=True, slots=True)` s validací rozsahů
(min/max pro float env vars, povolené log levels).

### 10.4 Alembic migrace

3 revize aktuálně:

- `0001_initial` — všechny tabulky, ENUMy, indexy, partial index na pending
- `0002_reliability_fields` — `ocr_locked_*`, `archive_is_valid`, …
- `0003_schema_cleanup` — drobné konsolidace

Volá se **programaticky** při startu (`upgrade_to_head(database_url)`),
takže operátor nemusí ručně pouštět `alembic upgrade head`.

---

## 11. Co všechno v aplikaci jde

| Funkce | Kde |
|---|---|
| Připojit se k libovolnému Spotovi v síti | MainWindow → Připojit se ke Spotovi |
| Uložit Spot profil do Windows Credential Locker | ConnectDialog → Zapamatovat |
| Volně chodit se Spotem klávesnicí (WASD) | Walk wizard |
| Nahrát mapu parkoviště | Recording wizard |
| Pojmenovaně přidat waypoint i checkpoint | Klávesa C (waypoint) / V/N/B (checkpoint s fotkou) |
| Vyfotit auto z levé / pravé / obou kamer | V / N / B + photo confirm dialog |
| Zazipovat a uložit mapu do PostgreSQL | Save Map Page (2-fázový retry-safe) |
| Spustit autonomní průjezd uložené mapy | Playback wizard |
| Zastavit autonomní jízdu nouzovým stopem | F1 nebo červený floating widget kdykoliv |
| Vrátit Spota domů po přerušení | Žluté tlačítko „STOP s návratem domů" |
| Detekovat SPZ na uložených fotkách (auto) | OCR worker — async, na pozadí |
| Re-OCR fotky lepším enginem (Nomeroff) | CRUD → Fotky → detail → Re-OCR |
| Spravovat registr povolených / zakázaných SPZ | CRUD → SPZ |
| Procházet historii všech runů | CRUD → Běhy |
| Exportovat ZIP libovolného runu (fotky + JSON) | Result page po jízdě nebo CRUD → Běhy |
| Sdílet mapy mezi PC | Mapy jsou v DB → stačí stejná `DATABASE_URL` |
| Zkontrolovat Wi-Fi spojení před připojením | Wifi check (ping + TCP 443) |
| Diagnostikovat aplikaci (verze balíčků) | `python main.py --diag` |
| Detekovat zombie OCR řádky a uklidit | Auto sweep při startu + každých 10 min |
| Tolerovat krátký výpadek DB (Wi-Fi switch) | OCR worker exponential backoff |
| Bezpečně skončit při kill signálu | aboutToQuit → bundle.disconnect s timeoutem |

---

## 12. Robustnost a edge case zvládnutí

| Edge case | Řešení |
|---|---|
| Operátor zavře okno během recordingu | `closeEvent` confirm dialog → `safe_abort` → recorder.stop + lease release |
| Wi-Fi výpadek během runu | Heartbeat + retry; pokud ztratí lokalizaci → status `aborted` |
| Disk plný / temp/ neuklizený | `cleanup_temp_root` při startu + každých 30 min mezi wizardy |
| Druhá instance ve stejném účtu | `QLockFile` raise s CZ zprávou |
| Záseky GraphNav `NotReadyYetError` | retry s 1s sleep do 20 s celkem |
| Locale-specific ping output | locale-independent `returncode == 0` smyčka |
| Bosdyn 4.1 streaming download bug | dočasné přepnutí flagu `_use_streaming_graph_upload` |
| Corrupted JPEG v DB | `cv2.imdecode is None` → `RuntimeError` → mark_failed |
| Service pod jiným jménem | `_ensure_client_by_type` fallback přes `service_type` |
| Heslo v plain `.env` | `DATABASE_URL_TEMPLATE` + keyring backend |
| OCR workspace bez modelu | `PermanentOcrError` → `worker_disabled` signál → UI status |
| RobotLost terminální stav | `ROBOT_LOST_ERROR_MARKERS` substring match → abort místo retry |
| Photo s 0 detekcí ≠ photo failed | `ocr_status='done'` ale `plate_detections` prázdné |
| Současný DB save + duplicitní jméno | `IntegrityError` → `MapNameAlreadyExistsError` (TOCTOU safe) |

---

## 13. Testování

| Sada | Soubory | Co pokrývá |
|---|---|---|
| Unit | 17 souborů `tests/unit/test_*.py` | Map archiver, plan invariants, plate normalize, recording flow (mock SDK), waypoint namer, wifi TCP-only, session disconnect timeout, estop floating widget |
| Integration | 2 soubory `tests/integration/` | Autonomy smoke (může běžet bez Spota), Spot connect (vyžaduje robota — opt-in) |

`pytest-postgresql` zajistí ephemerní PG instance na testy bez nutnosti
manuálního setup. `pytest-qt` umožní testování signálů v event loopu bez
plného GUI.

---

## 14. Distribuce a deploy

- **`setup_venv.bat`** — vytvoří `.venv` s Pythonem 3.10, nainstaluje
  `requirements.txt`. První instalace 3-5 minut (torch + ultralytics).
- **`launch.bat`** — pokud `.venv` neexistuje, zavolá setup, pak `python main.py`.
- **`activate_venv.bat`** / **`deactivate_venv.md`** — pro CLI uživatele.
- **`clean.py`** — Pythonem napsaný cleanup script (logs, temp, __pycache__).
- **PyInstaller** — frozen build je připraven (Nomeroff fallback v něm
  nefunguje, viz `fallback.py` poznámka — `sys.executable` ukazuje na
  aplikační EXE, ne Python; známé omezení).

---

## 15. Pravidla projektu (převzato z `instructions.md`)

Projekt definuje **explicitní normativ pro AI agenta i lidské vývojáře**:

- **CZ uživatelské texty** (operátor není technik, README, dialogy, log
  hlášky **česky**).
- **EN identifikátory** v kódu (třídy, funkce, proměnné, soubory, commit messages).
- **Python type hints všude**, `@dataclass` pro DTO, `pathlib.Path` namísto
  `os.path`.
- **Žádné globální proměnné**, vše přes třídy / dataclassy / DI.
- **Vrstvení**: UI ↔ services ↔ robot ↔ DB. UI nesmí volat SDK přímo.
- **Žádné importy z `autonomy/` do `app.*` před `bootstrap.inject_paths()`**.
- **Změny v `autonomy/` jsou jen additive** — nové moduly, žádné breaking
  changes (autonomy zůstává samostatně spustitelná).
- **CRUD je odstranitelný** — dynamický import + viditelnost tlačítka.
- **Nejnovější stabilní knihovny**, nepoužívat zapomenuté projekty.
- **Po každém větším úkolu**: doporučení + kritický rozbor + technický dluh.

---

## 16. Klíčové „aha-momenty" projektu

1. **Mapy jako BYTEA ZIP s SHA-256** — jediný source of truth, sdílení
   přes DB, žádný fileserver.
2. **2-fázový map save** — phase 1 stop+download → snapshot, phase 2 save
   do DB → idempotent retry. Operátor neztratí data ani když DB selže.
3. **OCR worker s `FOR UPDATE SKIP LOCKED`** — multi-worker ready
   (i když jeden zatím stačí), zombie recovery + heartbeat thread.
4. **Sdílený `SpotBundle` v MainWindow** — jeden Spot session přes 3 wizardy,
   neopakuje connect dance pro každý wizard.
5. **F1 = window-scoped E-Stop** — záměrně NE application-scoped, aby F1
   v CRUD okně nezavolalo E-Stop bez aktivního robota.
6. **Observed fiducial IDs z protobufů** — autoritativní zdroj pro
   playback `SPECIFIC_FIDUCIAL` lokalizaci.
7. **Subprocess Nomeroff fallback** — řeší torch ↔ bosdyn protobuf
   konflikt elegantně.
8. **Per-engine `plate_detections`** — fotka může mít detekce z více
   enginů, UI je všechny zobrazí, UNIQUE constraint zabraňuje duplikaci.
9. **`autonomy/` jako submodul + zákaz breakingu** — vývoj autonomie
   může jet souběžně, sjednocená app vždy konzumuje stable autonomy verzi.
10. **CZ texty + EN kód, type hints všude** — projekt je AI-agent-friendly
    i operátor-friendly současně.

---

## 17. Limity a budoucí vývoj

**Známé omezení:**

- Jeden OCR worker (pro PoC stačí, fronta se rozpustí v klidu).
- Žádné pause/resume při Wi-Fi výpadku během recordingu.
- Mapy >100 MB bez externí storage — `BYTEA` má praktický strop.
- Nomeroff fallback nefunguje v PyInstaller frozen buildu.
- `Return home` vyžaduje stálou lokalizaci — pokud Spot úplně ztratí mapu,
  je nutné fyzické dojítí.

**Možná rozšíření:**

- Multi-worker OCR (architektura na to připravená přes `worker_id`).
- WebSocket / live broadcast progressu pro vzdálený dohled.
- Spot CAM PTZ podpora (placeholder již v `spot_cam.py` autonomy).
- Detekce stejné SPZ napříč běhy (analytics nad `plate_text` indexem).
- Mobilní operátor klient (dnes jen Windows desktop).

---

## 18. Statistika repa

| Metrika | Hodnota |
|---|---|
| Hlavní aplikační kód | ~10k řádků Python (blondi) |
| Submodul autonomy | ~5k řádků Python |
| OCR knihovna | ~250 řádků (+ 60 MB modelů) |
| Počet DB tabulek | 6 |
| Počet Alembic migrací | 3 |
| Počet QWizard stránek | 9 |
| Počet pinned závislostí | 17 |
| Počet unit testů | 17 souborů |
| Pokrytí | mapping core + flow + parsery |
| Cílový OS | Windows 10/11 |
| Cílový Python | 3.10.20 (přesně) |
| Cílová DB | PostgreSQL 14+ |
| Cílový robot | Boston Dynamics Spot s GraphNav licencí |

---

## 19. Závěr — co je na projektu unikátní

Blondi **není refactor** ani **přepis** existujících nástrojů —
je to **integrace** dvou samostatných knihoven (autonomy, ocr) do **jednoho
operátorského workflow** s perzistencí v PostgreSQL a důrazem na bezpečnost
(E-Stop), spolehlivost (zombie recovery, retry-safe save) a UX (CZ wizardy
pro netechnické operátory).

Architektonicky je projekt zajímavý kombinací:

- robotiky (Spot SDK, GraphNav, AprilTag fiducials),
- computer vision (YOLOv8 + ONNX OCR),
- klasické business app (PySide6 desktop + PostgreSQL + Alembic),
- a **AI-friendly normativu** (`instructions.md` + CHANGELOG + memory
  systém pro AI agenty).

Výsledkem je proof-of-concept, který je **dostatečně robustní pro reálné
nasazení na parkovišti** a zároveň **dostatečně modulární**, aby bylo možné
ho dále rozšiřovat bez zásahů do podprojektů.
