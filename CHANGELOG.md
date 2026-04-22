# Changelog

All notable changes to project documentation (`instructions.md`,
`instructions-reference.md`, `README.md`) and developer-facing conventions.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

- **MAJOR** — breaking change v normativních pravidlech (např. přetočení "nesmíš" → "smíš"), nebo breaking change v autonomy API reflektovaný v reference.
- **MINOR** — nové pravidlo, sekce nebo scénář upgrade path.
- **PATCH** — oprava typo, upřesnění formulace, pinutí konkrétní verze závislosti.

---

## [Unreleased]

## [1.1.1] — 2026-04-22

Konzistence fix — synchronizace verzí závislostí mezi deklarovaným a
instalovaným stavem. Žádné funkční změny.

### Changed

- **`autonomy/requirements.txt`** — `bosdyn-client/core/mission` zvednuto z
  `==4.1.0` na `==5.1.4`, aby odpovídalo tomu, co je reálně nainstalované
  v `autonomy/.venv`. Zabrání náhodnému downgrade při čistém
  `cd autonomy && setup_venv.bat` po smazání venv.
- **Hlavička `instructions.md`** a **`instructions-reference.md`** —
  `applies_to.bosdyn` aktualizováno z `"4.0.x"` na `"5.1.x"`, `applies_to.pyside`
  z `"6.6+"` na `"6.7+"` (reálně 6.7.3 v `spot/.venv`). Bump verze dokumentace
  na 1.1.1.

### Fixed

- Nesoulad mezi dokumentací (hlavička `applies_to`) a realitou (`pip freeze`
  výstup z `.venv`) byl v 1.1.0 uveden jako known inconsistency. Nyní
  synchronizováno.
- Odstraněno riziko, že `autonomy/setup_venv.bat` na čisté mašině vyrobí venv
  s downgrade-ovaným `bosdyn-client==4.1.0`, který by byl nekompatibilní
  s naším smoke testem (očekává 5.x API).

## [1.1.0] — 2026-04-22

POC fixy — uzavření 8 z 24 pochybností z kritického rozboru po první implementaci.
Cíl: aplikace bezpečně projede nahrávání a playback na reálném Spotovi bez
thread leaků, bez křehkých callsite, s live OCR feedbackem.

### Added

- **`tests/integration/test_autonomy_smoke.py`** — 4 smoke testy ověří, že
  autonomy veřejné API je dostupné (importy + `hasattr` pro klíčové metody),
  naše additive moduly (`fiducial_check`, `return_home`, `waypoint_namer`)
  importovatelné, `LocalizationStrategy.SPECIFIC_FIDUCIAL` + `NavigationOutcome`
  hodnoty existují. Spouští se bez reálného Spota.
- **`tests/unit/test_pick_side_source.py`** — unit test pro auto-detect kamerového
  source (primární → fallback → None).
- **`constants.pick_side_source`** + `PREFERRED_LEFT_CANDIDATES` /
  `PREFERRED_RIGHT_CANDIDATES` — podpora pro Spot roboty, které advertise
  `frontleft_fisheye_image` místo `left_fisheye_image`.
- **Periodický temp cleanup** v `MainWindow` — QTimer 30 min, smaže `temp/map_*`
  pokud není aktivní žádný wizard.
- **Streamed OCR feedback do playback UI** — `OcrWorker.photo_processed` signál
  se propaguje přes `MainWindow` → `PlaybackWizard` → `PlaybackRunPage` a
  zobrazuje přečtené SPZ v live logu během jízdy.
- **`_teardown` metody** v `TeleopRecordPage` a `PlaybackRunPage` — zastavuje
  `ImagePipeline` QThread, `_run_thread`, `_return_home_thread`, skrývá E-Stop
  widget, smaže extrahovanou mapu z temp/. `SpotWizard.safe_abort` nyní volá
  `_teardown` aktuální stránky před disconnect bundle.
- **Sekce "POC known limitations + roadmap na produkci"** v `instructions.md` —
  16 bodů odložených na prod tier (parallel OCR, Wi-Fi resume, audit_log,
  Prometheus, GPU, retention, CRUD auth, ...).

### Changed

- **Per-user single-instance lock** — `main.py::_single_instance_lock` nyní
  obsahuje `getpass.getuser()` v jméně lock souboru (`spot_operator_<user>.lock`),
  takže více Windows uživatelů se neblokuje.
- **`SaveMapPage._start_save`** — přestal iterovat přes `wizard().pageIds()` a
  hledat `TeleopRecordPage`. Nyní používá
  `wizard().property("recording_service")`, kterou `TeleopRecordPage` nastavuje
  v `initializePage`. Méně křehké při refaktoru pořadí stránek.
- **`RecordingSidePage`** — radio buttony se nyní dynamicky enable/disable podle
  `wizard().property("available_sources")` (načteno `LoginPage` po úspěšném
  připojení). Při missing levé/pravé kamery se zobrazí italic note.
- **`MainWindow(config, *, ocr_worker=None)`** — nový parametr pro propagaci
  OCR workeru do `PlaybackWizard` kvůli live feedbacku.
