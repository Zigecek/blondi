---
version: 1.3.0
last_updated: 2026-04-23
next_review: 2026-07-22
applies_to:
  python: "3.10"
  bosdyn: "5.1.x"
  pyside: "6.7+"
  sqlalchemy: "2.0.x"
document_role: core
---

# Spot Operator — Instrukce pro AI agenta / vývojáře

Tento dokument je **normativní** — popisuje *co a proč* se má implementovat.
Implementační detaily (API signatury, DB schéma, code samples) jsou v
[`instructions-reference.md`](instructions-reference.md).
Operátorský návod (klávesy, troubleshooting, safety postupy) je v
[`README.md`](README.md).

---

## Cíl

V rootu `c:\Users\zige\spot\` existuje Windows desktop Python aplikace
(`spot_operator`), která sjednocuje dva existující podprojekty:

- **`autonomy/`** — Spot SDK + GraphNav recording/playback + live view + E-Stop (samostatně spustitelná).
- **`ocr/`** — YOLO detektor SPZ + OCR pipeline pro české SPZ (samostatný skript).

Aplikace pokrývá tři funkční oblasti:

1. **CRUD nad registrem SPZ + spot-runů + fotek** (dev/admin — **musí být fyzicky odstranitelná**; v produkci to dělá jiný systém přes DB).
2. **Wizard pro nahrávání mapy** operátorem (user-friendly, step-by-step).
3. **Wizard pro autonomní průjezd uložené mapy** s focením SPZ.

Jediný perzistentní kanál je **PostgreSQL**. Mapy jako ZIP v `BYTEA`, fotky
jako JPEG v `BYTEA`. Žádný HTTP server, žádný sdílený filesystem — kdokoli se
odkudkoli připojí ke stejné DB a vidí totéž.

---

## Glosář

| Pojem | Definice |
|---|---|
| **Waypoint** | Bod v GraphNav mapě. Bez fotky. Pojmenovaný `WP_NNN`. |
| **Checkpoint** | Waypoint **s přiřazenou fotkou** (nebo více). Pojmenovaný `CP_NNN`. V `maps.checkpoints_json[*]`. |
| **Fiducial** | AprilTag marker v prostředí (černobílý čtverec). ID je integer (typicky 1–100). Slouží k lokalizaci Spota. |
| **Run** | Jedno spuštění playbacku mapy. Řádek v `spot_runs`. Unikátní `run_code` (např. `run_20260422_1530`). |
| **capture_sources** | Per-checkpoint seznam jmen Spot image sources (`left_fisheye_image` / `right_fisheye_image` / oba) použitých pro focení na daném checkpointu. Operátor volí v teleopu individuálně přes klávesy **V / N / B** (vlevo / vpravo / obě) nebo tlačítka. Před uložením se zobrazí `PhotoConfirmOverlay` s live preview. Mapa samotná má v `default_capture_sources` obě strany (fallback pro playback). |
| **SPZ** | Státní poznávací značka. Česká: 1–8 znaků A–Z + 0–9. V DB `plate_text`. |
| **Detekce** | Jedna identifikovaná SPZ na fotce jedním OCR engine. Řádek v `plate_detections`. |
| **Bundle** | `SpotBundle` z `session_factory.connect()` — session + estop + lease + power + move_dispatcher. |
| **OCR pipeline** | YOLO detektor + fast-plate-ocr reader. Jedno volání = jedna fotka → list detekcí. |
| **Mapa** | GraphNav mapa kompletně v DB jako ZIP (`maps.archive_bytes`). Verifikovaná SHA-256. |
| **Additive (úprava autonomy)** | Přidání nového souboru/modulu do `autonomy/app/robot/` bez úpravy existujících souborů. |

---

## Architektonická rozhodnutí

### 1) Python 3.10 v jednom venv v rootu

Boston Dynamics Spot SDK vyžaduje Python 3.7–3.10. Použij **Python 3.10 x64**
doinstalovaný vedle stávajícího 3.12, a v rootu projektu vytvoř `.venv/`
nad Pythonem 3.10. Celá aplikace (autonomy wrappery, OCR, GUI, DB, OCR worker)
běží v **jednom procesu v jednom venv**.

### 2) `autonomy/` a `ocr/` jsou podprojekty-knihovny

- **Nesmí se v nich rozbít samostatné fungování.** `cd autonomy && launch.bat` i `cd ocr && python ocrtest.py` musí dál běžet beze změny.
- V `autonomy/` smíš dělat pouze **additive změny** — nové moduly, žádné breaking changes v API existujících souborů.
- V `ocr/` **žádné změny**. Voláme ho přes `sys.path` a subprocess.
- Nová aplikace je volá přes `sys.path` injekci (`spot_operator/bootstrap.py`), ne přes editable install ani kopírování.

### 3) Mapy kompletně v DB jako ZIP

GraphNav mapa (graph + waypoint_snapshots + edge_snapshots + checkpoints.json) se
po stažení **zipne**, spočítá SHA-256 a uloží do `maps.archive_bytes BYTEA`. Při
playbacku se vyextrahuje do `temp/map_<id>_<uuid>/`, uploadne se do robota, po
ukončení se temp smaže. **Žádné trvalé mapové soubory na disku.**

### 4) Fotky v DB jako BYTEA

Každá fotka se ihned po zachycení uloží do `photos` tabulky s `ocr_status='pending'`.
OCR worker (background QThread) fotky asynchronně zpracovává přes YOLO +
fast-plate-ocr a zapisuje detekce do `plate_detections`.

### 5) CRUD modul musí být fyzicky odstranitelný

Celá složka `spot_operator/ui/crud/` jde smazat a aplikace dál funguje. `MainWindow`
dělá:

```python
try:
    from spot_operator.ui.crud.crud_window import CrudWindow
    ...
