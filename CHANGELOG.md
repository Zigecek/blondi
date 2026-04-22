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