- **`requirements.txt`** — pinnuté konkrétní verze podle `pip freeze` z prvního
  úspěšného venv (2026-04-22). Přidané `nomeroff_net==4.0.1` a
  `pyinstaller==6.19.0`. Odpovídá `applies_to: bosdyn: "5.1.x"` (bylo uváděno
  "4.0.x" v hlavičce — bude aktualizováno v 1.2.0).
- **`setup_venv.bat`** — odstraněny extra `pip install nomeroff_net` a
  `pip install pyinstaller` řádky (nyní v requirements.txt = single source of
  truth).

### Fixed

- Thread leak: `ImagePipeline` QThread zůstával běžet po zavření recording nebo
  playback wizardu. Nyní se zastaví v `_teardown`.
- Crash risk: `OcrWorker.photo_processed` signál mohl přijít do zavřené
  `PlaybackRunPage`. Nyní kontroluje `self.isVisible()` a disconnect v
  `_teardown`.

### Known limitations (viz "POC known limitations" v instructions.md)

- Stále jen 1 OCR worker (fronta se může kumulovat při >30 fotek/min).
- `ocr_status=failed` nemá auto-retry.
- Wi-Fi loss uprostřed playbacku = abort, ne pause/resume.
- Nomeroff_net v hlavním venv, ne v separate venv.
- CRUD bez auth.
- Alembic 0001 je monolit — budoucí revize musí být additive.

## [1.0.0] — 2026-04-22

### Added

- `instructions.md` rozdělen na **core** (zkráceno na ~350 řádků) a nový soubor `instructions-reference.md` (~500 řádků, detailní reference).
- **Glosář** 11 projektových pojmů (waypoint, checkpoint, fiducial, run, capture_sources, ...) v `instructions.md`.
- **9 inline code samples** v `instructions-reference.md` pro kritické pasáže:
  - `map_archiver.zip_map_dir` + `extract_map_archive` + SHA verifikace
  - `map_storage.map_extracted` context manager + `save_map_to_db`
  - `OcrPipeline.process` (YOLO detector + fast-plate-ocr reader)
  - `OcrWorker.run` loop s `SELECT FOR UPDATE SKIP LOCKED`
  - `visible_fiducials` + distance calculation
  - `return_home` (abort + relocalize + navigate_to)
  - `FastPlateReader._unpack_result` (tolerance 4 tvarů výstupu)
  - `RecordingService.stop_and_archive_to_db` (recording → ZIP → DB flow)
  - Alembic migrace `ALTER COLUMN ... SET STORAGE EXTERNAL` pro BYTEA sloupce
- **YAML frontmatter** s `version`, `last_updated`, `next_review`, `applies_to` (Python/bosdyn/PySide/SQLAlchemy verze) v obou `instructions-*.md`.
- **Upgrade path** sekce v `instructions.md` pokrývající 5 scénářů:
  1. Next-review procedura
  2. Python 3.11+ podpora
  3. Breaking change v autonomy API
  4. Přidání nové OCR engine
  5. Restrukturalizace DB
  6. Pinutí verzí závislostí
- **Sekce "Jak pinout verze závislostí"** v `instructions-reference.md` — postup pro první zafixování + budoucí upgrade.
- **Pravidlo #5** v "Pravidla pro každý prompt" — ochrana samostatnosti podprojektů (`cd autonomy && launch.bat` a `cd ocr && python ocrtest.py` musí vždy projít).
- **Pravidlo #6** v "Pravidla pro každý prompt" — striktní rozdělení rolí mezi `README.md` / `instructions.md` / `instructions-reference.md`.
- **Zákaz #12** v "Co agent NESMÍ" — aplikace nesmí bypassovat E-Stop (F1 + floating widget musí volat `EstopManager.trigger()` přímo).
- **Zákaz #14** — zákaz duplikovat obsah mezi dokumenty.
- Sekce **"Pro vývojáře"** v `README.md` s odkazy na `instructions.md`, `instructions-reference.md`, `CHANGELOG.md`.

### Changed

- **Deduplikace** mezi `README.md` a `instructions.md`:
  - Klávesové zkratky, troubleshooting, safety postupy přesunuty **jen do `README.md`**.
  - Implementační pořadí, DB schéma, styl kódu přesunuty **jen do `instructions-reference.md`**.
  - `instructions.md` si ponechal jen architektonická rozhodnutí, zákazy, pravidla pro prompt.
- Reorganizace sekcí `instructions.md` — pořadí: úvod → glosář → rozhodnutí → zákazy → pravidla → upgrade path → odkazy.

### Removed

- Z `instructions.md` vyjmuty duplikáty klávesových zkratek (patří do `README.md` sekce 9).
- Z `instructions.md` vyjmuta plná DB schéma definice (patří do `instructions-reference.md`).
- Z `instructions.md` vyjmuty kompletní implementační pořadí a styl kódu (patří do `instructions-reference.md`).

---

## Předchozí stav

Do verze 1.0.0 existoval pouze jeden monolitický soubor `instructions.md`
(~900 řádků), který obsahoval vše: normativní pravidla, API signatury, DB
schéma, troubleshooting, klávesy. Bez verzování, bez frontmatteru, bez
CHANGELOG. Tato verze (1.0.0) je první formální release dokumentace.