except ImportError:
    # Tlačítko "Správa SPZ a běhů" se skryje.
    pass
```

**Žádný feature flag** — absence souboru = absence funkce.

### 6) Jedna PySide6 aplikace

Ne sada skriptů. Jedna `QApplication` s jedním `MainWindow` (launcher se 3 velkými
tlačítky — Spustit jízdu, Nahrát mapu, Správa SPZ).

### 7) Single instance lock

`QLockFile` v `temp/spot_operator.lock`. Brání druhému spuštění ve stejném user účtu.
Více workerů proti stejné DB je řešeno `SELECT FOR UPDATE SKIP LOCKED`, ne single-instance lockem.

### 8) Credentials Spota v Windows Credential Locker

Přes `keyring`. V DB je jen metadata (`label`, `hostname`, `username`, `keyring_ref`);
samotné heslo je v OS trezoru.

---

## Co agent NESMÍ udělat

1. **Nesmí editovat existující soubory** v `autonomy/` (kromě přidávání nových modulů do `autonomy/app/robot/`).
2. **Nesmí dělat žádné změny v `ocr/`** — referujeme přes `sys.path` a subprocess.
3. **Nesmí rozbít standalone fungování** `autonomy/` ani `ocr/`. Po každé změně musí `cd autonomy && launch.bat` nastartovat jako dřív, a `cd ocr && python ocrtest.py` běžet.
4. **Nesmí použít balíček `app`** jako top-level — autonomy má `app/` a kolize by byla fatální. Používej `spot_operator`.
5. **Nesmí ukládat mapy ani fotky na disk trvale.** Všechno perzistentní je v PG.
6. **Nesmí instalovat** `paddlepaddle`, `tensorflow`, `mediapipe` — riziko protobuf konfliktu s `bosdyn`.
7. **Nesmí používat blokující volání v Qt main threadu** (connect, capture, upload, navigate, OCR). Dlouhé operace v `QThread`.
8. **Nesmí vytvořit feature flag** pro CRUD odstranitelnost — odstranitelnost je **fyzická absence složky**.
9. **Nesmí hard-kódovat** Spot IP nebo credentials; vše přes `.env` + `keyring`.
10. **Nesmí skrývat chyby DB nedostupnosti** — `main.py` fatálně skončí pokud DB ping selže.
11. **Nesmí amendovat existující Alembic revizi** po commitu. Každá změna schématu = nová revize.
12. **Nesmí aplikace bypassovat E-Stop** — E-Stop tlačítko (F1 + floating widget) musí vyvolat `EstopManager.trigger()` přímo, ne jen soft velocity stop.
13. **Nesmí dělat důležitá architektonická rozhodnutí sám** — viz "Pravidla pro každý prompt" níže.
14. **Nesmí duplikovat obsah mezi `README.md` a `instructions*.md`** — klávesy a troubleshooting patří **jen do README**; normativní pravidla **jen do `instructions.md`**; API a code samples **jen do `instructions-reference.md`**.

---

## Pravidla pro každý prompt (POVINNÉ)

### 1) Před každým promptem přečíst instrukce

Agent na začátku každého promptu přečte:

- Tento soubor (`instructions.md`) — pravidla, zákazy, rozhodnutí.
- Podle potřeby `instructions-reference.md` — API, DB schéma, code samples.
- Podle potřeby `README.md` — operátorské kontrakty (klávesy, safety, troubleshooting).
- Volitelně `autonomy/instructions.md` — Spot SDK konvence.

Nikdy nepředpokládej, že instrukce znáš z paměti — vždy je znovu načti a ověř.

### 2) Nerozhodovat důležité věci sám — vždy se zeptat

Agent nesmí činit důležitá rozhodnutí (architektonická, technologická, návrhová,
schema DB, volba OCR engine, změny v autonomy/ocr) samostatně. Vždy se má
**zeptat uživatele**, raději víc než míň. U dotazu musí:

- uvést **možná řešení s detaily** (výhody, nevýhody, dopady),
- přidat své **doporučení** s odůvodněním,
- nechat finální rozhodnutí na uživateli.

### 3) Používat vždy co nejnovější technologie a knihovny

Agent preferuje:

- **nejnovější stabilní verze** knihoven,
- knihovny s **největší podporou komunity**,
- aktivně udržované projekty,
- **ne** izolované, nepodporované nebo zapomenuté knihovny.

Cíl: to, co je aktuálně nejlepší, nejpopulárnější a má nejdelší očekávanou životnost.

**Výjimka:** Spot SDK (`bosdyn-*`) je svázáno s Pythonem 3.10 a konkrétní verzí
protobuf — tady prioritou je **kompatibilita**, ne novinka.

### 4) Po dokončení úkolu dát doporučení a kritický rozbor

Po každém dokončeném úkolu agent poskytne:

- **doporučení pro vylepšení** — jak by se daná věc dala dále zlepšit,
- **kritický rozbor implementace** — možné díry, slabiny a rizika,
- **prostor pro zlepšení** — co bylo zjednodušeno, kde hrozí technický dluh.

Toto je **povinná** součást každého dokončeného úkolu.

### 5) Chránit samostatnost podprojektů

Úpravy v `autonomy/` jsou povolené, ale pouze **additive**. Úpravy v `ocr/`
**nejsou povolené** vůbec. Po každé úpravě agent musí:

- verifikovat, že `cd autonomy && launch.bat` projde (spustí autonomy GUI),
- verifikovat, že `cd ocr && python ocrtest.py` projde (nad testovací fotkou),
- teprve potom považovat task za dokončený.

### 6) Při úpravě dokumentace dodržovat rozdělení rolí

- Klávesové zkratky, troubleshooting, operátorské postupy → **jen `README.md`**.
- Normativní pravidla, zákazy, architektonická rozhodnutí → **jen `instructions.md`**.
- API signatury, DB schéma, code samples → **jen `instructions-reference.md`**.

Pokud se obsah rozvětvuje, napiš v druhém souboru **jednořádkový odkaz**, ne duplicitu.

---

## POC known limitations + roadmap na produkci

Aplikace je ve stavu **POC**. Následující známá omezení jsou **přijatá** pro POC
fázi a **musí být vyřešena před ostrým provozem**. Kategorie podle priority.

### Kritická — před produkcí vyřešit

- **Parallel OCR workers** — teď běží jeden worker. Při >30 fotek/min se fronta
  kumuluje (fast-plate-ocr ~1-2 s/fotku na CPU). Kód (`claim_next_pending` s
  `SKIP LOCKED`) paralelní workery podporuje — stačí z konfigurace spustit N.
- **`ocr_status=failed` auto-retry** — teď zůstává v `failed` napořád, operátor
  musí ručně v CRUD "Re-OCR". Přidat retry s exponential backoff a `max_retries`
  v konfiguraci.
- **Wi-Fi loss resume** — teď se běh ukončí jako `aborted`, mapa v DB zůstane
  platná. Design pause+resume by perzistoval stav průběžně a po reconnect
  pokračoval.
- **CRUD autentizace** — aktuálně otevřené. V prod buď smazat celou složku
  `spot_operator/ui/crud/` (design zámysl odstranitelnosti), nebo přidat
  token-based / Windows-account auth.
- **End-to-end OCR test** — teď jen normalizace. Přidat test s reálnou fotkou
  z `ocr/test/` a ověřit, že pipeline vrací očekávanou SPZ s confidence > 0.7.

### Důležitá — brzy po nasazení

- **audit_log tabulka** — kdo/kdy vytvořil/smazal/upravil mapu, SPZ záznam, běh.
- **Prometheus metriky OCR workeru** — počet zpracovaných fotek, latence,
  úspěšnost detekce, queue depth.
- **Retention CLI job** — `python -m spot_operator.services.retention
  --older-than 90` pro mazání starých fotek.
- **GPU akcelerace OCR** — auto-detekce `onnxruntime-gpu` provideru podle
  dostupnosti NVIDIA CUDA.
- **BYTEA archival** — partitioning `photos` po měsíci nebo S3 offload, až se
  DB přiblíží 100 GB.

### Nice to have

- **Nomeroff separate venv** — teď `nomeroff_net` a `torch` bydlí v hlavním
  venv (viz `requirements.txt`). Subprocess v `fallback.py` izoluje jen
  paměť/GIL, ne závislosti. Řešení: `.venv_nomeroff` + `sys.executable` v
  `fallback.py`.
- **Keyring per-user upozornění** — UI v login kroku by mohlo upozornit
  "credentials jsou viditelné jen pro tvůj Windows účet".
- **Alembic granularita** — `0001_initial` je monolit. Budoucí revize **budou
  additive** (nová tabulka, nový sloupec), ne editace existující.

---

## Upgrade path

Tento dokument má `next_review` v hlavičce. Když agent nebo developer otevře
dokument a `next_review` je v minulosti, **musí ověřit aktuálnost**:

1. Zkontrolovat aktuální verze `bosdyn-client`, `PySide6`, `SQLAlchemy`, `fast-plate-ocr`.
2. Zkontrolovat, jestli Spot SDK neposunulo podporu Python verzí (např. přidání 3.11).
3. Zkontrolovat CHANGELOG klíčových knihoven — breaking changes?
4. Aktualizovat `version`, `last_updated`, `next_review` v hlavičce obou `instructions-*.md`.
5. Zreflektovat změny v `CHANGELOG.md`.

**Kdo to dělá:** developer nebo AI agent, který poprvé narazí na zastaralou hlavičku.
**Kdy:** jakmile `next_review < today`. Cyklus: každé 3 měsíce.

### Scénář: Python 3.11+

Pokud Spot SDK začne podporovat Python 3.11+:

1. Přepni `applies_to.python` v hlavičce na `"3.10 | 3.11"`.
2. Ověř `setup_venv.bat` funguje pro obě verze.
3. Přepiš README sekci 3 "Proč Python 3.10 a ne 3.12" → "Podporované Python verze".
4. Neodstraňuj 3.10 hned — ponech kompatibilitu alespoň 6 měsíců.
5. `CHANGELOG.md` + bump MINOR verze.

### Scénář: Breaking change v autonomy API

Pokud autonomy upraví stabilní API, které `spot_operator` používá:

1. Rozhodni: revert v autonomy NEBO update spot_operator.
2. Pokud update: upravit volající kód + aktualizovat odpovídající API signatury v `instructions-reference.md`.
3. `CHANGELOG.md` + bump **MAJOR** verze (breaking change v reference dokumentu).

### Scénář: Přidání nové OCR engine

Pokud přibude třetí OCR engine (např. TrOCR):

1. Přidat `spot_operator/ocr/<new_engine>.py`.
2. Rozšířit `constants.OCR_ENGINE_*`.
3. `plate_detections` schéma nemění (již podporuje `engine_name`).
4. Dokumentovat v `instructions-reference.md` sekci "OCR pipeline" + code sample.
5. `CHANGELOG.md` + bump MINOR verze.

### Scénář: Restrukturalizace DB

Pokud je potřeba zásadní změna schématu:

1. Nová Alembic revize (nikdy needitovat existující).
2. Aktualizovat `instructions-reference.md` sekci "DB schéma".
3. `CHANGELOG.md` + bump MINOR nebo MAJOR podle dopadu na klienty.

### Scénář: Pinutí verzí závislostí

Postup je v `instructions-reference.md` sekci "Jak pinout verze závislostí".
Po úspěšném pinutí: `CHANGELOG.md` + bump PATCH.

---

## Odkazy

- **[`instructions-reference.md`](instructions-reference.md)** — adresářový layout, DB schéma, API signatury, 9 code samples, implementační pořadí, styl kódu, postup pinutí verzí.
- **[`README.md`](README.md)** — operátorský návod (15 sekcí): instalace, spuštění, klávesy, troubleshooting, bezpečnostní poznámky.
- **[`CHANGELOG.md`](CHANGELOG.md)** — historie verzí dokumentace.
- **[`autonomy/instructions.md`](autonomy/instructions.md)** — původní Spot SDK konvence (referenční).
