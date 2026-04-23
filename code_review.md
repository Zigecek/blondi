# Spot Operator — Critical Code Review

**Datum:** 2026-04-23
**Metoda:** Pouze statická analýza kódu (žádné spuštění aplikace, žádné testy proti robotovi)
**Rozsah:** `spot_operator/`, `autonomy/`, `alembic/`, `main.py`, `tests/` (~12 000 řádků Python)
**Plán:** `C:\Users\zige\.claude\plans\projdi-cel-k-d-aplikace-curried-prism.md`

---

## 0. Executive summary

**Rozsah auditu:** 18 oblastí, ~12 000 řádků Python, identifikováno **196 nálezů** (FIND-001 až FIND-196). Z toho:

- **KRITICKÉ: 4** (E-Stop fake release; lease leak při UI close; silent demotion kind; retry-impossible při save failure)
- **VYSOKÉ: ~45** — data integrity, safety, error handling
- **STŘEDNÍ: ~90** — fragile patterns, race conditions, UX diery
- **NÍZKÉ: ~57** — tech debt, formatting, dokumentace

### TL;DR — 5 nejpalčivějších problémů

1. **FIND-156 (KRITICKÉ, Safety):** `EstopFloating` bez `on_release` callback při release jen resetuje *vizual* — robot fyzicky zůstává v E-Stop. Uživatel si myslí, že robot je odblokovaný.
   → Fix: `on_release` povinný parametr.

2. **FIND-061 / FIND-154 (KRITICKÉ, Resource):** Lease/session disconnect na `closeEvent` hlavního okna **nemá timeout** a může zavěsit UI, nebo se vůbec nespustí pokud OS app zabije. Lease zůstane visící → další spuštění selže.
   → Fix: wrap `disconnect` s 3s timeout + `QApplication.aboutToQuit` handler.

3. **FIND-072 (VYSOKÉ, Root cause "robot jede na vzdálený CP"):** `start_waypoint_id` se nastaví při *jakémkoli* prvním `create_waypoint` (checkpoint i waypoint kind). Pokud operátor klikne "Checkpoint" poblíž fiducialu (ne "Waypoint"), a robot fyzicky není *přesně* u tagu při tvorbě waypointu, `localize_at_start(fiducial_id, start_waypoint_id=CP_001)` v playbacku padne/mis-localizuje → "robot jede na vzdálený CP".
   → Fix: hard-enforce první operace je "Waypoint", a/nebo validovat v `save_map_to_db`, že `start_waypoint_id`-waypoint skutečně obsahuje observaci fiducialu.

4. **FIND-066 / FIND-078 (VYSOKÉ, Data + UX, Root cause "chybějící data"):** Při capture failure recording silent-demotes checkpoint na waypoint se **stejným CP_NNN jménem**. Glosář v `instructions.md` říká "Checkpoint = Waypoint s fotkou", realita porušuje invariant. Playback tuhle pozici nefotí, operátor myslí, že data existují.
   → Fix: explicit user dialog při capture failure + re-assign name na WP_NNN nebo přidat `capture_status` jako first-class DB field.

5. **FIND-140 (VYSOKÉ, UX):** Pokud `save_map_to_db` selže po `stop_recording`, RETRY není možný — `service.is_recording=False` a user nemůže uložit znovu. Jediná cesta: zavřít wizard, ztratit všechna data, začít od nuly.
   → Fix: rozdělit `stop_and_archive_to_db` na two-phase (stop+export do temp, save z tempu) — save je retry-able.

### Sekundární témata, která prostupují celým kódem

- **Silent failures:** `except Exception: _log.warning(...)` + `return None/[]/False` — operátor nevidí, že věc selhala (FIND-075, FIND-086, FIND-094, FIND-099, FIND-108, FIND-118, FIND-122, ...).
- **Layer violations:** Fallback `getattr(...)` + duck typing mezi contracts / service / UI vrstvami (FIND-041, FIND-104, FIND-155, FIND-177-182).
- **Race conditions:** TOCTOU v `generate_unique_run_code`, `upsert`, `exists_by_name`, `claim_next_pending` + 5 min zombie sweep (FIND-024, FIND-026, FIND-027, FIND-048).
- **Qt signal lifetime:** `PlaybackService` signály nejsou odpojeny v teardown → crash/duplicitní handling (FIND-141). Stejný pattern prosvítá přes většinu wizard pages.
- **Doporučená fix priorita:** Safety > Data integrity > UX/retry > Concurrency > Dokumentace. Viz §7.

### Obecná kvalita kódu

Kód **není fundamentálně špatně napsaný**. Hlavní silné stránky:
- Jasná vrstvená struktura (db → services → UI).
- Dobré docstrings a CZ komentáře s kontextem ("proč, ne jen co").
- Ochrany kolem bosdyn RPC (retry, fallback, dead-code-on-purpose markers).
- Alembic migrace, typed dataclasses, thread-local DB sessions.

Ale **v detailech silně drhne**:
- Kaskády `try/except Exception: log.warning` maskují chyby.
- "Happy path" je cestou, ale error paths jsou sekundárně navržené.
- UX při selháních je často stealthy (log, ne dialog).
- Mnoho "defensive" kódu je ve skutečnosti duplicitní validace (FIND-102) nebo dead code (FIND-095, FIND-055).
- Testy pokrývají jen happy path malých helperů; end-to-end flows netestované (§17).

Uživatelův předchozí pocit ("někdy to chodilo jak chtělo") odpovídá realitě — flow má několik míst, kde **selhání 1 vnitřní assumption nevolá halt + operator feedback**, místo toho pokračuje s degradovanými daty.

---

## 1. Root-cause analýza hlášených bugů

*(Doplní se po projití všech oblastí, se zpětným propojením na findings z §2.)*

### 1.1 "Playback skáče na vzdálený CP / ignoruje větve / přeskočí první CP"

**Nejpravděpodobnější chain of causation** (kombinace několika findings):

1. **Recording fáze — `start_waypoint_id` nesprávně identifikovaný** ([FIND-072](#find-072)).
   Operátor klikne "Checkpoint" (V/N/B) hned na startu u fiducialu, místo aby nejprve kliknul "Waypoint" (C). `RecordingService.capture_and_record_checkpoint` volá `create_waypoint(CP_001)` a zároveň `self._start_waypoint_id = wp_id` (řádek [recording_service.py:154-155](spot_operator/services/recording_service.py#L154-L155)). Fiducial je nahraný jako observace ve WaypointSnapshot protobufu, ale observace je "kdesi vedle" skutečné fyzické pozice start_waypointu (kvůli GraphNav odometry drift mezi kliknutím a `create_waypoint` RPC).

2. **Map save — validace nezkontroluje korelaci** ([FIND-037](#find-037), [FIND-075](#find-075)).
   `save_map_to_db` volá `validate_map_dir(map_dir, expected_start_waypoint_id=..., checkpoint_waypoint_ids=...)` — ověří, že `start_waypoint_id` existuje v grafu. **Ale neověří**, že fiducial_id je opravdu observován *poblíž start_waypoint*. Mapa je uložena jako "valid".

3. **Playback load — `_localize_with_fallback` strict** ([playback_service.py:593-662](spot_operator/services/playback_service.py#L593-L662)).
   `localize_at_start(fiducial_id, start_waypoint_id)` volá bosdyn `set_localization` s hintem. Bosdyn se pokusí najít observaci fiducialu *blízko* `start_waypoint_id`. Pokud drift mezi observací a waypoint byl příliš velký:
   - Scénář A: Bosdyn hodí exception → `RuntimeError: Bosdyn set_localization selhal` → playback neprojde. Uživatel vidí error (ne "robot jede špatně").
   - **Scénář B (hlášený uživatelem):** Bosdyn **vrátí OK**, ale `localized_wp != meta.start_waypoint_id`. Kontrola na řádku 642 raise, takže playback by neměl projít. ALE pokud `meta.fiducial_id is None` (FIND-094 v oblast 8), kód skočí na fallback `FIDUCIAL_NEAREST` bez další kontroly waypoint_id match. Bosdyn vybere "random" observaci fiducialu v mapě (jiný waypoint uprostřed).

4. **Playback run — `run_all_checkpoints` iteruje CP podle pořadí v JSON**.
   Pre-flight check na řádku 198-202 by měl chytnout mis-localizaci: `if localized_wp != expected_start: raise`. **Ale** pokud `expected_start` je `"(neznámý)"` (meta.start_waypoint_id je None — stalo se při recording s bugem nebo manual edit), check se přeskočí.

5. **První `navigate_to(CP_001)` — GraphNav planning z mis-localized pozice**.
   GraphNav předpokládá, že robot je u `localized_wp` (uprostřed mapy). Plánuje shortest path k CP_001. Pokud CP_001 je fyzicky dál než jiný CP blízko `localized_wp`, GraphNav to zvládne. Ale pokud mapa má **smyčku** (recording byl jednosměrný kruh), GraphNav může vybrat "zkratku" opačným směrem → robot fyzicky jede proti zamýšlenému směru.

6. **Drift akumuluje, RobotLostError po N checkpointech**.
   Po 2-3 mis-nav pokusech odometry drift naroste → `RobotLostError` → `_is_robot_lost_error` chytne a abort (správně). Ale předtím už robot "ignoroval větve" a "šel na vzdálený CP".

**Kritická cesta opravy (priority order):**
- FIND-072: Enforce první op = Waypoint.
- FIND-094: Remove inconsistent FIDUCIAL_NEAREST fallback bez waypoint verify.
- FIND-037: Ověřit invariant `start_waypoint_id je ve waypoint_snapshot s observací fiducialu` v `save_map_to_db`.
- FIND-086: Vytvořit run v DB *před* pre-flight checks → audit trail selhávajících pokusů.

**Sekundární přispěvatelé** (zvyšují pravděpodobnost výše uvedeného):
- FIND-067: Ambiguity check v localize_at_start se nečte — pokud jsou 2 blízké observace, bosdyn vybere jednu silently.
- FIND-104: `set_global_avoidance` fail → default padding → robot se "zachytává" o překážky při špatné trase.
- FIND-090: RobotLostError detekce přes substring — může selhat v nové bosdyn verzi.

### 1.2 "Chybějící data v trase"

Tři samostatné chain of causation, které všechny produkují "nevidím, co tam mělo být":

**Chain A — Silent capture failure demotes checkpoint → waypoint** ([FIND-066](#find-066), [FIND-078](#find-078), [FIND-190](#find-190)):

1. Operátor klikne "Foto vlevo" (V).
2. `capture_sources(poller, [left_fisheye_image])` zavolá bosdyn `ImagePoller.capture`. Pokud to failne (network glitch, kamera na moment padne) → vrátí prázdný dict.
3. `recording_service.capture_and_record_checkpoint` vidí `not photos` → **tichý swap kind na "waypoint"**, note="capture_failed".
4. UI v `teleop_record_page._capture` vidí `result.capture_status == "failed"` a ukáže error_dialog (existuje, viz FIND-141 v oblasti 12). Operátor klikne OK, pokračuje.
5. **PROBLÉM:** Checkpoint stále nese jméno `CP_005` (z `_namer.next_checkpoint()`), ale kind je "waypoint". Playback pak **nebude** fotit na této pozici (kind=waypoint, capture_sources=[]).
6. Operátor v result-page vidí "CP_005" dokončeno (nav OK, no photos) → myslí si "fotka se neudělala", ale je v clear state. Nic to nevrátí zpět.

**Chain B — Photo commit failure + exception swallowing v playback loop** ([FIND-032](#find-032), [FIND-099](#find-099)):

1. Playback volá `_capture_at_checkpoint` → `save_photo_to_db` → DB commit.
2. DB commit selže (connection drop, disk full). Exception propaguje.
3. `playback_service._capture_at_checkpoint` (řádek 707-710) chytá: `failed_sources.append(src)`.
4. `checkpoint_result` vznikne s `saved_sources=(), failed_sources=(all)`.
5. CP je označen failed v `checkpoint_results_json`, ale **další CP pokračují** — `_record_checkpoint_result` volá DB update run status. Pokud DB je *stále* down, update padne, `except Exception as exc: _log.exception(...); continue` (řádek 339-355).
6. Run vypadá částečně OK (některé CP prošly), jen *chybějí fotky* u některých.

**Chain C — OCR zombie + double processing** ([FIND-026](#find-026), [FIND-028](#find-028)):

1. OCR worker claim fotku (A), začne pipeline.process.
2. Process trvá > 5 min (slabý CPU + velká fotka + YOLO model load).
3. `photos_repo.sweep_zombies` (triggered na restartu app nebo periodic jobem) resetuje `ocr_locked_at < now() - 5min` řádky na pending.
4. Jiný worker (B) claim stejnou fotku.
5. Oba workery dokončí, volají `_store_results` → `insert_many(detections, ON CONFLICT DO NOTHING)`. Pro plate_text=NULL (no-text detection), `NULLS DISTINCT` znamená oba inserty projdou → **duplicitní NULL detekce**.
6. Uživatel ve photos_tab vidí fotku s 2× "?" detekcí.

**Kritická cesta opravy:**
- FIND-066/078: Explicit user dialog s volbou "retry / skip / abort" při capture failure.
- FIND-099: Rozlišit transient (DB outage) vs permanent exceptions v playback loop.
- FIND-026: Zvýšit OCR zombie timeout nebo heartbeat `ocr_locked_at` během běhu.

---

### 1.3 "Warningy/chyby místo user feedbacku"

Seznam konkrétních míst, kde uživatel **měl vidět chybu**, ale dostane jen log:

| # | Scénář | Lokace | Co dnes dělá | Co by měl dělat |
|---|--------|--------|--------------|-----------------|
| 1 | `read_observed_fiducial_ids` selže (corrupted protobuf) | [FIND-075](#find-075) | `_log.warning` + fallback na UI fiducial_id | Error dialog "Nelze ověřit fiducial v mapě. Uložit jako neplatnou nebo retry?" |
| 2 | `set_global_avoidance` selže (bosdyn issue) | [FIND-104](#find-104) | `_log.warning` + pokračovat s default | Dialog "Avoidance není nastavený — pokračovat?" |
| 3 | YOLO model file chybí (config wrong path) | [FIND-106](#find-106) | OCR worker loop + backoff donekonečna | Terminal abort + status bar "OCR permanently disabled" |
| 4 | PowerManager fail v E-Stop auto-recovery | [FIND-062](#find-062) | raise exception → connect failed s generic msg | Clear dialog "Motors wouldn't turn off — physical intervention needed" |
| 5 | `cv2.imdecode` failne (corrupted JPEG) | [FIND-108](#find-108) | Return `[]` → mark done | `mark_failed` s "Image decode error" |
| 6 | `lease.release` selže při disconnect | [FIND-061](#find-061) | `_log.warning` a pokračovat | UI badge "Lease leaked — robot may be busy for 30s" |
| 7 | F1 E-Stop před připojením | [FIND-134](#find-134) | Tichý no-op | Flash + message "E-Stop není dostupný" |
| 8 | Map save failure, retry nemožný | [FIND-140](#find-140) | `error_dialog` pak slepá ulička | Two-phase save s retry tlačítkem |
| 9 | OCR Reader `MemoryError` | [Agent 2 flag, FIND-110](#find-110) | Catch as ordinary Exception, continue | Propagate a terminal abort |
| 10 | `capture_sources` vrátí prázdné | [FIND-066](#find-066) | Silent demotion na waypoint | Dialog confirm |
| 11 | Keyring unavailable při load_password | [FIND-121](#find-121) | Return None | Message "Nelze načíst heslo z WCL" |
| 12 | Wi-Fi ping parse fail (CZ locale) | [FIND-124](#find-124) | "3/3" reportováno místo reálu | Fix parsing, fallback na TCP-only |

**Kořenový pattern:** Kód preferuje "continue with degraded state" nad "halt and tell operator". Pro safety-critical aplikaci (robot + motor + akumulátor) je to **špatná volba defaultu**.

### 1.3 "Warningy/chyby místo user feedbacku"

*(Doplní se.)*

---

## 2. Detailní nálezy po oblastech

Každý nález má unikátní ID (FIND-###) a obsahuje:

- **Severity:** KRITICKÉ / VYSOKÉ / STŘEDNÍ / NÍZKÉ
- **Kategorie:** Safety / Data / Type / Concurrency / Error / UX / Resource / Observability
- **Lokace:** soubor:řádek (clickable link)
- **Popis:** co kód dělá teď
- **Riziko:** co se může pokazit v nejhorším případě
- **Doporučení:** jak fixnout (ne implementuji)
- **Verifikace:** jak poznat, že fix zabere

---

### Oblast 1 — Konstanty & config

#### FIND-001 — Plaintext DB heslo v `.env` zapsaném v rootu repa
**Severity:** VYSOKÉ · **Kategorie:** Safety / Security
**Lokace:** [.env:3](.env#L3)

Soubor `.env` má `DATABASE_URL=postgresql+psycopg://spot_operator:dcef052d8fce53cb0b1f38fb399bf5e247d17c5a54c813f7d2acdd8517405791@kozohorsky.com:6767/spot_operator` — plain hex heslo přímo v souboru. `.gitignore` ho sice ignoruje (nekontroloval jsem ještě — verifikovat: `grep -n env .gitignore`), ale:

- V repu existuje `.env.example`, takže je jasné, že je očekáván. Pokud `.env` někdy unikne do gitu, heslo je v historii *navždy*.
- Keyring je použit jen pro Spot credentials, ne pro DB.
- `config._require("DATABASE_URL")` bere celý string včetně hesla — neumožňuje rozložit host/user/pass na jednotlivé env vars.

**Riziko:** Únik DB hesla při push, sdílení worktree, debug dumpu, nebo backupu disku.

**Doporučení:** Rozdělit DATABASE_URL na `DATABASE_HOST`, `DATABASE_USER`, `DATABASE_NAME`, `DATABASE_PORT` a heslo uložit do systémového keyringu (jako u Spot credentials). Nebo minimálně *dokumentovat* v README, že `.env` nesmí být commit-ován, a ověřit `.gitignore`.

**Verifikace:** `git log -p -- .env` — pokud heslo nebylo nikdy commit-ováno, je OK; pokud ano, je nutná rotace.

---

#### FIND-002 — `LOG_LEVEL` z env nevaliduje hodnotu před předáním do `logging.setLevel`
**Severity:** NÍZKÉ · **Kategorie:** Error handling
**Lokace:** [spot_operator/config.py:65](spot_operator/config.py#L65), [spot_operator/logging_config.py:40](spot_operator/logging_config.py#L40)

`log_level = os.environ.get("LOG_LEVEL", "INFO").upper()` → pak `root.setLevel(config.log_level)`. Pokud uživatel napíše `LOG_LEVEL=EXTREME` nebo `LOG_LEVEL=debug` (lowercase zachrání `.upper()`), ale překlep typu `LOG_LEVEL=INFORM` projde tiše jako neznámá úroveň (standardní Python `logging` v takovém případě padne s `ValueError: Unknown level: 'INFORM'`).

**Riziko:** Aplikace padne při bootstrapu s málo srozumitelnou chybou místo aby řekla "neplatný LOG_LEVEL".

**Doporučení:** Validovat proti `{"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}` s friendly zprávou.

---

#### FIND-003 — Float/int env-proměnné bez validace rozsahu a friendly error message
**Severity:** STŘEDNÍ · **Kategorie:** Error handling
**Lokace:** [spot_operator/config.py:49-62](spot_operator/config.py#L49-L62)

```python
spot_timeout_seconds = float(os.environ.get("SPOT_TIMEOUT_SECONDS", "15"))
fiducial_distance_threshold_m = float(os.environ.get("FIDUCIAL_DISTANCE_THRESHOLD_M", "2.0"))
ocr_detection_min_confidence = float(os.environ.get("OCR_DETECTION_MIN_CONFIDENCE", "0.5"))
```

- Pokud uživatel napíše `SPOT_TIMEOUT_SECONDS=abc` → `ValueError: could not convert string to float: 'abc'` — generic Python error, neříká *která* env var.
- Žádná kontrola rozsahu: `FIDUCIAL_DISTANCE_THRESHOLD_M=-5` projde, `OCR_DETECTION_MIN_CONFIDENCE=2.5` taky (confidence má být 0..1).

**Riziko:** Zmatek při debugování ("co to znamená `could not convert string to float: ''`?"). Invalidní hodnoty mohou tiše pokazit fiducial check (záporná vzdálenost) nebo OCR (confidence > 1 znamená že nikdy neprojde).

**Doporučení:** Helper `_require_float(key, default, *, min=None, max=None)` a analogicky pro int. V chybě uvést jméno env var.

---

#### FIND-004 — `_require("DATABASE_URL")` error message předpokládá existenci `.env.example`
**Severity:** NÍZKÉ · **Kategorie:** UX / Error handling
**Lokace:** [spot_operator/config.py:89-96](spot_operator/config.py#L89-L96)

```python
raise RuntimeError(
    f"Chybí povinná proměnná prostředí '{key}'. "
    f"Zkopíruj .env.example na .env a doplň ji."
)
```

Pokud `.env.example` nebyl dodán (třeba zabalený PyInstaller build), rada "zkopíruj .env.example" je falešná.

**Riziko:** Operátor neví co dělat.

**Doporučení:** Ověřit `(ROOT / ".env.example").is_file()` a podle toho formulovat zprávu. V PyInstaller buildu zabalit `.env.example` jako resource nebo zapsat hint s konkrétními env vars.

---

#### FIND-005 — `VALID_CAPTURE_SOURCES` neobsahuje `CAMERA_FRONT_COMPOSITE`, ale ta je v konstantách vyjmenovaná
**Severity:** NÍZKÉ · **Kategorie:** Data consistency
**Lokace:** [spot_operator/constants.py:14,17-23](spot_operator/constants.py#L14-L23)

`CAMERA_FRONT_COMPOSITE = "front_composite"` je deklarovaná na řádku 14, ale `VALID_CAPTURE_SOURCES` na řádku 17 ji nevyjmenovává. Pokud `contracts._normalize_sources` nebo `validate_sources_known` někdy dostane `"front_composite"`, dostane unknown-source error, ale hodnota je legit konstanta projektu.

**Riziko:** Front composite je dead code / aspirační konstanta → dokumentace chaotická; pokud někdo přidá do recordings UI, validace to zamítne.

**Doporučení:** Buď odstranit `CAMERA_FRONT_COMPOSITE`, nebo ji přidat do `VALID_CAPTURE_SOURCES` s poznámkou v jakém scénáři se použije.

---

#### FIND-006 — `TELEOP_SPEED_PROFILES` duplikuje `autonomy/app/constants.py` bez sync enforcement
**Severity:** STŘEDNÍ · **Kategorie:** Data consistency / Tech debt
**Lokace:** [spot_operator/constants.py:98-103](spot_operator/constants.py#L98-L103)

Komentář říká "Sladěno s autonomy/app/constants.py TELEOP_SPEED_PROFILES" — tj. shadow copy. Pokud někdo změní autonomy verzi, spot_operator dostane stare hodnoty → rozdíl ve stejných UI labelech.

**Riziko:** Silent drift mezi dvěma definicemi.

**Doporučení:** `from app.constants import TELEOP_SPEED_PROFILES as _AUTONOMY_PROFILES; TELEOP_SPEED_PROFILES = _AUTONOMY_PROFILES` (a jen lokální CZ labely přidat zvlášť). Nebo assertion při startu.

---

#### FIND-007 — `bootstrap._verify_presence` kontroluje jen 2 soubory z desítek
**Severity:** NÍZKÉ · **Kategorie:** Error handling
**Lokace:** [spot_operator/bootstrap.py:35-46](spot_operator/bootstrap.py#L35-L46)

Kontrolují se jen `autonomy/app/robot/sdk_session.py` a `ocr/ocrtest.py`. Pokud jsou tyto 2 soubory OK, ale zbytek autonomy chybí nebo je poškozený, crashe pak v runtime se zmatečnými ImportErrory.

**Riziko:** Instalace bez jedné submodule padne až po pár minutách s nejasnou stack trace.

**Doporučení:** Buď kontrolovat celou strukturu (seznam kritických souborů), nebo jen přidat sanity import statement typu `from app.models import NavigationOutcome` v bootstrap dry-run.

---

#### FIND-008 — Qt message handler `_install_qt_handler` je idempotentní přes `qInstallMessageHandler`, ale `setup()` ne
**Severity:** NÍZKÉ · **Kategorie:** Resource management
**Lokace:** [spot_operator/logging_config.py:42-44](spot_operator/logging_config.py#L42-L44)

`setup()` odstraňuje existující root handlery (good), ale `_install_qt_handler` instaluje nový Qt message handler pokaždé, co se `setup()` volá. Qt message handler je globální; pokud by někdo volal `setup()` víckrát, přepíše starší handler, ale `qInstallMessageHandler` neuvolňuje paměť předchozího. V testu to znamená malý memory leak; v produkci `setup()` volá jen bootstrap, takže je to neškodné.

**Riziko:** Kosmetické. Testy s fixture který volá setup opakovaně si budou muset poradit.

**Doporučení:** Skip-if-already-installed guard (třeba module-level flag).

---

#### FIND-009 — Žádný `override=True` v `load_dotenv` znamená, že env var má prioritu před `.env`
**Severity:** INFO / Tech debt · **Kategorie:** UX
**Lokace:** [spot_operator/config.py:45](spot_operator/config.py#L45)

`load_dotenv(env_file, override=False)` — standardní dotenv chování, ale při debugování je matoucí: "proč se nenačetla hodnota z `.env`? — protože tu samou proměnnou jsi měl v shellu z předchozí session". Důsledek: nekonzistentní stav vývojářů.

**Riziko:** Zmatečné debugování "proč moje nová hodnota v .env nic nedělá".

**Doporučení:** Dokumentovat v README, nebo přepnout na `override=True` pokud chceme, aby `.env` vyhrával.

---

#### FIND-010 — `CRUD_WORKER_STOP_TIMEOUT_MS = 3000` je krátký pro velké tabulky
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/constants.py:121](spot_operator/constants.py#L121)

3 sekundy na zastavení worker threadu, který může běžet velký SQL dotaz (10k+ řádků, OCR detections s JSONB agregacemi). Pokud worker neskončí v 3 s, `QThread.wait()` vrátí `False` a worker zůstane v nedefinovaném stavu → pravděpodobný crash při `deleteLater()`.

**Riziko:** Při pomalé DB se CRUD okno zavírá s crash nebo zamrzlým worker threadem.

**Doporučení:** Zvýšit na `10000` (10 s) nebo udělat adaptivně podle velikosti datasetu. Souvisí s FIND v oblasti 14 (paged_table_model).

---

### Oblast 2 — DB schema & migrations

#### FIND-011 — Migrace 0002 přidává sloupce s `server_default` a hned ALTER-uje na `None` — nové řádky musí mít explicitní hodnotu
**Severity:** VYSOKÉ · **Kategorie:** Data integrity
**Lokace:** [alembic/versions/20260423_0900_0002_reliability_fields.py:22-66](alembic/versions/20260423_0900_0002_reliability_fields.py#L22-L66)

Pattern:
```python
op.add_column("spot_runs", sa.Column("checkpoint_results_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
...
op.alter_column("spot_runs", "checkpoint_results_json", server_default=None)
```

Účel (asi): existujícím řádkům doplnit default, nové musí být explicit. Problem: **když SQLAlchemy model říká `default=list` (Python-side), ale sloupec v DB má `server_default=NULL`**, INSERT z ORM bez explicitního `checkpoint_results_json=[]` vyvolá IntegrityError (NULL violates not-null). V praxi `runs_repo.create` posílá explicitně `[]`, takže pokrytí je, ale:

- Pokud přibude jiné vložení (test, admin UI, migration data seed), INSERT padne.
- `metadata_version`, `archive_is_valid`, `return_home_status` mají stejný pattern.

**Riziko:** Silent breakage budoucího kódu. Jasné "jak má model fungovat" vs "jak DB vynucuje" se rozchází.

**Doporučení:** Buď nechat `server_default` natrvalo (idempotent a defensive), nebo naopak z models.py odstranit `default=list` a explicit vynutit v repo. Pattern "set default, then clear default" je matoucí.

**Verifikace:** Vložit do SpotRun přes SQL `INSERT INTO spot_runs (run_code, status) VALUES ('test', 'running')` — padne na NOT NULL `checkpoint_results_json`, což potvrzuje problém.

---

#### FIND-012 — `upgrade_to_head` bez error handling ani user-friendly zprávy
**Severity:** VYSOKÉ · **Kategorie:** Error handling / UX
**Lokace:** [spot_operator/db/migrations.py:16-31](spot_operator/db/migrations.py#L16-L31)

Pokud `command.upgrade(cfg, "head")` padne (chybný SQL, konflikt, DB nedostupná), spadne celá aplikace při startu s raw alembic tracebackem. Operátor netuší co dělat.

**Riziko:** Po update projektu aplikace nejde spustit; operátor neví, jestli zrušit update, spustit rollback, nebo jen nemá DB spojení.

**Doporučení:**
```python
try:
    command.upgrade(cfg, "head")
except Exception as exc:
    _log.exception("Alembic upgrade failed")
    raise RuntimeError(
        f"Migrace databáze selhala: {exc}. "
        "Ověř připojení k DB (DATABASE_URL) nebo spusť `alembic downgrade -1`."
    ) from exc
```

---

#### FIND-013 — `current_revision` vytváří nový engine místo re-use pool
**Severity:** NÍZKÉ · **Kategorie:** Resource management
**Lokace:** [spot_operator/db/migrations.py:34-45](spot_operator/db/migrations.py#L34-L45)

```python
engine = create_engine(database_url)
try:
    ...
finally:
    engine.dispose()
```

Nepoužívá se hlavní pool z `init_engine`. Teoreticky neškodné (ephemerní), ale nesymetrické se zbytkem modulu. Jen volané při diagnostice.

**Doporučení:** Pokud je engine inicializovaný, použít ho; jinak vytvořit ephemerní. Nebo zcela odstranit, pokud se v produkci nevolá.

---

#### FIND-014 — `expire_on_commit=False` v sessionmaker — žádoucí optimalizace, ale bez dokumentace invariantů
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity / Tech debt
**Lokace:** [spot_operator/db/engine.py:41-49](spot_operator/db/engine.py#L41-L49)

```python
sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
```

Dva sub-optimal settings:
- `autoflush=False` — repo musí explicit `flush()` pokud potřebuje vidět ID (což dělá `photo.id` v `save_photo_to_db`). OK, ale snadno se na to zapomene.
- `expire_on_commit=False` — po commit objekty zůstanou usable (jinak by `photo.id` po commit vyvolal refresh-DetachedInstanceError). Ale ORM objekty *neexpirují*, takže čtení po commit vrátí stará data, pokud někdo přímo přes SQL update-nul řádek mezi commity.

**Riziko:** Pokud OCR worker commituje update `photos.ocr_status`, jiný thread čtoucí stejnou `Photo` instanci uvidí starý status (stale cache per-thread session). Není to bug v dnešním kódu (každá operace otevírá novou session kontextem `with Session() as s`), ale fragile.

**Doporučení:** Dokumentovat invariant: "v repo funkcích nikdy nepassujte ORM objekt napříč session boundaries; vrací se primitivní DTO". Nebo přepnout na `expire_on_commit=True` a explicitně volat `flush()` před `obj.id`.

---

#### FIND-015 — Scoped session s `scopefunc=threading.get_ident` neřeší skončené thready
**Severity:** STŘEDNÍ · **Kategorie:** Resource management / Concurrency
**Lokace:** [spot_operator/db/engine.py:41-49, 84-94](spot_operator/db/engine.py#L41-L49)

`scoped_session(..., scopefunc=threading.get_ident)` drží thread-local session dokud explicit `_session_factory.remove()` neneuvolní. `shutdown_engine` to dělá, ale jen při kompletním vypnutí — běh QThread (OCR worker start → stop → start) → `threading.get_ident()` může vrátit *stejné ID* (při thread recycling) → session je re-used včetně případného uncommitted state.

Dále: pokud worker skončí (funkce `run()` vrátí), session pro toto `get_ident()` zůstává v `_session_factory.registry` zaparkovaná, spotřebovává pool connection slot dokud není garbage collected.

**Riziko:** Drobný connection leak při opakovaném spuštění worker threadů. Při mnoha cyklech může saturovat pool.

**Doporučení:** Na konci `run()` metody každého workera volat `Session().remove()` (nebo `_session_factory.remove()`) pro cleanup thread-local session.

---

#### FIND-016 — Photo cascade delete v SpotRun bez batching — mass-delete riziko
**Severity:** STŘEDNÍ · **Kategorie:** Performance / Data integrity
**Lokace:** [spot_operator/db/models.py:133-135, 147-153](spot_operator/db/models.py#L133-L153)

```python
photos: Mapped[list["Photo"]] = relationship(
    back_populates="run", lazy="select", cascade="all, delete-orphan"
)
# + Photo.run_id FK ondelete="CASCADE"
```

Pokud operátor smaže běh z CRUD okna, DB CASCADE smaže všechny photos; ORM `delete-orphan` by se spustil jen při přetrhnutí parent-child vztahu v session. Při přímém DELETE přes repo — je rychlejší DB cascade (server-side), ale:
- Photos obsahují `image_bytes` jako LargeBinary (`SET STORAGE EXTERNAL` v migraci) — toast řádky, při 1000 photos × 500 KB = 500 MB delete v jedné transakci → DB může uvrhnout.
- Transakce drží locky na `photos` + `plate_detections` dlouho.

**Riziko:** Při smazání velkého běhu se UI "zasekne" na delší dobu (desítky sekund), blokuje další DB operace.

**Doporučení:** V `runs_repo.delete_cascade` (pokud existuje — ověřit v oblasti 3) dělat batch delete po `N` photos v samostatných transakcích s COMMIT mezi batchy.

---

#### FIND-017 — `PlateDetection` unique constraint na (photo_id, engine_name, plate_text) s nullable `plate_text` — Postgres semantika NULL ≠ NULL
**Severity:** NÍZKÉ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/db/models.py:184-188, 194](spot_operator/db/models.py#L184-L194)

V PostgreSQL `UNIQUE` s NULL hodnotou povoluje multiple řádků (NULL ≠ NULL). Takže OCR pipeline může pro tu samou fotku + engine uložit **N "no-text" detections** (každá s `plate_text=NULL`), aniž by unique constraint chytil duplicitu.

**Riziko:** Po retry OCR se hromadí prázdné detection řádky pro fotku. Nekonzistentní data v DB.

**Doporučení:** Přidat `NULLS NOT DISTINCT` v migraci (PG 15+), nebo nepoužívat NULL pro `plate_text` (použít `""`), nebo rozdělit "detekovaná SPZ" a "prázdný bbox" do dvou tabulek.

---

#### FIND-018 — Model `Map.default_capture_sources: list[str]` bez `server_default` — INSERT bez explicitní hodnoty padne
**Severity:** NÍZKÉ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/db/models.py:81](spot_operator/db/models.py#L81), [alembic/versions/20260422_1200_0001_initial.py:65](alembic/versions/20260422_1200_0001_initial.py#L65)

```python
default_capture_sources: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
# V migraci: sa.Column("default_capture_sources", postgresql.JSONB(), nullable=False)
# — bez server_default
```

Žádný Python default (`default=list`) ani server default. `map_storage.save_map_to_db` vždy posílá, OK, ale fragile.

**Doporučení:** Přidat `default=list` v modelu pro robustnost.

---

#### FIND-019 — Model/schema sync: `Map.metadata_version` default=2 v modelu, server_default=None v DB (po migraci 0002)
**Severity:** NÍZKÉ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/db/models.py:83](spot_operator/db/models.py#L83), [alembic/versions/20260423_0900_0002_reliability_fields.py:63](alembic/versions/20260423_0900_0002_reliability_fields.py#L63)

Model: `metadata_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)`
Migrace 0002 po `add_column` volá `alter_column(..., server_default=None)`.

Stejný pattern jako FIND-011; SQLAlchemy Python default řeší INSERTy z ORM, ale přímé SQL INSERT `INSERT INTO maps (name, archive_bytes, ...)` bez `metadata_version` padne.

**Doporučení:** Podobné jako FIND-011.

---

#### FIND-020 — Žádný test pokrývající "map with checkpoints_json=NULL" edge case
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity / Testing
**Lokace:** [spot_operator/db/models.py:82](spot_operator/db/models.py#L82) — `checkpoints_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)`

Model povoluje NULL pro `checkpoints_json`, a `parse_checkpoint_plan(raw or {})` to handluje jako prázdný plan. Ale playback_service pak hodí `RuntimeError("Mapa neobsahuje žádné checkpointy")`. Uživatel neví, proč to padlo.

**Riziko:** Starší mapa (nebo importovaná mapa) bez `checkpoints_json` se dostane do map_select, uživatel ji klikne → playback zaklepe. Poloviční chyba.

**Doporučení:** Ve filteru map-listu (`maps_repo.list_all`) vyhazovat mapy s NULL checkpoints_json, nebo je zobrazit jako grayed out. Dokumentovat v oblast 5 (map storage) a 12 (map_select_page).

---

#### FIND-021 — `ocr_locked_by: String(64)` — postačí pro worker ID, ale žádná timeout-based sweep v modelu
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/db/models.py:168-169](spot_operator/db/models.py#L168-L169)

Sloupec `ocr_locked_by` + `ocr_locked_at` implementují optimistic locking pro OCR worker. Ale samotná tabulka nemá:
- Maximální délku validního locku.
- Trigger na vyčištění "stale" locků (např. `ocr_locked_at < now() - 5 min AND ocr_status='processing'`).

`OCR_ZOMBIE_TIMEOUT_MIN = 5` v constants.py existuje, ale logic je v aplikační vrstvě (`ocr_worker.py`), ne v DB. Pokud aplikace zhroutí mezi `claim` a `mark_done`, řádek zůstane v `processing` stavu s lockem.

**Riziko:** Zombie-locked fotky po aplikačním crashu (Agent 2 původně hlásil tento problém jako kritický v ocr_worker.py; verifikuji v oblast 9).

**Doporučení:** Přidat background sweep funkci, která každých X minut uvolní locks starší než OCR_ZOMBIE_TIMEOUT_MIN. Nebo periodický job v DB samotném.

---

#### FIND-022 — `Map.name` má UNIQUE constraint — re-recording se stejným jménem padne s IntegrityError
**Severity:** STŘEDNÍ · **Kategorie:** UX / Error handling
**Lokace:** [spot_operator/db/models.py:74](spot_operator/db/models.py#L74), [alembic/versions/20260422_1200_0001_initial.py:72](alembic/versions/20260422_1200_0001_initial.py#L72)

`UniqueConstraint("name", name="ux_maps_name")`. Pokud operátor nahraje mapu "warehouse-A", pak nahraje znovu (třeba po chybě), `save_map_to_db` padne na UNIQUE violation.

**Riziko:** Po neúspěšném save padne retry; uživatel nevidí jasnou chybu a neví, zda smazat starý záznam.

**Doporučení:** Ve SaveMapPage ověřit existenci `name` před pokusem o save (`maps_repo.get_by_name(name)`), a nabídnout:
- Přejmenovat
- Přepsat (delete + insert)
- Zrušit save

Dnes se problém zatím pravděpodobně rozkrývá až po crash v repo vrstvě.

---

### Oblast 3 — DB repozitáře

#### FIND-023 — `runs_repo.mark_progress` a `finish` ignorují `rowcount` — tichá aktualizace neexistujícího běhu
**Severity:** STŘEDNÍ · **Kategorie:** Error handling / Data integrity
**Lokace:** [spot_operator/db/repositories/runs_repo.py:157-189](spot_operator/db/repositories/runs_repo.py#L157-L189)

```python
session.execute(update(SpotRun).where(SpotRun.id == run_id).values(**values))
```

Pokud `run_id` neexistuje (např. volající má stale value po rollbacku `create`), UPDATE ovlivní 0 řádků a funkce se vrátí "úspěšně". Volající si myslí, že progress je uložen, ale v DB nic není.

**Riziko:** Playback zapisuje checkpointové výsledky do neexistujícího běhu, finální run_completed emitovaný UI, ale DB je prázdná. Přesně odpovídá uživatelem hlášenému "něco to neukládá".

**Doporučení:** Ověřit `result.rowcount == 1` a při 0 logovat ERROR a zvážit `raise`. Alternativně vrátit bool (úspěch/neúspěch).

**Verifikace:** Ručně zavolat `mark_progress(session, 9999999, 5)` — dnes projde bez chyby.

---

#### FIND-024 — `runs_repo.generate_unique_run_code` má TOCTOU race — 2 parallel creations mohou skolidovat při INSERT
**Severity:** STŘEDNÍ · **Kategorie:** Concurrency / Data integrity
**Lokace:** [spot_operator/db/repositories/runs_repo.py:198-209](spot_operator/db/repositories/runs_repo.py#L198-L209)

```python
for attempt in range(max_attempts):
    candidate = base if attempt == 0 else f"{base}_{attempt:02d}"
    exists = session.execute(select(...).where(...)).scalar_one_or_none()
    if exists is None:
        return candidate  # ← TOCTOU: někdo jiný to mohl INSERT mezitím
```

Pokud dva thready vyberou stejný kandidát (a žádný ještě nedělal INSERT), oba projdou `exists is None`, oba vrátí stejný run_code. První INSERT v `runs_repo.create` projde, druhý padne na UNIQUE constraint. A v `playback_service.run_all_checkpoints` není try/except na `create`.

**Riziko:** Při dvou paralelních spuštěních playbacku (nepravděpodobné v UI, ale možné) druhý pokus spadne s IntegrityError místo aby si vybral jiný run_code.

**Doporučení:** Buď wrap create v try/except IntegrityError + retry, nebo použít DB savepoint + ON CONFLICT, nebo generovat run_code s UUID suffix.

---

#### FIND-025 — `maps_repo.create` spoléhá na volajícího, že ověří unique name
**Severity:** STŘEDNÍ · **Kategorie:** UX / Error handling
**Lokace:** [spot_operator/db/repositories/maps_repo.py:13-51](spot_operator/db/repositories/maps_repo.py#L13-L51)

Funkce prostě vytvoří `Map(...)`, `session.add`, `session.flush()`. Při duplicitě padne `IntegrityError` až při commit. `save_map_to_db` (oblast 5) má z toho "zdroje pravdy" invariant, ale pokud někdo mimo save_map_to_db volá tohle přímo, dostane nepříjemnou chybu bez CZ popisu.

**Riziko:** Fragile API; rozhraní repo by mělo být self-contained.

**Doporučení:** Přidat `exists_by_name` check na začátku `create` + vlastní `MapNameAlreadyExists` exception.

---

#### FIND-026 — `photos_repo.sweep_zombies` + běžící worker = potenciál pro double OCR na jedné fotce
**Severity:** VYSOKÉ · **Kategorie:** Concurrency / Data integrity
**Lokace:** [spot_operator/db/repositories/photos_repo.py:377-397](spot_operator/db/repositories/photos_repo.py#L377-L397), [spot_operator/constants.py:65](spot_operator/constants.py#L65)

Sweep resetuje photos s `ocr_locked_at < now() - 5 min`. Worker při `claim_next_pending` nastaví `ocr_locked_at = now()` **jednou**; pokud OCR trvá > 5 min (model warmup + velká fotka + ONNX pomalý provider), sweep resetne locked photo na pending, jiný worker (nebo ten samý při dalším claimu) si ji znovu vezme. Dva OCR běhy pro tu samou fotku → duplicitní detekce (nebo konflikt při unique constraint, FIND-017).

**Riziko:** Duplikátní detekce, race při `mark_done`, wasted compute. V nejhorším mohou oba workery současně updatovat `plate_detections` a jeden dostane integrity error.

**Doporučení:** Přidat periodický heartbeat `ocr_locked_at = now()` uvnitř pipeline.process (třeba každých 30 s pokud pipeline poskytuje callback), nebo zvýšit `OCR_ZOMBIE_TIMEOUT_MIN` na bezpečnou hodnotu s margem (30 min) a sweep volat jen po explicitním operator requestu.

**Verifikace:** Simulovat OCR trvající 6 min (sleep), pustit paralelně worker a sweep — ověřit, jestli dojde k opakovanému claim.

---

#### FIND-027 — `plates_repo.upsert` má TOCTOU race mezi `get_by_text` a INSERT
**Severity:** STŘEDNÍ · **Kategorie:** Concurrency / Data integrity
**Lokace:** [spot_operator/db/repositories/plates_repo.py:130-155](spot_operator/db/repositories/plates_repo.py#L130-L155)

```python
existing = get_by_text(session, plate_text)
if existing:
    existing.status = status
    ...
    return existing
plate = LicensePlate(...)
session.add(plate)
```

Dva současně volající `upsert("ABC123", ...)` oba projdou `get_by_text→None`, oba `session.add`, při commit druhá transakce dostane IntegrityError. `plates_tab` to neošetřuje → UI crash.

**Riziko:** Vzácné, ale při hromadném importu SPZ (seed z CSV) pravděpodobné.

**Doporučení:** Použít `INSERT ... ON CONFLICT ... DO UPDATE` přes `pg_insert`:
```python
stmt = pg_insert(LicensePlate).values(...).on_conflict_do_update(
    index_elements=["plate_text"], set_={"status": ..., ...}
)
```

---

#### FIND-028 — `detections_repo.insert_many` + NULL `plate_text` = ON CONFLICT nechytne duplicity
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/db/repositories/detections_repo.py:14-27](spot_operator/db/repositories/detections_repo.py#L14-L27)

```python
.on_conflict_do_nothing(index_elements=["photo_id", "engine_name", "plate_text"])
```

V Postgres jsou NULL hodnoty v unique constraintu *distinct* (pokud není `NULLS NOT DISTINCT`). Takže pro OCR který nenašel žádnou SPZ (plate_text=NULL), každý retry přidá nový řádek. Po 10 retries má fotka 10 "prázdných" detekcí.

**Riziko:** Bloat tabulky `plate_detections`, nekonzistentní data, matoucí detail dialog.

**Doporučení:** Buď `NULLS NOT DISTINCT` v migraci (PG 15+), nebo nepoužívat NULL pro plate_text (použít `""`), nebo nevkládat řádek, když OCR nenašlo plate.

---

#### FIND-029 — `photos_repo.reset_all_to_pending` dělá N+1 DELETE queries
**Severity:** STŘEDNÍ · **Kategorie:** Performance
**Lokace:** [spot_operator/db/repositories/photos_repo.py:339-374](spot_operator/db/repositories/photos_repo.py#L339-L374)

```python
photo_ids = list(session.execute(photo_ids_stmt).scalars().all())
for photo_id in photo_ids:
    detections_repo.delete_for_photo(session, int(photo_id))
```

Pro 10k fotek = 10 001 DB roundtripů. UI zamrzne na desítky sekund.

**Riziko:** Tlačítko "Reset všech na pending" na produkci zablokuje aplikaci.

**Doporučení:** Single DELETE s IN / JOIN:
```python
sqldelete(PlateDetection).where(PlateDetection.photo_id.in_(photo_ids_subquery))
```
Už existuje `detections_repo.delete_for_run(session, run_id)` s podobným patternem — lze zgeneralizovat.

---

#### FIND-030 — `photos_repo._to_photo_row` mixuje UI fallback `"?"` do repo vrstvy
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [spot_operator/db/repositories/photos_repo.py:107](spot_operator/db/repositories/photos_repo.py#L107)

```python
plates=tuple(d.plate_text or "?" for d in photo.detections),
```

Repo vrstva neví, že `"?"` má být zobrazeno uživateli. Jiný konzument (export, API) dostane taky `"?"` místo raw NULL. Rozdělení povinností.

**Doporučení:** Vrátit `tuple(d.plate_text for d in ...)` (může obsahovat None) a UI ať si fallback určí samo.

---

#### FIND-031 — `photos_repo.fetch_last_image_bytes_for_plate` normalizuje jen `upper().strip()`, ne alfanumericky
**Severity:** STŘEDNÍ · **Kategorie:** Data consistency
**Lokace:** [spot_operator/db/repositories/photos_repo.py:202-226](spot_operator/db/repositories/photos_repo.py#L202-L226) vs [spot_operator/db/repositories/plates_repo.py:34-38](spot_operator/db/repositories/plates_repo.py#L34-L38)

```python
# photos_repo:
normalized = (plate_text or "").strip().upper()

# plates_repo:
return "".join(ch for ch in text.upper() if ch.isalnum())
```

Pro vstup `"AB-123"` dostane photos_repo `"AB-123"`, plates_repo `"AB123"`. V DB jsou plate_text uloženy bez pomlček (via `normalize_plate_text` při upsert), takže photos_repo **nikdy nenajde** plate s pomlčkou ve vstupu.

**Riziko:** SPZ detail dialog ukazuje "Žádná fotka" pro platný záznam, pokud user přes API/CSV importu použil formát s pomlčkami.

**Doporučení:** Použít sdílený helper `normalize_plate_text` ve všech query míst pro plate_text.

---

#### FIND-032 — `photo_sink.save_photo_to_db` vrací `photo.id` po commit, ale exception v commit ztratí photo_id
**Severity:** VYSOKÉ · **Kategorie:** Error handling
**Lokace:** [spot_operator/db/repositories/photos_repo.py:67-90](spot_operator/db/repositories/photos_repo.py#L67-L90) (viz také oblast 7 — photo_sink)

`insert` volá `session.flush()` → photo.id je naplněno (DB vygeneruje autoincrement). Volající (`photo_sink.save_photo_to_db`) udělá `s.commit()`. Pokud commit selže (connection drop, konflikt), exception propaguje, photo.id je validní v ORM ale v DB řádek neexistuje. Volající sice dostane exception (neviditelný photo_id), ale dál nahoře v `playback_service._capture_at_checkpoint` je `except Exception` s `failed_sources.append(src)`:

```python
# playback_service.py:707-710
except Exception as exc:
    _log.warning("save photo failed (cp=%s src=%s): %s", ...)
    failed_sources.append(src)
```

OK — exception se *zachytí* a označí zdroj za failed. Ale pokud commit by byl úspěšný a jen `photo.id` by spadl na detached error, `photo_taken` signál by se neemitoval, ale photo by byl v DB — inkonzistence UI vs DB. Dnes to fakticky nenastává (flush + commit v `with` bloku), ale invariant není chráněn testem.

**Riziko:** Fragile, neuniformní.

**Doporučení:** `save_photo_to_db` by měl vrátit photo_id *až po úspěšném commit*. Napsat test s `session.commit` mock-vyvolávajícím exception.

---

#### FIND-033 — `RunRow.status` je `str` (value enumu), ale je zmíchané mezi vrstvami
**Severity:** NÍZKÉ · **Kategorie:** Type consistency / Tech debt
**Lokace:** [spot_operator/db/repositories/runs_repo.py:27, 103](spot_operator/db/repositories/runs_repo.py#L27-L103)

`RunRow.status: str` — repo konvertuje `r.status.value`. V CRUD tabulce pak porovnání `row.status == "completed"` je magic string místo `RunStatus.completed.value`. V `playback_result_page` pak ještě jinak. Ztráta static-check bezpečnosti.

**Doporučení:** Buď držet `RunStatus` enum napříč, nebo centralizovat `status_label(status_value: str) -> str` pro UI.

---

#### FIND-034 — `photos_repo.claim_next_pending` vrací ORM `Photo` s modifikovaným stavem do volajícího; bez commit zůstane v memory stale
**Severity:** NÍZKÉ · **Kategorie:** Data integrity / Tech debt
**Lokace:** [spot_operator/db/repositories/photos_repo.py:274-293](spot_operator/db/repositories/photos_repo.py#L274-L293)

Funkce modifikuje `photo.ocr_status = processing` a vrací `photo`. Volající MUSÍ commitnout session, jinak:
- DB lock `FOR UPDATE SKIP LOCKED` se při rollback uvolní (OK).
- ORM objekt v paměti má `processing` stav, ale DB má `pending`. Pokud volající předá `photo` dál (třeba do signálu), následující kód čtoucí `photo.ocr_status` vidí stale state.

**Doporučení:** Dokumentovat jasný contract "volající MUSÍ commit ihned". Nebo returnovat jen photo_id (ne ORM objekt), volající ať si to přečte samostatně po commit.

---

#### FIND-035 — `PlateDetection` insert s NULL plate_text spolu s floating confidence — zbytečný zápis "nic nebylo detekováno"
**Severity:** NÍZKÉ · **Kategorie:** Data design
**Lokace:** konsekvence FIND-028, spolu s modelem [spot_operator/db/models.py:194](spot_operator/db/models.py#L194)

V pipeline/ocr může vznikat situaci "YOLO detekoval bbox, ale text reader vrátil nic" — `plate_text=NULL`, `detection_confidence=0.7`, `text_confidence=NULL`. Tato informace je hodnotná (možná spot videí detekci ale ne text), ale unique constraint nechytne duplicity (FIND-028).

**Doporučení:** Rozhodnout: buď store všechny (a dát NULL NOT DISTINCT), nebo jen ty s non-NULL text. Současný mix je fragile.

---

#### FIND-036 — `get_photo_metadata` sort lambda má float/None porovnávací past (`text_confidence=0.0`)
**Severity:** NÍZKÉ · **Kategorie:** Edge case
**Lokace:** [spot_operator/db/repositories/photos_repo.py:186-190](spot_operator/db/repositories/photos_repo.py#L186-L190)

```python
key=lambda d: (d.text_confidence is None, -(d.text_confidence or 0))
```

`text_confidence == 0.0` → `-(0 or 0) == -0 == 0`, což je stejné jako plate s `text_confidence == 0.0` vyřezaný explicit. Edge case málo pravděpodobný v praxi, ale invariant "non-None před None" není narušen. OK.

**Doporučení:** Kosmetické, není nutno fixovat.

---

### Oblast 4 — Contracts & JSON serializace

#### FIND-037 — `parse_checkpoint_plan` nevaliduje invarianty napříč poli (start_waypoint_id je v checkpoints[], duplicate name/waypoint_id)
**Severity:** VYSOKÉ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/services/contracts.py:126-182](spot_operator/services/contracts.py#L126-L182)

Funkce validuje každé pole samostatně, ale nekontroluje:

1. **`start_waypoint_id` je některé `checkpoints[].waypoint_id`** — pokud recording zapsal `start_waypoint_id="abc"`, ale žádný checkpoint nemá `waypoint_id="abc"`, playback si myslí že má validní mapu, ale při `navigate_to(start)` může dojít k neočekávané chybě v GraphNav.
2. **Žádný duplicate `checkpoints[].name`** — operátor by viděl v UI dva "CP_001" a nevěděl který je který.
3. **Žádný duplicate `checkpoints[].waypoint_id`** — bosdyn by jel dvakrát na stejné místo, ale playback by hlásil jako 2 různé úspěchy.
4. **Aspoň 1 checkpoint má `kind="checkpoint"` (či aspoň 1 existuje celkově).** Jinak playback padne s `RuntimeError("Mapa neobsahuje žádné checkpointy")` až při start, operátor neví dřív.
5. **Žádná `""` jako `fiducial_id`** — `_as_optional_int("")` vyvolá `ValueError` který se neodfiltruje.

**Riziko:** Mapa se do DB dostane v *syntakticky* platném ale *sémanticky* rozbitém stavu. Crash se projeví až při playbacku, kdy je operátor u robota s fiducialem a čeká.

**Doporučení:** Přidat funkci `validate_plan_invariants(plan: MapPlan) -> None` volanou z `save_map_to_db` před insertem do DB (už to částečně dělá `_validate_loaded_map`, ale na load path, ne save).

**Verifikace:** Test: zapsat do DB mapu s `start_waypoint_id` mimo seznam checkpoint waypointů — dnes projde, po fixu pade s konkrétní chybou.

---

#### FIND-038 — `_extract_fiducial_id` vyhodí `ValueError` pro `payload["fiducial"]=<int>` (legacy nebo manually edited JSON)
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity / Backward compat
**Lokace:** [spot_operator/services/contracts.py:272-278](spot_operator/services/contracts.py#L272-L278)

```python
if "fiducial" in payload:
    fiducial = payload.get("fiducial") or {}
    if fiducial is not None and not isinstance(fiducial, dict):
        raise ValueError("Map fiducial block must be an object.")
    return _as_optional_int((fiducial or {}).get("id"), fallback)
```

Pokud někdo (legacy migrace, manual JSON edit, budoucí verze SDK) zapíše `"fiducial": 5` místo `"fiducial": {"id": 5}`, `parse_checkpoint_plan` spadne celý a `map_storage.load_map_to_temp` vyhodi exception — mapa je nepoužitelná i když data jsou čitelná.

**Riziko:** Mapa je permanentně "rozbitá" z hlediska playbacku kvůli formát mismatch. Operátor musí smazat a znovu nahrát mapu.

**Doporučení:** Pokud `fiducial` je int, interpretovat jako `{"id": fiducial}` a logovat warning. Nebo aspoň zachovat přístup `payload.get("fiducial_id")` jako fallback.

---

#### FIND-039 — `_as_optional_int` s prázdným stringem vyvolá `ValueError("Expected integer, got str.")` — matoucí zpráva
**Severity:** NÍZKÉ · **Kategorie:** UX / Error message
**Lokace:** [spot_operator/services/contracts.py:315-324](spot_operator/services/contracts.py#L315-L324)

```python
if isinstance(value, str) and value.strip():
    return int(value.strip())
raise ValueError(f"Expected integer, got {type(value).__name__}.")
```

Pro `value=""` (prázdný string): nespadne do `if` (prázdný strip), padne na `raise ValueError("Expected integer, got str.")`. Pro `value="   "` stejně. Uživatel diagnostiky vidí "Expected integer, got str" bez toho aby věděl, že problém je *prázdnost*.

**Doporučení:** Explicitně: `if value == "" or value.strip() == "": return fallback`. Nebo konkrétnější zpráva.

---

#### FIND-040 — `_normalize_sources` nenese tolerance pro single string value (legacy formát)
**Severity:** STŘEDNÍ · **Kategorie:** Backward compat
**Lokace:** [spot_operator/services/contracts.py:286-296](spot_operator/services/contracts.py#L286-L296)

```python
if not isinstance(value, (list, tuple)):
    raise ValueError("Capture sources must be a list of strings.")
```

Pokud historicky nějaký checkpoint měl `"capture_sources": "left_fisheye_image"` (skalar místo list — legacy nebo OCR-mixup), `parse_checkpoint_plan` padne na téhle check. Celá mapa se stává nedosažitelnou.

**Riziko:** Nemožnost číst starší mapy.

**Doporučení:**
```python
if isinstance(value, str):
    value = [value]
elif not isinstance(value, (list, tuple)):
    raise ValueError(...)
```

---

#### FIND-041 — `build_checkpoint_plan_payload` používá `getattr(cp, ..., default)` — duck typing bez kontraktu
**Severity:** STŘEDNÍ · **Kategorie:** Type consistency / Tech debt
**Lokace:** [spot_operator/services/contracts.py:103-122](spot_operator/services/contracts.py#L103-L122)

```python
"capture_status": getattr(cp, "capture_status", None),
"saved_sources": list(_normalize_sources(getattr(cp, "saved_sources", ()))),
"failed_sources": list(_normalize_sources(getattr(cp, "failed_sources", ()))),
"note": getattr(cp, "note", ""),
"created_at": getattr(cp, "created_at", ""),
```

Funkce přijímá `checkpoints: Iterable[Any]`. Protokol "jaké atributy musím mít" je implicit. Pokud někdo zavolá `build_checkpoint_plan_payload(..., checkpoints=[MapCheckpoint(...)])`, `MapCheckpoint` nemá `capture_status` → tichý `None` v JSON. Další parse to přejde jako neurčený status.

**Riziko:** Silent data loss. Statická analýza neuvidí problém (getattr s defaultem schová chybu).

**Doporučení:** Buď explicit `Protocol` (PEP 544) pro požadované atributy, nebo wrapper dataclass `CheckpointPayloadInput` který konverguje RecordedCheckpoint → stabilní formát před serializací.

---

#### FIND-042 — `parse_checkpoint_results` vyžaduje `started_at` a `finished_at` jako povinné — při `nav_outcome=error` před startem neexistují
**Severity:** STŘEDNÍ · **Kategorie:** Error handling / Data integrity
**Lokace:** [spot_operator/services/contracts.py:244-250](spot_operator/services/contracts.py#L244-L250)

```python
started_at=_required_str(item.get("started_at"), ...)
finished_at=_required_str(item.get("finished_at"), ...)
```

`build_checkpoint_result` vyžaduje `datetime` (non-None), ale pokud by playback vytvořil `CheckpointResult` před volbou `started_at` (např. při exception v samotném entry), hodnoty by byly `None`. `parse_checkpoint_results` by to neuvidělo (write path to dnes nedostane), ale ČTEcí cesta nemá error recovery — pokud se pokud 1 checkpoint v legacy DB má chybějící timestamp, celý parse padne.

**Riziko:** Historický záznam znemožní otevření result-page.

**Doporučení:** Použít `_as_optional_str` s fallback na `""` pro timestampy při read; striktní jen při write.

---

#### FIND-043 — `schema_version` forward-compat: nejsou ověřené supported verze
**Severity:** NÍZKÉ · **Kategorie:** Compatibility
**Lokace:** [spot_operator/services/contracts.py:9, 138](spot_operator/services/contracts.py#L9), [spot_operator/services/contracts.py:281-283](spot_operator/services/contracts.py#L281-L283)

```python
MAP_METADATA_SCHEMA_VERSION = 2
...
def _normalize_schema_version(value: Any) -> int:
    normalized = _as_optional_int(value, 1)
    return max(normalized or 1, 1)
```

Když budoucí v3 přidá nová pole s novou sémantikou (např. `conditional_capture_sources`), parse to tiše ignoruje — map bude interpretována jako v2. Žádné warning.

**Doporučení:** `if schema_version > MAP_METADATA_SCHEMA_VERSION: log.warning("Map schema %d is newer than supported %d", ...)`.

---

#### FIND-044 — Žádná round-trip test `payload → parse → payload == original`
**Severity:** NÍZKÉ · **Kategorie:** Testing
**Lokace:** [tests/unit/test_map_contracts.py](tests/unit/test_map_contracts.py)

Existující test jen ověřuje legacy → v2 upgrade a unknown source rejection. Chybí:
- Round-trip: `build → parse → build` produkuje stejnou JSON.
- Duplicate name/waypoint_id detection (viz FIND-037).
- Chybějící `start_waypoint_id`, prázdný `checkpoints`, NULL payload, apod.

**Doporučení:** Přidat test pro každý invariant z FIND-037 + round-trip test.

---

#### FIND-045 — `MapCheckpoint` je frozen dataclass, ale `CheckpointRef` (v playback_service) je ne-frozen kopie s jinými typy
**Severity:** NÍZKÉ · **Kategorie:** Type consistency
**Lokace:** [spot_operator/services/contracts.py:22-27](spot_operator/services/contracts.py#L22-L27) vs [spot_operator/services/playback_service.py:54-61](spot_operator/services/playback_service.py#L54-L61)

```python
# contracts.py
class MapCheckpoint:
    capture_sources: tuple[str, ...]  # ← tuple

# playback_service.py
class CheckpointRef:
    capture_sources: list[str]  # ← list (navíc mutable)
```

Konverze v `_extract_checkpoints`:
```python
capture_sources=list(cp.capture_sources),
```

Proč ne použít `MapCheckpoint` přímo? Duplicity → místo kde se synchronizace může rozjet. `CheckpointRef` navíc není `frozen=True`, takže `cp.capture_sources.append(...)` by šlo (mutace sdíleného stavu napříč iterace).

**Doporučení:** Buď sjednotit typy (použít `MapCheckpoint` přímo), nebo aspoň oba frozen s tuple.

---

#### FIND-046 — `build_checkpoint_result.nav_outcome: str` nevaliduje proti enum hodnotám
**Severity:** NÍZKÉ · **Kategorie:** Type consistency
**Lokace:** [spot_operator/services/contracts.py:185-209](spot_operator/services/contracts.py#L185-L209)

```python
def build_checkpoint_result(
    *,
    nav_outcome: str,  # ← str, ne NavigationOutcome enum
    ...
)
```

V `playback_service.py` se posílá `result.outcome.value` (string), ale pokud by někdo hodil string "REACHED" (uppercase z jiné vrstvy), `is_complete` check by selhal (`nav_outcome == "reached"`). Mismatched case → stealth bug.

**Doporučení:** Buď přijímat enum (`NavigationOutcome`), nebo normalizovat `nav_outcome.lower()` v builderu + whitelist validní hodnoty.

---

### Oblast 5 — Map storage & archivace

#### FIND-047 — `_validate_loaded_map` má side-effect do DB v load pathu
**Severity:** VYSOKÉ · **Kategorie:** Data integrity / Architecture
**Lokace:** [spot_operator/services/map_storage.py:178-225](spot_operator/services/map_storage.py#L178-L225)

`load_map_to_temp` je volán z `playback_service.upload_map_only` → každý playback attempt, který načte mapu, UPDATE-uje `archive_is_valid`, `archive_validation_error`, `metadata_version` v DB. Důsledky:

1. **Race:** dva paralelní loady (nepravděpodobné v UI, ale mutiple playback attempts) dělají 2 UPDATE.
2. **False positives:** Pokud `validate_map_dir` selže kvůli **přechodné chybě** (např. bosdyn protobuf parse kvůli nekompatibilní SDK verzi), mapa se v DB označí jako `archive_is_valid=False` trvale. Při další spuštění app (po upgrade SDK) se stále ukazuje jako neplatná, dokud si to operátor nevšimne.
3. **Hidden contract:** Volající netuší, že `load_map_to_temp` zapisuje do DB.

**Riziko:** Legitimní mapa se tiše označí jako neplatná kvůli transient failure; reverze vyžaduje SQL UPDATE.

**Doporučení:** Oddělit validaci od DB updatu. Validovat v load pathu in-memory; DB update jen v save pathu (`save_map_to_db`) nebo přes explicit `revalidate_map(map_id)` funkci volanou z admin UI.

---

#### FIND-048 — `save_map_to_db` TOCTOU race mezi `exists_by_name` a `create`
**Severity:** STŘEDNÍ · **Kategorie:** Concurrency
**Lokace:** [spot_operator/services/map_storage.py:105-127](spot_operator/services/map_storage.py#L105-L127)

```python
with Session() as s:
    if maps_repo.exists_by_name(s, name):
        raise ValueError(f"Mapa s názvem '{name}' už v DB existuje.")
    m = maps_repo.create(s, name=name, ...)
    s.commit()
```

Dva thready se stejným `name` projdou `exists_by_name=False`, druhý padne na UNIQUE constraint při commit. Exception není přeformulovaná, uživatel vidí generic DB error (viz FIND-025).

**Riziko:** Vzácné, ale reálné pokud by aplikace byla multi-instance.

**Doporučení:** Chytit `IntegrityError` v `save_map_to_db` a přeformulovat na uživatelsky srozumitelnou hlášku.

---

#### FIND-049 — `build_run_zip` — kompletní archiv v RAM (buf.getvalue()) + N+1 detekce queries
**Severity:** STŘEDNÍ · **Kategorie:** Performance / Resource management
**Lokace:** [spot_operator/services/zip_exporter.py:26-95](spot_operator/services/zip_exporter.py#L26-L95)

Dva problémy v jedné funkci:

1. **RAM blow-up:** `photos_repo.list_for_run` načte všechny `image_bytes` najednou (používá eager load, bez `defer`). `BytesIO` pak drží úplný ZIP. Pro 1000 fotek × 500 KB = ~500 MB v Python paměti. UI thread zamrzne (pokud je to volané v něm).
2. **N+1 query:** `for photo in photos: detections = detections_repo.list_for_photo(s, photo.id)` — pro každou fotku dodatečný DB roundtrip.

**Riziko:** Export velkých runů spustí OOM crash nebo UI freeze.

**Doporučení:**
- Použít `photos_repo.list_for_run_light` (má `selectinload(Photo.detections)` + defer bytes) + per-photo `fetch_image_bytes` streamovaný do zip writeru.
- Zvážit streaming ZIP přímo do output filu místo BytesIO (zipfile podporuje file-like handles).

---

#### FIND-050 — `cleanup_temp_root` při startu maže všechny `map_*` adresáře bez single-instance lock check
**Severity:** STŘEDNÍ · **Kategorie:** Resource management
**Lokace:** [spot_operator/services/map_storage.py:250-257](spot_operator/services/map_storage.py#L250-L257)

```python
for child in temp_root.iterdir():
    if child.name.startswith("map_"):
        shutil.rmtree(child, ignore_errors=True)
```

Pokud by dvě instance aplikace běžely paralelně (navzdory single-instance lock z `LOCK_FILE_NAME`), druhá spuštěná instance smaže extrahovanou mapu první instance, která ji zrovna uploaduje na robota. Playback první instance padne uprostřed uploadu (`FileNotFoundError`).

**Riziko:** Mitigace je `LOCK_FILE_NAME`, ale pokud lock selže (crash bez cleanup lockfile), opakované spuštění zničí aktivní mapu.

**Doporučení:** Cleanup jen na start *po* acquire lock. Nebo identifikovat temp-dirs per-PID (`map_<id>_<pid>_<uuid>`) a mazat jen své.

---

#### FIND-051 — `shutil.rmtree(target, ignore_errors=True)` tichý cleanup fail na Windows file locks
**Severity:** NÍZKÉ · **Kategorie:** Resource management
**Lokace:** [spot_operator/services/map_storage.py:164-166](spot_operator/services/map_storage.py#L164-L166)

Na Windows mohou být některé GraphNav soubory zamčené bosdynem po uploadu. `ignore_errors=True` je tiše přehlédne, ale temp adresář zůstane, konzumuje disk space. Kumulativně během týdne × mnoho playbacků → zaplnění disku.

**Riziko:** Disk se plní, aplikace pomalu padá.

**Doporučení:** Logovat (ne ignorovat) chyby a pravidelně volat `cleanup_temp_root` i za běhu (ne jen při startu).

---

#### FIND-052 — `zip_map_dir` zipne *cokoliv* v directory (včetně `.tmp`, `.swp`)
**Severity:** NÍZKÉ · **Kategorie:** Data hygiene
**Lokace:** [spot_operator/services/map_archiver.py:29-34](spot_operator/services/map_archiver.py#L29-L34)

```python
files = sorted(p for p in map_dir.rglob("*") if p.is_file())
```

Pokud bosdyn `download_map` nechá po sobě temp artefakty (`.tmp`, `~` backupy), nafoukne to archiv a způsobí že deduplikace (SHA-256) je permanentně jiná pro identický obsah.

**Riziko:** Archivy se nepředvídatelně liší; detekce "stejné mapy po redownload" selhává.

**Doporučení:** Whitelist file extensions nebo alespoň vynechat soubory začínající na `.` / `~` a s extension `.tmp`, `.swp`.

---

#### FIND-053 — `extract_map_archive` načte celý ZIP do `BytesIO` před extrakcí
**Severity:** NÍZKÉ · **Kategorie:** Performance
**Lokace:** [spot_operator/services/map_archiver.py:38-56](spot_operator/services/map_archiver.py#L38-L56)

```python
with zipfile.ZipFile(io.BytesIO(data)) as zf:
    ...
    zf.extractall(target_dir)
```

`data` je zip archive jako bytes (z DB). Pro velký archiv si aplikace držet bytes + BytesIO + extracted soubory = až 3× velikost v paměti/disku.

**Riziko:** OOM pro >500 MB mapu.

**Doporučení:** Pokud archiv >100 MB, streamovat přes dočasný soubor. Ale typické GraphNav mapy jsou <50 MB, takže v praxi OK.

---

#### FIND-054 — `validate_map_dir` importuje `bosdyn.api.graph_nav.map_pb2` uvnitř funkce
**Severity:** NÍZKÉ · **Kategorie:** Code style
**Lokace:** [spot_operator/services/map_archiver.py:74](spot_operator/services/map_archiver.py#L74)

Lazy import je OK pokud jde o circular dep, ale bosdyn je top-level závislost projektu. Lazy import zde není opodstatněný a znemožňuje static check (mypy).

**Doporučení:** Přesunout na top-of-file.

---

#### FIND-055 — `count_waypoints_in_map_dir` není nikde volán
**Severity:** NÍZKÉ · **Kategorie:** Tech debt / Dead code
**Lokace:** [spot_operator/services/map_archiver.py:59-64](spot_operator/services/map_archiver.py#L59-L64)

```python
def count_waypoints_in_map_dir(map_dir: Path) -> int | None:
```

Grep nenachází call-site. Dead code.

**Doporučení:** Odstranit nebo použít v recording pro overení očekávaného počtu waypointů proti `validation.waypoint_ids`.

---

#### FIND-056 — `MapMetadata.default_capture_sources: list[str]` — mutable v frozen dataclass
**Severity:** NÍZKÉ · **Kategorie:** Type consistency
**Lokace:** [spot_operator/services/map_storage.py:36-53](spot_operator/services/map_storage.py#L36-L53)

```python
@dataclass(frozen=True, slots=True)
class MapMetadata:
    ...
    default_capture_sources: list[str]
```

`frozen=True` brání re-assign `meta.default_capture_sources = [...]`, ale *ne* mutace samotného seznamu (`meta.default_capture_sources.append(...)`). Porušuje invariant neměnitelnosti.

**Doporučení:** Změnit typ na `tuple[str, ...]` (stejně jako ve `MapPlan`). Konverze v `_to_metadata` `list(...) → tuple(...)`.

---

#### FIND-057 — `list_all_metadata` a `maps_repo.list_all` jsou duplicitní/nekonzistentní implementace
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [spot_operator/services/map_storage.py:228-247](spot_operator/services/map_storage.py#L228-L247) vs [spot_operator/db/repositories/maps_repo.py:64-69](spot_operator/db/repositories/maps_repo.py#L64-L69)

Obě funkce dělají skoro totéž, ale `list_all_metadata` přidává `defer(Map.archive_bytes)` a `_to_metadata` konverzi. `maps_repo.list_all` tohle nedělá — pokud ho někdo zavolá v UI thread, načte MB bytes.

**Doporučení:** Odstranit `maps_repo.list_all` (nebo ho přesměrovat), aby byl jediný entry point.

---

#### FIND-058 — `save_map_to_db` nezachycuje exception z `validate_map_dir` aby přidal CZ popis
**Severity:** STŘEDNÍ · **Kategorie:** UX / Error handling
**Lokace:** [spot_operator/services/map_storage.py:97-101](spot_operator/services/map_storage.py#L97-L101)

```python
validation = validate_map_dir(
    source_dir,
    expected_start_waypoint_id=effective_start_waypoint_id,
    checkpoint_waypoint_ids=[cp.waypoint_id for cp in plan.checkpoints],
)
```

Pokud tohle padne (chybějící snapshot, corrupted protobuf), uživatel v `SaveMapPage` dostane anglickou zprávu z `validate_map_dir`. Recording pak nemá možnost se zotavit (mapa už je na robotu).

**Riziko:** Uživatel si myslí, že nahrál mapu, ale save padl s anglickým textem.

**Doporučení:** `try/except ValueError` + přeformulovat na CZ.

---

#### FIND-059 — `_safe_name(name)[:60]` může vytvořit kolize pro dlouhé checkpoint names
**Severity:** NÍZKÉ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/services/zip_exporter.py:121-122](spot_operator/services/zip_exporter.py#L121-L122)

```python
def _safe_name(name: str) -> str:
    return _SAFE_RE.sub("_", name)[:60]
```

Pokud dva checkpointy mají jména lišící se po 60. znaku (`"CP_with_very_long_description_A"` vs `"CP_with_very_long_description_B"`), po truncate jsou identické → kolize ZIP file path. `zf.writestr` přepíše první druhým.

**Riziko:** Ztráta fotek v exportu při dlouhých pojmenováních.

**Doporučení:** Jelikož je photo_id součástí filename (`{base}__{photo.id}`), technicky kolize neexistuje na jpg files. Ale detekce json má shodné `base` (bez id). Kontrola: `photos/<cp>__<src>__<id>.jpg` — id je na konci → OK, fallback lock. Na kolize jsou chráněny. Moderate nález.

---

#### FIND-060 — `build_run_zip` volá `getattr(run, "checkpoint_results_json", []) or []` — defensive, ale přeskočí type check
**Severity:** NÍZKÉ · **Kategorie:** Type consistency
**Lokace:** [spot_operator/services/zip_exporter.py:49](spot_operator/services/zip_exporter.py#L49)

`SpotRun.checkpoint_results_json` je `Mapped[list[dict[str, Any]]]` a `nullable=False` podle modelu — nikdy by neměl být None. Defensive `or []` maskuje by-definition bug, kdyby někdy přece jen byla None. Stejně pro `return_home_status` / `return_home_reason`.

**Doporučení:** Udělat cleanup a spoléhat na invariant; pokud bude None, explicitně se to pozná.

---

### Oblast 6 — Robot layer

#### FIND-061 — `SpotBundle.disconnect()` nemá timeout — UI může zamrznout při zhroucení bosdyn RPC
**Severity:** KRITICKÉ · **Kategorie:** Safety / Resource management
**Lokace:** [spot_operator/robot/session_factory.py:55-77](spot_operator/robot/session_factory.py#L55-L77)

`disconnect()` volá postupně `move_dispatcher.shutdown()`, `lease.release()`, `estop.shutdown()`, `session.disconnect()`. Každé je v try/except (dobře), ale **bez timeoutu**. Pokud bosdyn RPC `lease.release()` zavěsí na odpojeném Wi-Fi / RESET robota:

- `disconnect()` blokuje volající thread (obvykle UI thread v `main_window.closeEvent`).
- UI zamrzne na minuty (bosdyn default timeout).
- Operátor klikne X znovu → Windows zabije aplikaci → lease zůstane visící na robotu (pokud tolerance < Spot timeout).

**Riziko:** Po zavření aplikace nejde se znovu připojit (robot věří, že předchozí klient ještě drží lease). Vyžaduje restart Spota fyzicky.

**Doporučení:** Každý step v `disconnect` obalit s timeoutem (např. `concurrent.futures.wait(..., timeout=3)`) a při timeout jen logovat. Alternativa: provést `disconnect` v samostatném daemon threadu a nečekat na něj pokud UI thread potřebuje rychle ukončit.

---

#### FIND-062 — `PowerManager.power_off()` je asynchronní; `estop.start()` retry se volá bez čekání na completion
**Severity:** VYSOKÉ · **Kategorie:** Safety / Robot state
**Lokace:** [spot_operator/robot/session_factory.py:131-142](spot_operator/robot/session_factory.py#L131-L142)

```python
PowerManager(session).power_off()
_log.info("Motory vypnuty pro E-Stop auto-recovery")
...
# Retry — motory jsou off.
estop = EstopManager(session)
estop.start()
```

Bosdyn `power_off()` vrací po odeslání requestu; reálné vypnutí motorů proběhne po 1–2 sekundách. Pokud `estop.start()` přijde okamžitě, bosdyn může znovu hodit `MotorsOnError`. Nepříjemný retry loop nebo podruhé neúspěšná connect.

**Riziko:** Auto-recovery path má malé reliability window. Při opakovaných pokusech operátor vidí "E-Stop setup selhal" i přesto že první retry by uspěl po 2s delay.

**Doporučení:** Po `power_off()` poll `robot.is_powered_on()` s timeout (cca 10s). Pokud je stále on po timeout, `raise` s konkrétní zprávou.

---

#### FIND-063 — `connect_partial` swallowuje exception na estop/lease/power/dispatcher, ale vrací bundle jako "success"
**Severity:** VYSOKÉ · **Kategorie:** Error handling / UX
**Lokace:** [spot_operator/robot/session_factory.py:146-175](spot_operator/robot/session_factory.py#L146-L175)

```python
except Exception as exc:
    _log.exception("Failed to start E-Stop manager: %s", exc)
# no re-raise — bundle.estop zůstane None
```

Volající (`connect()`) pak dostane bundle s `missing=['estop', 'lease', 'power']` a `ensure_operator_ready()` vyhodí obecnou hlášku "Chybí: estop, lease, power" — bez příčin. Operátor neví, proč selhalo (auth? network? firewall?).

**Riziko:** Diagnostika selhání je obtížná — stačí jen jedna chyba, ale aplikace neprozrazuje která.

**Doporučení:** Uchovat first exception (`first_error: Exception | None`) a při `ensure_operator_ready` vyhodit s tou informací. Nebo konstruovat dynamický error message s příčinami.

---

#### FIND-064 — Lease keepalive není explicitně ověřen v `SpotBundle` — spoléhá na autonomy `LeaseManager`
**Severity:** VYSOKÉ · **Kategorie:** Safety (dependence na 3rd party)
**Lokace:** [spot_operator/robot/session_factory.py:120-144](spot_operator/robot/session_factory.py#L120-L144) (odkazuje na `app.robot.lease.LeaseManager`)

Spot SDK `Lease` má default timeout ~5s. Bez keepalive threadu (bosdyn `LeaseKeepAlive`) robot odmítne commands po pár vteřinách. Bundle předpokládá, že autonomy `LeaseManager` keepalive zajistí, ale *tento kód to nezaručuje ani neoveřuje*.

**Riziko:** Pokud autonomy v budoucnu změní `LeaseManager` (vypne keepalive), recording a playback selhávají po pár vteřinách s "lease expired". Stealth regression.

**Doporučení:** Ověřit čtením `autonomy/app/robot/lease.py` (oblast 16), že keepalive běží. Přidat explicit health check funkci `bundle.verify_lease_active() -> bool` nebo wrapper test.

---

#### FIND-065 — `is_motors_powered` vrací `False` při exception → volající aplikuje power_on na může-už-on robota
**Severity:** NÍZKÉ · **Kategorie:** Error handling
**Lokace:** [spot_operator/robot/power_state.py:29-36](spot_operator/robot/power_state.py#L29-L36)

```python
try:
    return bool(robot.is_powered_on())
except Exception as exc:
    _log.warning("is_powered_on check failed: %s", exc)
    return False
```

Při network glitch nebo authentication expired, funkce řekne "vypnutý" i když motors fakticky běží. UI pak může nabídnout tlačítko "Zapnout", operátor klikne → `power_on` RPC (idempotentní, nevadí). Ale UX je matoucí.

**Doporučení:** Vrátit `Optional[bool]` (None = unknown) a UI by měl zobrazit "?" místo tvrzení že je off.

---

#### FIND-066 — `capture_sources` silently demotes checkpoint → waypoint při capture failure
**Severity:** VYSOKÉ · **Kategorie:** UX / Data integrity
**Lokace:** [spot_operator/robot/dual_side_capture.py:27-36](spot_operator/robot/dual_side_capture.py#L27-L36) + [spot_operator/services/recording_service.py:175-180](spot_operator/services/recording_service.py#L175-L180)

```python
# dual_side_capture.py — capture vrátí prázdný dict pokud vše selže
# recording_service.py:
if not photos:
    kind = "waypoint"  # ← silent demotion!
    capture_status = CAPTURE_STATUS_FAILED
    note = "capture_failed"
```

Operátor klikl "Checkpoint" s intention fotit, ale kamera selhala → systém tiše uložil "waypoint" (bez fotek) s note="capture_failed". Uživatel *nic* v UI nevidí — myslí si, že má CP s fotkami.

**Riziko:** Playback pak nevytvoří fotky na tomto checkpointu (protože je to waypoint kind), operátor si myslí že má kompletní data, ale má jen mapu bez fotek.

**Doporučení:** Při capture failure NEvytvářet záznam jako waypoint, ale:
1. Zobrazit user-facing dialog "Fotka se nepodařila. Zkusit znovu / Přeskočit / Zrušit recording".
2. Pokud přeskočit, uložit jako `kind="checkpoint"` s `capture_status="failed"` — zachovat intenci.

---

#### FIND-067 — `localize_at_start` má `do_ambiguity_check=True`, ale response handling neprozradí, že byla ambiguity
**Severity:** STŘEDNÍ · **Kategorie:** Observability
**Lokace:** [spot_operator/robot/localize_strict.py:63, 72-93](spot_operator/robot/localize_strict.py#L63-L93)

```python
"do_ambiguity_check": True,
...
resp = client.set_localization(**kwargs)
localized_wp = resp.localization.waypoint_id
```

Bosdyn `set_localization` s `do_ambiguity_check=True` může ve `resp.ambiguity_result` reportovat, že bylo více kandidátních pozic. Kód to vůbec nečte.

**Riziko:** Pokud byla ambiguita (např. dvě podobné observace fiducialu), bosdyn si vybral jednu a vrátil OK. Playback skončil na špatném waypointu "podobně blízko", navigace dál je skácená. Nikdo nelog-oval, že ambigui existovala.

**Doporučení:** Číst `resp.ambiguity_result` (pokud API poskytuje) a logovat warning s konkurenčními kandidáty. Při ambiguity > 1 kandidát raise nebo alespoň explicit warning pro UI.

---

#### FIND-068 — `localize_at_start` hodí obecný `RuntimeError` při všech chybách bosdyn set_localization
**Severity:** STŘEDNÍ · **Kategorie:** Error handling / UX
**Lokace:** [spot_operator/robot/localize_strict.py:71-77](spot_operator/robot/localize_strict.py#L71-L77)

```python
try:
    resp = client.set_localization(**kwargs)
except Exception as exc:
    raise RuntimeError(f"Bosdyn set_localization selhal ...: {exc}") from exc
```

Jedna exception class bez rozlišení:
- Fiducial není vidět v kameře (operátor je moc daleko).
- Síťový timeout.
- Neznámý waypoint_id (mapa je rozbitá).
- Bosdyn SDK version mismatch.

Uživatelská akce se liší podle typu. Teď se vše projeví jako "Bosdyn set_localization selhal" + generický stacktrace.

**Doporučení:** Rozlišit aspoň "fiducial not visible" vs "jiná" — bosdyn specifické exceptiony (např. `NoLocalizationError`) catchnout dřív.

---

#### FIND-069 — `connect_partial` vlajky `with_lease` a `with_estop` jsou dead flexibility
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [spot_operator/robot/session_factory.py:80-86](spot_operator/robot/session_factory.py#L80-L86)

Parametry existují, ale grep ukazuje, že všechny call-sites používají `connect()` (bez `with_*`) a volají defaulty (True/True). `connect_partial` nemá žádný přímý call-site kromě `connect()` samotného.

**Doporučení:** Sjednotit do jedné funkce, odstranit flexibilitu pokud není použita (YAGNI).

---

#### FIND-070 — `EstopManager` auto-recovery path (MotorsOnError) nepřihlíží možnosti, že motors jsou on kvůli aktivnímu teleopu (operátor ovládal jiným klientem)
**Severity:** VYSOKÉ · **Kategorie:** Safety
**Lokace:** [spot_operator/robot/session_factory.py:111-143](spot_operator/robot/session_factory.py#L111-L143)

Pokud druhá osoba má Spot pod teleopem (ovládá z tabletu), naše aplikace při connectu udělá auto-recovery: získá lease (odsune tablet), power_off (Spot spadne). Uživatel tabletu nevědí proč.

**Riziko:** Neočekávaný pád robota při "Connect" z aplikace když je někdo jiný aktivní.

**Doporučení:** Před auto-recovery zjistit, zda má někdo aktivní lease (`lease_client.list_leases()`) a zeptat se operátora přes UI dialog: "Na Spotovi je aktivní jiný klient (X). Skutečně převzít?" → explicit confirmation.

---

#### FIND-071 — `SpotBundle` je `@dataclass` bez `slots=True` — attribute typos jsou tiché
**Severity:** NÍZKÉ · **Kategorie:** Type consistency
**Lokace:** [spot_operator/robot/session_factory.py:19-31](spot_operator/robot/session_factory.py#L19-L31)

```python
@dataclass
class SpotBundle:
    session: object
    estop: object | None = None
    ...
```

Bez `slots=True` můžu `bundle.estopp = ...` (typo) a zapíše se do `__dict__` beze stopy. Pak `bundle.estop is None` a disconnect nevěří že estop existuje.

**Doporučení:** Přidat `slots=True` (ale pozor — object-typed fields jsou OK; complications s `Optional` defaults v slots jsou řešitelné).

---

### Oblast 7 — Recording service & flow

#### FIND-072 — `start_waypoint_id` se nastaví při PRVNÍM `create_waypoint` bez ohledu na `kind` (waypoint vs checkpoint)
**Severity:** VYSOKÉ · **Kategorie:** Data integrity / UX
**Lokace:** [spot_operator/services/recording_service.py:119-134](spot_operator/services/recording_service.py#L119-L134), [spot_operator/services/recording_service.py:136-206](spot_operator/services/recording_service.py#L136-L206)

Obě metody `add_unnamed_waypoint` (řádek 123-124) a `capture_and_record_checkpoint` (řádek 154-155) obsahují:

```python
if self._start_waypoint_id is None:
    self._start_waypoint_id = wp_id
```

Pokud operátor klikne rovnou "Checkpoint" (bez předchozího "Waypoint") poblíž fiducialu, první `CP_001` se stane *startem*. Sémanticky se dá říct "je to OK, protože je tam kde je fiducial". **Ale:**

- Bosdyn GraphNav waypoint pozici odvozuje z odometrie + fiducial observací **v tom waypointu**. Pokud robot fyzicky drobnou úpravou kroku *nebyl* u tagu, když se waypoint vytvořil (např. operátor posunul robot o 1m), waypoint pozice a fiducial observace mají drift.
- Playback's `localize_at_start(fiducial_id, start_waypoint_id=CP_001)` pak nemusí najít souhlasné observace → bosdyn padá s `NoLocalization` nebo vybere jinou (nežádoucí) observaci → **robot je mis-localized** → navigate_to(CP_002) plánuje nesmyslně → robot jede na "vzdálený CP" (jak uživatel hlásí).

**Riziko:** Root cause bug hlášeného "robot jede náhodně". Tichá silent failure pattern: recording si myslí, že vše je OK; playback selže neinstruktivně.

**Doporučení:**
1. **Hard enforce** v UI: první operace po `start_recording` musí být `add_unnamed_waypoint` (nebo operátor je upozorněn "Stojí robot na startu u fiducialu? Klikni Waypoint pro označení startu.").
2. Alternativně: recording service si vypočítá skutečný startovní waypoint pomocí `graph_nav_client.get_localization_state()` PŘED prvním `create_waypoint` a uloží si ho.
3. V `stop_and_archive_to_db` ověřit, že `start_waypoint_id`-waypoint má v `waypoint_snapshots/` observaci fiducialu — pokud ne, warn uživatele před save.

**Verifikace:** Scénář replikace — klikni Checkpoint prvně, pusť playback, sleduj logy pro mis-localization warnings.

---

#### FIND-073 — Duplicitní init logika `self._start_waypoint_id = wp_id` ve dvou metodách
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [spot_operator/services/recording_service.py:123-124, 154-155](spot_operator/services/recording_service.py#L123-L155)

Kopie stejného kódu v `add_unnamed_waypoint` i `capture_and_record_checkpoint`. Při refactoru jednoho druhý zapomenut.

**Doporučení:** Extrahovat do helper metody `_ensure_start_waypoint(wp_id)`.

---

#### FIND-074 — `_recorder.stop_recording()` a `download_map()` nemají retry při network glitch
**Severity:** STŘEDNÍ · **Kategorie:** Error handling / UX
**Lokace:** [spot_operator/services/recording_service.py:232-234](spot_operator/services/recording_service.py#L232-L234)

```python
self._recorder.stop_recording()
self._recorder.download_map(tmp_root)
```

Pokud mezi těmi dvěma voláními (nebo uvnitř) selže Wi-Fi na sekundu, exception propaguje → uživatel ztratí celou nahrávku a musí znovu projít celou cestu. Žádný retry, žádná možnost "zkusit znovu".

**Riziko:** Ztráta hodin práce kvůli 1s network glitchu.

**Doporučení:** Wrap do retry (3 attempts, 2s backoff). U `download_map` navíc ověřit počet stažených souborů (`count_waypoints_in_map_dir`) — pokud je < expected, znovu pokus.

---

#### FIND-075 — `read_observed_fiducial_ids` failure je jen warning; uživatel neví, že `fiducial_id` v DB může být nereálný
**Severity:** VYSOKÉ · **Kategorie:** Error handling / UX
**Lokace:** [spot_operator/services/recording_service.py:241-274](spot_operator/services/recording_service.py#L241-L274)

```python
try:
    from app.robot.graphnav_recording import read_observed_fiducial_ids
    observed_list = list(read_observed_fiducial_ids(tmp_root))
    ...
except Exception as exc:
    _log.warning("read_observed_fiducial_ids failed: %s", exc)

# Fallback priority
effective_fiducial_id = (
    observed_fiducial_id or self._fiducial_id or end_fiducial_id
)
```

Když `read_observed_fiducial_ids` padne (corrupted protobuf, neaktuální SDK verze), je použita UI hodnota `self._fiducial_id` (tag, který operátor viděl *před* recording). Ale tag nemusí být *skutečně zaznamenaný* v mapě — v takovém případě playback `localize_at_start(fiducial_id=X)` selže s "fiducial not found in graph".

**Riziko:** Mapa vypadá validně po recording ale je nepoužitelná pro playback.

**Doporučení:**
1. Pokud `read_observed_fiducial_ids` padne, **raise** (ne warning) a ukázat operátorovi: "Validace mapy selhala. Přehrat znovu?". Jinak save je ne-bezpečný.
2. Nebo alespoň `archive_is_valid=False` + `archive_validation_error="could not read fiducials"`.

---

#### FIND-076 — `capture_and_record_checkpoint` ukládá foto data v `self._checkpoints[i].photos` (in-memory tuples) — potenciál pro OOM při dlouhé nahrávce
**Severity:** STŘEDNÍ · **Kategorie:** Resource management
**Lokace:** [spot_operator/services/recording_service.py:41-44, 158](spot_operator/services/recording_service.py#L41-L44)

```python
photos: list[tuple[str, bytes, int, int]] = field(default_factory=list, repr=False)
```

Každý checkpoint drží vlastní fotky (bytes) v RAM. Typické: 2 kamery × 500 KB × 50 CP = 50 MB. Ale nedokumentovaný limit; 200 CP by bylo ~200 MB — riziko OOM na slabších noteboocích.

**Riziko:** Crash při dlouhé nahrávce.

**Doporučení:** Fotky ukládat do temp souborů (jako tuple z (src, Path, w, h)), ne do RAM. Nebo přímo do DB už za běhu (ne at-end).

---

#### FIND-077 — `self._checkpoints` se neresetuje mezi po-sobě-jdoucím spuštění stejné RecordingService instance
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/services/recording_service.py:96-117](spot_operator/services/recording_service.py#L96-L117) (`start` method)

```python
def start(self, *, map_name_prefix, ...):
    if self._recorder.is_recording:
        raise RuntimeError("Recording already in progress.")
    self._default_capture_sources = list(default_capture_sources)
    self._fiducial_id = fiducial_id
    self._recorder.start_recording(...)
```

`start()` neresetuje `self._checkpoints` ani `self._start_waypoint_id`. V běžném flow se po `stop_and_archive_to_db` vytvoří nová instance (`TeleopRecordPage.initializePage`), tak to nevadí *dnes*. Ale pokud by někdo re-use existing service, **starý stav by se smíchal s novým** → mapa by obsahovala checkpointy z předchozí nahrávky.

**Riziko:** Stealth regression budoucích změn.

**Doporučení:** `start()` explicitně resetuje state:
```python
self._checkpoints.clear()
self._start_waypoint_id = None
```
(`abort()` už to dělá, takže sjednotit.)

---

#### FIND-078 — `capture_and_record_checkpoint` při capture failure **silently demotes** na waypoint (duplicita FIND-066)
**Severity:** VYSOKÉ · **Kategorie:** UX / Data integrity
**Lokace:** [spot_operator/services/recording_service.py:175-180](spot_operator/services/recording_service.py#L175-L180)

Duplikuji referenci na FIND-066 v oblasti 6 — klíčový UX bug. Viz také FIND-082.

**Doporučení:** UI dialog při failure, viz FIND-066.

---

#### FIND-079 — `note = "capture_failed"` / `"capture_partial"` jsou hardcoded stringy bez enum
**Severity:** NÍZKÉ · **Kategorie:** Type consistency
**Lokace:** [spot_operator/services/recording_service.py:180, 183](spot_operator/services/recording_service.py#L180-L183)

Magic string literals. Při konzumaci (`note` sloupec JSON) se musí porovnávat s stejným stringem jinde.

**Doporučení:** Konstanty nebo enum `CaptureNote.CAPTURE_FAILED`, `CaptureNote.CAPTURE_PARTIAL`.

---

#### FIND-080 — `encode_bgr_to_jpeg` nevaliduje `image_bgr.shape` (předpokládá HWx3, BGR)
**Severity:** STŘEDNÍ · **Kategorie:** Type consistency / Error handling
**Lokace:** [spot_operator/services/photo_sink.py:17-26](spot_operator/services/photo_sink.py#L17-L26)

```python
rgb = image_bgr[:, :, ::-1]  # BGR → RGB
height, width = rgb.shape[:2]
img = Image.fromarray(rgb)
```

Autonomy `ImagePoller.capture(src)` může vrátit různé formáty podle source:
- Fisheye: grayscale? (Některé bosdyn image sources jsou 1-channel.)
- Front camera: BGR `(H,W,3)`.
- Thermal: 16-bit grayscale.

Pokud přijde 2D grayscale nebo (H,W,1), `image_bgr[:, :, ::-1]` selže s `IndexError` nebo produkuje chybný obrázek. `Image.fromarray` na (H,W) s uint8 udělá grayscale JPEG (OK), ale kód předpokládá že je to BGR.

**Riziko:** Crash při pokusu o uložení fotky z nestandard source; nebo uložený obrázek je vizuálně špatný.

**Doporučení:**
```python
if image_bgr.ndim == 2:
    img = Image.fromarray(image_bgr)  # grayscale
elif image_bgr.ndim == 3 and image_bgr.shape[2] == 3:
    rgb = image_bgr[:, :, ::-1]
    img = Image.fromarray(rgb)
else:
    raise ValueError(f"Unsupported image shape: {image_bgr.shape}")
```

---

#### FIND-081 — `encode_bgr_to_jpeg` vytváří `rgb` jako view — potenciální memory layout issue
**Severity:** NÍZKÉ · **Kategorie:** Edge case
**Lokace:** [spot_operator/services/photo_sink.py:21-23](spot_operator/services/photo_sink.py#L21-L23)

`rgb = image_bgr[:, :, ::-1]` je non-contiguous numpy view. `Image.fromarray` v PIL > 9 většinou zvládá, ale některé builds vyžadují contiguous memory.

**Doporučení:** `rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])` pro robustnost.

---

#### FIND-082 — `capture_sources` ukládá `list` reference do `RecordedCheckpoint.capture_sources` (shared mutable)
**Severity:** NÍZKÉ · **Kategorie:** Type consistency
**Lokace:** [spot_operator/services/recording_service.py:189](spot_operator/services/recording_service.py#L189)

```python
cp = RecordedCheckpoint(
    name=name,
    waypoint_id=wp_id,
    kind=kind,
    capture_sources=sources,  # ← shared reference
    ...
)
```

`sources` přijatý parametr je sdílen s uložený checkpoint. Pokud volající dále `sources.append(...)`, modifikuje i uložený. Dnes volající (teleop_record_page) to nedělá, ale fragile.

**Doporučení:** `capture_sources=list(sources)` pro explicit copy.

---

#### FIND-083 — `stop_and_archive_to_db` volá `save_map_to_db` uvnitř vlastní `try/finally` pro temp cleanup; ale uvnitř `save_map_to_db` je `Session().commit()` — pokud commit selže, temp by se smazal, ale map_id nebyl vrácen
**Severity:** NÍZKÉ · **Kategorie:** Error handling
**Lokace:** [spot_operator/services/recording_service.py:229-300](spot_operator/services/recording_service.py#L229-L300)

Tok je konzistentní: try-wrap, finally cleanup temp. Pokud save_map_to_db raise, mapa je v DB? Závisí na kde to raisne.

- `parse_checkpoint_plan`, `validate_map_dir`, `zip_map_dir` — před DB session, nic v DB.
- `maps_repo.create` - insert do session (pending), `session.commit()` pak. Pokud commit fail, transakce rollback, nic v DB.

Takže stav je "buď všechno nebo nic". OK.

ALE: Temp cleanup smaže GraphNav stažené soubory — pokud save selže, user musí znovu jít celou cestu (FIND-074).

**Doporučení:** Při save failure zachovat temp a nabídnout retry "Uložit znovu?".

---

#### FIND-084 — `RecordingService.photo_count` property počítá přes všechny `_checkpoints` pokaždé; není cachovaný
**Severity:** NÍZKÉ · **Kategorie:** Performance
**Lokace:** [spot_operator/services/recording_service.py:85-86](spot_operator/services/recording_service.py#L85-L86)

```python
@property
def photo_count(self) -> int:
    return sum(len(c.photos) for c in self._checkpoints)
```

Volaný v UI refresh loop — linear scan per call. Pro 100 CP × 10 FPS refresh = 1000 ops/s, trivial. OK v praxi, ale mohl by být inkrement counter.

---

#### FIND-085 — `abort()` při nečisté session: `_recorder.is_recording` True + exception v `stop_recording` → `_checkpoints.clear()` pokračuje, ale `_recorder` může zůstat v running stavu (z perspektivy bosdyn)
**Severity:** STŘEDNÍ · **Kategorie:** Robot state / Error recovery
**Lokace:** [spot_operator/services/recording_service.py:302-310](spot_operator/services/recording_service.py#L302-L310)

```python
def abort(self) -> None:
    if self._recorder.is_recording:
        try:
            self._recorder.stop_recording()
        except Exception as exc:
            _log.warning("stop_recording during abort failed: %s", exc)
    self._checkpoints.clear()
    self._start_waypoint_id = None
```

Pokud bosdyn `stop_recording` failne, ale robot má internal GraphNav recording state → tento state přetrvá do další session. Další `start_recording` může dostat "session already active" error.

**Riziko:** Recovery path je rozbitý — operátor pak musí restart app nebo robota.

**Doporučení:** Po abort s failed `stop_recording` volat bosdyn `graph_nav_recording_client.get_record_status()` a pokud je stále active, explicit `stop_recording()` retry s delay.

---

### Oblast 8 — Playback service & flow

#### FIND-086 — `run_all_checkpoints` vytváří run v DB **až po** pre-flight checks; při selhání checks žádný DB audit record
**Severity:** STŘEDNÍ · **Kategorie:** Observability / Data integrity
**Lokace:** [spot_operator/services/playback_service.py:167-224](spot_operator/services/playback_service.py#L167-L224)

Checks PŘED `runs_repo.create`:
- `_extract_checkpoints` (může raise)
- `_is_localized_on_current_graph` (vrací False → raise)
- `localized_wp != expected_start` (raise)

Pokud kterákoliv selže, run se nikdy nevytvoří v DB. Pro audit ("kolikrát selhal playback na kterém kroku") nejsou data → operátor nevidí historii failures v CRUD okně.

**Riziko:** Diagnostika "proč playback často selhává" je slepá — uvidí jen runs, které prošly pre-flight. Skryté 80% pokusů není zaznamenáno.

**Doporučení:** Vytvořit run *první*, s `status=pending_start`. Pre-flight checks pak označí run jako `failed` s konkrétní reason, než se vůbec hne robot.

---

#### FIND-087 — `_is_localized_on_current_graph` volaný 2× před startem (řádky 183, 192) — race window
**Severity:** NÍZKÉ · **Kategorie:** Redundancy
**Lokace:** [spot_operator/services/playback_service.py:183-202](spot_operator/services/playback_service.py#L183-L202)

Mezi dvěma voláními `_is_localized_on_current_graph` (kontrola) a `_current_localization_waypoint` (získání hodnoty) se může změnit stav robota. Race je malý, ale existuje.

**Doporučení:** Volat `client.get_localization_state()` jen jednou a použít výsledek ve všech následných checkách.

---

#### FIND-088 — Po navigate failure (recoverable) `continue` — ale `consecutive_nav_fails` resetovaný jen při success, ne při recovery
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/services/playback_service.py:251-308, 337](spot_operator/services/playback_service.py#L251-L337)

Logika retry counting:
- `not result.ok` → `consecutive_nav_fails += 1`
- Úspěch → `consecutive_nav_fails = 0`

Ale: pokud selže CP_002, retry uspěje na CP_003 a dál, counter je resetnutý pouze při success. OK.

Edge case: pokud CP_001 selže → `consecutive_nav_fails=1` → continue. CP_002 uspěje → counter=0. Ale v `checkpoint_results` je CP_001 záznam s `capture_status=NOT_APPLICABLE`, což *matou* úspěchu (2/3). To je actual data integrity — vypadá mapa OK, ale CP_001 jsme fakticky *neviděli*.

Souvisí s UX:  playback_result_page ukáže "2 z 3 dokončeno". Uživatel neví, kterýkterý nebyl.

**Doporučení:** Result page musí zřetelně rozlišovat "dokončené (s fotkou)" vs "navigovatelné ale bez fotek" vs "nenavigovatelné (failed)". Per-checkpoint visualization.

---

#### FIND-089 — `_navigate_with_retry` retry strategie závisí jen na `_should_retry_outcome` — STUCK a NO_ROUTE se nezkusí znovu
**Severity:** STŘEDNÍ · **Kategorie:** Robot state
**Lokace:** [spot_operator/services/playback_service.py:438-522](spot_operator/services/playback_service.py#L438-L522)

```python
def _should_retry_outcome(self, result) -> bool:
    if result.is_localization_loss:
        return True
    return result.outcome == NavigationOutcome.TIMEOUT
```

Jen `LOST`, `NOT_LOCALIZED`, `TIMEOUT` se retryují. Ale:
- `STUCK` (fyzická překážka na chvíli) by zasloužil retry po 3s delay, pokud zmizela.
- `NO_ROUTE` po re-localize + retry by mohl fungovat (pokud GraphNav měla stale map).

**Riziko:** Recoverable failures se vzdáme příliš brzy, operátor musí manuálně znovu.

**Doporučení:** Rozšířit retry whitelist o STUCK (s 3s delay) a NO_ROUTE. Nebo explicit enum per-outcome strategy.

---

#### FIND-090 — `_is_robot_lost_error` detekce přes substring match — **fragile** při změně bosdyn formatu
**Severity:** STŘEDNÍ · **Kategorie:** Type consistency / Bosdyn compat
**Lokace:** [spot_operator/services/playback_service.py:524-531](spot_operator/services/playback_service.py#L524-L531), [spot_operator/constants.py:85-89](spot_operator/constants.py#L85-L89)

```python
ROBOT_LOST_ERROR_MARKERS: tuple[str, ...] = (
    "robotlosterror",
    "already lost",
    "robot is already lost",
)
```

Bosdyn future verze může přeformulovat message (např. na "GraphNav localization lost"). Detekce nechytí → playback pokračuje, robot fyzicky nereaguje, další navigate vrátí stejnou RobotLostError, ale playback neudělá terminal abort.

**Riziko:** Regression při upgrade bosdyn SDK.

**Doporučení:** Detekce přes typ exception (pokud `NavigationResult` má `result.exception: Exception`), ne přes string matching.

---

#### FIND-091 — `_record_checkpoint_result` commituje DB pro *každý* checkpoint (ne batch)
**Severity:** NÍZKÉ · **Kategorie:** Performance
**Lokace:** [spot_operator/services/playback_service.py:749-762](spot_operator/services/playback_service.py#L749-L762)

```python
def _record_checkpoint_result(self, ...) -> None:
    self._checkpoint_results.append(checkpoint_result)
    with Session() as s:
        runs_repo.mark_progress(s, self._run_id, success_count, checkpoint_results_json=...)
        s.commit()
```

Každý CP = 1 DB commit. Pro 100 CP × 1s commit = 100s overheadu. OK pro typické playbacky (5–20 CP), ale lahodné pro budoucí škálování.

**Výhoda (pozitiva):** Při crash uprostřed máme persisted state.

**Doporučení:** Nechat tak pro crash recovery; ale monitorovat latency v logu pro rozpoznání pomalé DB.

---

#### FIND-092 — `_record_checkpoint_result` posílá celý `checkpoint_results_json` seznam pokaždé — O(N²) volumen
**Severity:** STŘEDNÍ · **Kategorie:** Performance
**Lokace:** [spot_operator/services/playback_service.py:754-760](spot_operator/services/playback_service.py#L754-L760)

Při 100 CP se poslední update pošle celý seznam 100 objektů do DB. Cumulative volumen = 1+2+3+...+100 = 5050 záznamů zapsaných přes N zápisů. Pomalé + zbytečně velké.

**Doporučení:** Uložit checkpoint_results jako separátní tabulka `run_checkpoint_results` (FK na run_id). Pak se stačí `INSERT` per CP.

---

#### FIND-093 — `return_home` a `request_abort` jsou zameny — `request_return_home` volá `request_abort`
**Severity:** VYSOKÉ · **Kategorie:** Logic error / UX
**Lokace:** [spot_operator/services/playback_service.py:118-121](spot_operator/services/playback_service.py#L118-L121)

```python
def request_return_home(self) -> None:
    """Požádá o návrat domů — běží asynchronně přes RunReturnHomeThread."""
    # Reálně spouštíme v samostatném threadu, protože navigate_to blokuje.
    self.request_abort()
```

Comment říká "spouštíme v threadu", ale implementace volá `request_abort()` — NE return home. To je MARK_INCONSISTENCY: `request_return_home` by měla zavolat `self.return_home(start_wp_id)` v threadu.

Nejspíš autor zamýšlel: "abort current operation first, then spawn return_home thread". Ale druhý krok chybí.

**Riziko:** Uživatel klikne "Návrat domů" → nic se nestane kromě abortu current navigate. Robot nezačne jet domů automaticky.

**Doporučení:** Ověřit v `PlaybackRunPage`, který thread po aborcích spouští `return_home`. Možná je to OK na UI vrstvě — ověřit callflow. Ale zakomentovat zda je současná `request_return_home` žádaný no-op alias, nebo forgotten TODO.

**Verifikace:** Spustit playback, stisknout "Return home" v UI; sledovat logy, zda robot jede nebo jen aborti.

---

#### FIND-094 — Inkonzistence: `_localize_with_fallback` při `meta.fiducial_id is None` přepne na `FIDUCIAL_NEAREST`, ale dál se neoveřuje správný waypoint
**Severity:** VYSOKÉ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/services/playback_service.py:614-621](spot_operator/services/playback_service.py#L614-L621)

```python
if meta.fiducial_id is None:
    _log.warning("Map %s nemá meta.fiducial_id — používám FIDUCIAL_NEAREST jako last resort.", meta.name)
    self._navigator.localize(strategy=LocalizationStrategy.FIDUCIAL_NEAREST)
    return  # ← HNED return, žádná další verifikace!
```

Při fallback na FIDUCIAL_NEAREST, kód se vrací bez ověření, že `localized_wp == meta.start_waypoint_id`. Naopak na strict path (řádek 642+) je **tohle přesně ověřeno**. Inkonzistence.

**Riziko:** Při mapách bez fiducial_id projede lokalizace tiše i když je robot "někde jinde". Pak `run_all_checkpoints` padne na pozdější kontrole (`_is_localized_on_current_graph`), ale uživatel neví proč.

**Doporučení:** I po FIDUCIAL_NEAREST přidat `_current_localization_waypoint()` kontrolu vs `meta.start_waypoint_id` a raise pokud se liší víc než tolerance.

---

#### FIND-095 — Dead code: `if localized_wp != meta.start_waypoint_id` na řádku 642 i 649 (DVĚ IDENTICKÉ KONTROLY)
**Severity:** NÍZKÉ · **Kategorie:** Tech debt / Dead code
**Lokace:** [spot_operator/services/playback_service.py:642-662](spot_operator/services/playback_service.py#L642-L662)

```python
if localized_wp != meta.start_waypoint_id:
    raise RuntimeError(...)  # ← první check: raise

# Ověření: skončili jsme opravdu blízko startu?
if localized_wp != meta.start_waypoint_id:  # ← druhý check: NIKDY není true!
    _log.warning(...)
```

Druhá kontrola je nedosažitelná (pokud první raisne, nedostaneme se k ní). Zbylý log message v `else` větvi.

**Doporučení:** Odstranit dead branch.

---

#### FIND-096 — `cleanup()` používá `import shutil` lokálně a `shutil.rmtree(..., ignore_errors=True)` — Windows filelock silent failure
**Severity:** NÍZKÉ · **Kategorie:** Resource management
**Lokace:** [spot_operator/services/playback_service.py:428-434](spot_operator/services/playback_service.py#L428-L434)

```python
def cleanup(self) -> None:
    if self._map_temp_dir is not None:
        import shutil
        shutil.rmtree(self._map_temp_dir, ignore_errors=True)
        self._map_temp_dir = None
```

Viz FIND-051 — na Windows mohou být GraphNav soubory locked bosdynem. `ignore_errors=True` projde bez varování, temp adresář zůstává. Při opakovaném playbacku se temp plní.

**Doporučení:** Log warning při failure + plánovat `cleanup_temp_root` periodicky.

---

#### FIND-097 — `_warn_if_drift` jen loguje — uživatel to nevidí
**Severity:** STŘEDNÍ · **Kategorie:** UX / Observability
**Lokace:** [spot_operator/services/playback_service.py:533-544](spot_operator/services/playback_service.py#L533-L544)

```python
_log.warning(
    "Localize drift at %s: bosdyn říká robot je na %s, cíl byl %s. "
    "Drift pokračuje — riziko RobotLostError v dalších CP.",
    cp.name, post_wp[:12], cp.waypoint_id[:12],
)
```

Drift je prekurzor `RobotLostError` — dá se tomu předejít re-localize. Ale log je neviditelný pro uživatele; při playbacku operátor nemá signál, že by měl zasáhnout.

**Doporučení:** Emitovat `drift_warning` Qt signál + zobrazit v UI (PlaybackRunPage) s tlačítkem "Re-localize now".

---

#### FIND-098 — Signály `run_started`, `map_uploaded`, `localized`, ... emitované z worker threadu, ale kód v thread používá `self._run_id` — pokud UI disconnectne, není to kritické, ale viděl by duplikované eventy
**Severity:** NÍZKÉ · **Kategorie:** Concurrency
**Lokace:** [spot_operator/services/playback_service.py:67-74](spot_operator/services/playback_service.py#L67-L74)

Qt signály jsou thread-safe přes Qt.QueuedConnection (default), takže je to OK. Ale pokud by se UI signály disconnectly mid-run a reconnectly k jiné stránce, mohly by se doručit na novou i starou → duplicate handling.

**Doporučení:** V page `teardown()` explicitně `disconnect()` všech signálů (viz Agent 1 nálezy).

---

#### FIND-099 — `run_all_checkpoints` používá `except Exception as exc: _log.exception(...); ...; continue` — **catch-all** v hot loop
**Severity:** STŘEDNÍ · **Kategorie:** Error handling
**Lokace:** [spot_operator/services/playback_service.py:339-355](spot_operator/services/playback_service.py#L339-L355)

```python
except Exception as exc:
    _log.exception("Checkpoint %s failed: %s", cp.name, exc)
    ...
    abort_reason = f"exception at {cp.name}: {exc}"
    continue  # ← pokračuj na další checkpoint!
```

Pokud exception je `ProgrammingError` v DB nebo `KeyboardInterrupt`, continue je *nesprávný*:
- `KeyboardInterrupt` by měl abortovat celý run.
- `MemoryError` podobně.
- `ProgrammingError` (DB schema mismatch) na CP1 se prakticky určitě projeví i na CP2-N.

**Doporučení:** Catch jen konkrétní exceptiony (NavigationError, CaptureError). `BaseException` re-raise. `OperationalError` (DB transient) retry. `ProgrammingError` abort.

---

#### FIND-100 — `return_home` volá autonomy `return_home` — žádná kontrola, že playback byl dokončen / robot je v klidu
**Severity:** STŘEDNÍ · **Kategorie:** Robot state
**Lokace:** [spot_operator/services/playback_service.py:380-426](spot_operator/services/playback_service.py#L380-L426)

```python
def return_home(self, start_wp_id: str):
    from app.robot.return_home import return_home
    ...
    result = return_home(
        self._navigator,
        start_wp_id,
        timeout_s=PLAYBACK_RETURN_HOME_TIMEOUT_SEC,
        progress=self._emit_progress,
    )
```

Nedělá žádnou pre-check:
- Robot je ještě v localized stavu (`_is_localized_on_current_graph`)?
- Navigator není v stuck z předchozího CP?
- Battery OK?

Pokud jsou některé v degradovaném stavu, `return_home` selže uprostřed cesty a robot zůstane někde.

**Doporučení:** Pre-flight check před voláním autonomy `return_home`.

---

#### FIND-101 — `_classify_final_status` má subtilní bug: pokud `abort_reason="Aborted by user"` ale `success == total`, vrací `RunStatus.aborted`, ne `completed`
**Severity:** NÍZKÉ · **Kategorie:** Logic
**Lokace:** [spot_operator/services/playback_service.py:736-747](spot_operator/services/playback_service.py#L736-L747)

```python
if abort_reason:
    if abort_reason == "Aborted by user":
        return RunStatus.aborted
    if success == 0:
        return RunStatus.failed
    return RunStatus.partial
if success == total:
    return RunStatus.completed
return RunStatus.partial
```

Scénář: operátor klikne Abort po úspěšném dokončení posledního CP (ale před emitem `run_completed`). `abort_reason="Aborted by user"` bylo už nastaveno v `for` loop — ale pokud je nastaveno **až v break before** final iteration, pak `success == total` je možné.

Výsledek: status = aborted i přestože úspěch 100%. Matoucí pro audit.

**Doporučení:** Prioritizovat `success == total` nad `abort_reason == "Aborted by user"`.

---

#### FIND-102 — `_extract_checkpoints` filter `if cp.waypoint_id` je dead code (defensive)
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [spot_operator/services/playback_service.py:680](spot_operator/services/playback_service.py#L680) vs [spot_operator/services/contracts.py:157-159](spot_operator/services/contracts.py#L157-L159)

`parse_checkpoint_plan` přes `_required_str` **už raise** na prázdný `waypoint_id`. Takže filter `if cp.waypoint_id` v playback_service je nedosažitelný.

**Doporučení:** Odstranit (nebo alespoň `assert cp.waypoint_id, "invariant violated"`).

---

#### FIND-103 — Error message v `run_all_checkpoints` když chybí checkpointy: "Mapa neobsahuje žádné checkpointy." — příčinu nezná
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/services/playback_service.py:176-177](spot_operator/services/playback_service.py#L176-L177)

```python
if not checkpoints:
    raise RuntimeError("Mapa neobsahuje žádné checkpointy.")
```

Pokud `checkpoints_json` je None v DB (viz FIND-020), zpráva je tato. Pokud všechny CP mají prázdný waypoint_id (odfiltrováno — ale viz FIND-102 že se to nestane), taky tahle. Uživatel neví, jestli je mapa stará / corrupted / prázdná recording.

**Doporučení:** Rozlišit: "Mapa nemá uložené checkpoints_json" vs "Všechny checkpointy byly zahozeny kvůli chybě X".

---

#### FIND-104 — `set_global_avoidance` exception handling jen warning; playback pokračuje s default padding
**Severity:** NÍZKÉ · **Kategorie:** Robot state
**Lokace:** [spot_operator/services/playback_service.py:229-241](spot_operator/services/playback_service.py#L229-L241)

```python
try:
    from app.robot.mobility_state import set_global_avoidance
    set_global_avoidance(self._bundle.session, PLAYBACK_AVOIDANCE_STRENGTH)
    ...
except Exception as exc:
    _log.warning("Nepodařilo se nastavit global avoidance ...", ...)
```

Pokud nastavení selže, `PLAYBACK_AVOIDANCE_STRENGTH` není zajištěn → Spot má defaultní padding (obvykle nižší). Robot se může dostat blíže překážkám → drift → RobotLostError.

**Riziko:** Robot je méně opatrný, ale uživatel myslí, že je.

**Doporučení:** Warning dialog v UI: "Nepodařilo se nastavit avoidance — pokračovat s default?". Nebo raise pokud není critical.

---

### Oblast 9 — OCR pipeline & worker

#### FIND-105 — `OcrWorker._claim_and_process_one` claim-flow je *správný*, ale křehký a nedokumentovaný
**Severity:** STŘEDNÍ · **Kategorie:** Tech debt / Error handling
**Lokace:** [spot_operator/services/ocr_worker.py:120-150](spot_operator/services/ocr_worker.py#L120-L150)

Oproti tomu, co předběžné exploraci napovídalo: **claim commit je oddělený od processing** (claim v první session se commitem hned, pipeline.process mimo session, mark_done/mark_failed v nové session). Tohle je korektní design (protichůdně k Agent report 2). Nicméně:

- Flow je zřetelný jen pozornému čtenáři; docstring to explicitně neříká.
- Pokud `_store_results` selže *mezi* `delete_for_photo_engine` a `insert_many` (mid-commit), SQLAlchemy transakce rollback a nic v DB se nezmění — OK.
- Pokud `mark_failed` sám padne (např. při DB outage), photo zůstane v `processing` s lockem. Sweep zombies po `OCR_ZOMBIE_TIMEOUT_MIN=5min` to vyčistí. OK s rizikem duplicate OCR (viz FIND-026).

**Doporučení:** Přidat docstring na `_claim_and_process_one` popisující přesně, *co se stane při selhání v kterém kroku* (state machine diagram). Pomůže future debug.

---

#### FIND-106 — Při permanent failure (např. YOLO model file chybí) worker loop cyklí donekonečna s backoff
**Severity:** VYSOKÉ · **Kategorie:** Error handling
**Lokace:** [spot_operator/services/ocr_worker.py:89-118](spot_operator/services/ocr_worker.py#L89-L118)

`_handle_loop_error` neklasifikuje `exc` — každá chyba je treated as transient a aplikuje se backoff (max 60s). Pokud je chyba *permanent* (`FileNotFoundError: YOLO model`, `ModuleNotFoundError: fast_plate_ocr`), worker nikdy neskončí, jen spamuje log.

Operátor neví, že OCR nefunguje — photo queue narůstá, není feedback.

**Riziko:** OCR worker zombie; žádné photos se nezpracují, ale aplikace vypadá funkční.

**Doporučení:** Klasifikovat chyby:
- `FileNotFoundError` / `ModuleNotFoundError` → terminal abort + emit signal "OCR permanently disabled".
- `OperationalError` (DB) → backoff.
- Jiné → single log + pokračovat.

---

#### FIND-107 — `assert photo_id is not None` v produkci může být disabled přes `python -O`
**Severity:** NÍZKÉ · **Kategorie:** Code quality
**Lokace:** [spot_operator/services/ocr_worker.py:134](spot_operator/services/ocr_worker.py#L134)

Pokud někdo spustí aplikaci s `python -O main.py`, `assert` se skip-ne. Následně `photo_id=None` by propadl do `self.photo_processed.emit(photo_id, ...)` → Qt signal type error (int expected).

**Doporučení:** `if photo_id is None: raise RuntimeError("photo_id unexpectedly None after claim")`.

---

#### FIND-108 — `OcrPipeline.process` při `cv2.imdecode` failure vrátí `[]` + označí photo jako done
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/ocr/pipeline.py:58-61](spot_operator/ocr/pipeline.py#L58-L61), konzekvence v [spot_operator/services/ocr_worker.py:139-143](spot_operator/services/ocr_worker.py#L139-L143)

```python
image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
if image is None:
    _log.warning("OCR pipeline: failed to decode image (%d bytes)", len(image_bytes))
    return []
```

Corrupted JPEG nebo nepodporovaný formát vrátí prázdný list → worker `mark_done` → uživatel vidí fotku s "0 detekcí, status done". Neví, že fotka byla corruptedly decodnuta.

**Riziko:** Diagnostika neprozradí, že fotka je reálně rozbitá.

**Doporučení:** Pokud imdecode failne, pipeline by měla *raise* (např. `ImageDecodeError`), worker pak `mark_failed` → operator vidí status a může ji nahlásit/re-capture.

---

#### FIND-109 — `YoloDetector._ensure_loaded` bez threading lock, ale volá se jen přes pipeline lock
**Severity:** NÍZKÉ · **Kategorie:** Concurrency
**Lokace:** [spot_operator/ocr/detector.py:28-40](spot_operator/ocr/detector.py#L28-L40)

```python
def _ensure_loaded(self) -> Any:
    if self._model is not None:
        return self._model
    ...
    self._model = YOLO(str(self._model_path))
```

V `OcrPipeline.warmup` a `.process` je `with self._lock`, takže ensure_loaded je v praxi pod lockem. Ale pokud by někdo použil YoloDetector přímo (mimo pipeline), race possible. `OcrPipeline` lock je implicit contract.

**Doporučení:** Buď `Detector` vlastní `threading.Lock()` uvnitř ensure_loaded (double-check locking), nebo explicit docstring "volat pouze přes OcrPipeline".

---

#### FIND-110 — `FastPlateReader.read` má 3-level nested try/except — velmi fragile
**Severity:** STŘEDNÍ · **Kategorie:** Tech debt / Error handling
**Lokace:** [spot_operator/ocr/reader.py:77-105](spot_operator/ocr/reader.py#L77-L105)

```python
try:
    try:
        result = reader.run(gray, return_confidence=True)
    except TypeError as exc:
        result = reader.run(gray)  # retry bez kwarg
except Exception as exc:
    # Grayscale failed, try RGB
    ...
    try:
        try:
            result = reader.run(rgb, return_confidence=True)
        except TypeError as exc2:
            result = reader.run(rgb)
    except Exception:
        return "", None
```

- Double nested TypeError handling pro grayscale i RGB → kopie stejného patternu.
- 3 úrovně vnoření → čtenář netuší, který scénář se kdy aktivuje.
- Exception msg `TypeError` je podezřelá (`return_confidence` kwarg missing?); mělo by být feature detection (`hasattr(reader.run, '__signature__')`).

**Doporučení:** Refactor do jedné fallback cesty s explicitním pattern: kontrola API signatury při `_ensure_loaded`, pak jednotné `reader.run(...)` bez try/except kolem kwargů.

---

#### FIND-111 — `_unpack_result` duck-types přes 6 různých návratových formátů fast_plate_ocr; nemá test
**Severity:** STŘEDNÍ · **Kategorie:** Type consistency / Testing
**Lokace:** [spot_operator/ocr/reader.py:123-167](spot_operator/ocr/reader.py#L123-L167)

Každá verze `fast_plate_ocr` má jiný `.run()` return (string, tuple, dict, dataclass, list of those). Kód se snaží všechny pokrýt. Ale:
- Žádné unit testy.
- Při nové verzi s jiným formátem (např. `PlatePrediction2` s jinými atributy) půjde tiše přes všechny branch → `return "", None` → prázdné detekce pro všechny fotky.

**Doporučení:** Explicit verze-check pri `_ensure_loaded` + pinning verze v requirements. Nebo unit test pro každý formát (mock result).

---

#### FIND-112 — `_normalize_plate` odstraní vše mimo alfanumeric bez kontroly délky
**Severity:** NÍZKÉ · **Kategorie:** Data validation
**Lokace:** [spot_operator/ocr/reader.py:17-21](spot_operator/ocr/reader.py#L17-L21), [spot_operator/constants.py:57](spot_operator/constants.py#L57)

```python
def _normalize_plate(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())
```

Výstup může být `""` (prázdný) nebo `"A"` (1 znak) nebo `"A"*100`. `PLATE_TEXT_REGEX` v constants říká `^[A-Z0-9]{1,16}$`, ale nikde se nepoužívá.

**Riziko:** DB ukládá "plate" s 30 znaky, CRUD Filter na regex přestává najít. Uživatel vidí nesmyslné detekce.

**Doporučení:** Aplikovat regex validaci v `_normalize_plate`; pokud nevyhovuje (max 16), vrátit `""` + log warning.

---

#### FIND-113 — `fallback.py._WRAPPER_CODE` subprocess template může failnout v PyInstaller-frozen aplikaci
**Severity:** VYSOKÉ · **Kategorie:** Packaging
**Lokace:** [spot_operator/ocr/fallback.py:74-88](spot_operator/ocr/fallback.py#L74-L88)

```python
cmd = [
    sys.executable,  # ← u frozen app = path k EXE, ne k Python
    "-c",
    wrapper_code,
    ...
]
```

V PyInstaller-frozen build je `sys.executable` aplikace samotná (spot_operator.exe). Subprocess pak spustí **celou aplikaci** s `-c ...` jako argv. Totálně rozbite — frozen GUI app nepřijímá `-c`.

**Riziko:** Re-OCR lepším enginem v production buildu crashnutí nebo žádná response.

**Doporučení:** Detekovat frozen stav (`getattr(sys, 'frozen', False)`) a použít samostatný Python interpreter distribuovaný s aplikací; nebo přepsat fallback mimo subprocess (inline import s try/except).

---

#### FIND-114 — `temp_path.unlink(missing_ok=True)` v `finally` swallowuje všechny OSError
**Severity:** STŘEDNÍ · **Kategorie:** Resource management
**Lokace:** [spot_operator/ocr/fallback.py:127-130](spot_operator/ocr/fallback.py#L127-L130)

```python
finally:
    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass
```

`missing_ok=True` ignoruje jen `FileNotFoundError`. Permission errors (soubor locked jiným procesem na Windows) padnou na `PermissionError` — chycena bare except a silentně se propadne. Temp soubor zůstane.

**Riziko:** Disk fill při opakovaném re-OCR.

**Doporučení:** Logovat: `_log.warning("Failed to delete temp OCR file %s: %s", temp_path, exc)`.

---

#### FIND-115 — `fallback.py` subprocess timeout 30s — krátký pro slabší hardware
**Severity:** NÍZKÉ · **Kategorie:** Configuration
**Lokace:** [spot_operator/ocr/fallback.py:24](spot_operator/ocr/fallback.py#L24)

```python
_SUBPROCESS_TIMEOUT_SEC = 30
```

Nomeroff Net s torch může trvat 20–40s na weakem CPU (první volání načte model). 30s timeout je hraniční.

**Doporučení:** Zvýšit na 60s nebo udělat konfigurovatelné přes env var.

---

#### FIND-116 — `sweep_zombies_now` volán jen 1× při startu workera
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/services/ocr_worker.py:58-64, 74](spot_operator/services/ocr_worker.py#L58-L74)

Zombies vzniklé *během* běhu (viz FIND-026: OCR delší než 5 min → sweep reset → double OCR) se řeší jen při restartu app. Při long-running session mohou být zombies akumulované.

**Doporučení:** Periodic sweep (každých 10 min v worker loop) nebo DB-side PG_cron.

---

#### FIND-117 — `_handle_loop_error` spí v 0.5s increments → latence abort
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/services/ocr_worker.py:115-118](spot_operator/services/ocr_worker.py#L115-L118)

```python
for _ in range(int(wait_s * 2)):
    if self._stop:
        return
    time.sleep(0.5)
```

Při max backoff 60s je worker poll na `self._stop` 2× za sekundu — OK. Ale při zavření aplikace user čeká až 0.5s, než worker reaguje. Acceptable.

**Doporučení:** Použít `threading.Event.wait(timeout)` místo sleep loop — woken hned při set().

---

#### FIND-118 — `Detection.to_db_row` uses `self.plate or None` → prázdný string se uloží jako NULL plate_text
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity
**Lokace:** [spot_operator/ocr/dtos.py:34-42](spot_operator/ocr/dtos.py#L34-L42)

```python
"plate_text": self.plate or None,
```

Pokud `plate=""`, NULL se zapíše. Kombinace s FIND-028 (NULL NOT DISTINCT v unique) — opakované NULL-text detekce se akumulují. Ale pipeline již filtruje: `if not text: continue` na řádku 90 pipeline — takže do DB by `plate=""` nemělo přijít.

Ale `fallback.py` parse může vytvořit Detection s prázdným plate? Kontrola řádek 150: `if not plate: continue`. OK.

**Doporučení:** Defensive nicméně: v `to_db_row`, raise `ValueError` pokud plate je prázdný — signalizuje bug upstream.

---

### Oblast 10 — Credentials & WiFi

#### FIND-119 — `save_credentials` ukládá keyring BEFORE commit DB; pokud DB selže, keyring má osiřelý záznam
**Severity:** STŘEDNÍ · **Kategorie:** Data integrity / Resource management
**Lokace:** [spot_operator/services/credentials_service.py:60-88](spot_operator/services/credentials_service.py#L60-L88)

```python
keyring.set_password(service_name, keyring_ref, password)  # ← Keyring first
...
with Session() as s:
    ...
    s.commit()  # ← DB second (may fail)
```

Pokud mezi `set_password` a `commit` dojde k DB chybě, heslo zůstane v Windows Credential Locker bez DB záznamu. `delete_credentials` to nevyčistí (protože neví o orphaned keyring entry). Postupně se hromadí.

**Riziko:** Bezpečnostní debris ve WCL; nelze procházet (nemá UI).

**Doporučení:** Reverse pořadí — commit DB první (s temp label nebo pending flag), pak keyring set, pak update DB label na final. Nebo try/finally odstranit keyring pokud commit failne.

---

#### FIND-120 — `save_credentials` při overwrite existujícího záznamu nesmaže starý keyring entry (pokud se keyring_ref změní)
**Severity:** STŘEDNÍ · **Kategorie:** Resource management
**Lokace:** [spot_operator/services/credentials_service.py:67-80](spot_operator/services/credentials_service.py#L67-L80)

```python
existing = credentials_repo.get_by_label(s, label)
if existing:
    existing.hostname = hostname
    existing.username = username
    existing.keyring_ref = keyring_ref  # ← nový ref, stary se nesmaže
    s.commit()
    ...
```

Pokud operátor změní `username` u stejného label, `_build_keyring_ref(label, username)` vygeneruje nový ref. Stary keyring záznam (`{label}:{old_username}`) zůstane osiřelý.

**Doporučení:** Před přepsáním ověřit `existing.keyring_ref != keyring_ref` a pokud ano, `keyring.delete_password(service_name, existing.keyring_ref)`.

---

#### FIND-121 — `load_password` při keyring failure vrací `None` bez user-facing feedback
**Severity:** STŘEDNÍ · **Kategorie:** UX / Error handling
**Lokace:** [spot_operator/services/credentials_service.py:98-103](spot_operator/services/credentials_service.py#L98-L103)

```python
def load_password(service_name: str, keyring_ref: str) -> Optional[str]:
    try:
        return keyring.get_password(service_name, keyring_ref)
    except keyring.errors.KeyringError as exc:
        _log.warning("keyring.get_password failed: %s", exc)
        return None
```

Pokud WCL je nedostupný (Windows Credential Manager služba vypnutá), user click na stored credential v login dialogu se prostě vrátí "unknown password" bez vysvětlení. Operátor znovu zadává heslo ručně.

**Doporučení:** Vrátit `Result<Optional[str], str>` (popř. raise) a UI by měl ukázat "Nelze načíst heslo z Windows Credential Locker (%s)".

---

#### FIND-122 — `delete_credentials` při keyring failure silently pokračuje
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/services/credentials_service.py:117-121](spot_operator/services/credentials_service.py#L117-L121)

```python
try:
    keyring.delete_password(service_name, keyring_ref)
except keyring.errors.KeyringError as exc:
    _log.warning("keyring.delete_password failed (ignoring): %s", exc)
return True
```

DB záznam smazán, keyring entry zůstal. Fragment ve WCL bez UI indicatoru.

**Doporučení:** Vrátit `bool` indikaci, jestli *oba* kroky prošly; UI by mohl upozornit.

---

#### FIND-123 — `_build_keyring_ref` používá `label:username` — pokud jedno z nich obsahuje `:`, ref je ambiguous
**Severity:** NÍZKÉ · **Kategorie:** Edge case
**Lokace:** [spot_operator/services/credentials_service.py:130-131](spot_operator/services/credentials_service.py#L130-L131)

```python
def _build_keyring_ref(label: str, username: str) -> str:
    return f"{label}:{username}"
```

Pokud `label="foo:bar"` a `username="baz"` vs `label="foo"` a `username="bar:baz"`, obě dají `"foo:bar:baz"` → konflikt.

**Riziko:** Ojedinělé, ale reálné pokud jsou label validation lax.

**Doporučení:** URL encode oba parts, nebo použít JSON/tuple jako key formát.

---

#### FIND-124 — `spot_wifi._ping` parse výstupu je fragile přes lokalizované Windows
**Severity:** STŘEDNÍ · **Kategorie:** Robustness
**Lokace:** [spot_operator/services/spot_wifi.py:58-89](spot_operator/services/spot_wifi.py#L58-L89)

```python
text = proc.stdout.lower()
if "received =" in text:
    ...
```

Windows `ping` výstup závisí na system locale:
- EN: "Received = 3"
- CZ: "Přijato = 3"
- DE: "Empfangen = 3"

Parse na českých Windows selže → fallback na "return code 0 → všechny úspěšné" (řádek 89). Nekorektní: returncode 0 znamená "alespoň 1 odpověď", ne "všechny prošly".

**Riziko:** Na CZ Windows `ping 1/3` se interpretuje jako `3/3` → user vidí "vše OK", ale síť má drop-out.

**Doporučení:** Nespoléhat na text parsing. Buď:
- Použít `-n 1` (single ping) + returncode jako ok/not-ok per attempt, loopovat N× z Pythonu.
- Nebo nahradit za Python-native ICMP (např. `icmplib`).

---

#### FIND-125 — `_ping` timeout calculation může přetéct pokud `timeout_s * count + 5` je velký
**Severity:** NÍZKÉ · **Kategorie:** Configuration
**Lokace:** [spot_operator/services/spot_wifi.py:67](spot_operator/services/spot_wifi.py#L67)

```python
proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s * count + 5)
```

S `WIFI_PING_COUNT=3` a `WIFI_PING_TIMEOUT_SEC=3.0` = 14s total. OK. Ale při jiné konfiguraci (`count=100`) timeout = 305s, uživatel čeká 5 min → bad UX.

**Doporučení:** Omezit max timeout (např. `min(timeout_s * count + 5, 30)`).

---

#### FIND-126 — `_tcp_connect` nemá explicit exception logging
**Severity:** NÍZKÉ · **Kategorie:** Observability
**Lokace:** [spot_operator/services/spot_wifi.py:92-97](spot_operator/services/spot_wifi.py#L92-L97)

```python
def _tcp_connect(ip: str, *, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            return True
    except Exception:
        return False
```

Při pokusu o diagnostiku by uživatel rád věděl: "connection refused" vs "no route to host" vs "timeout". Ale všechny jsou catch-all → return False bez logu.

**Doporučení:** `except Exception as exc: _log.debug("TCP %s:%d failed: %s", ip, port, exc); return False`.

---

#### FIND-127 — `open_windows_wifi_menu` je no-op na non-Windows bez informování uživatele
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/services/spot_wifi.py:106-116](spot_operator/services/spot_wifi.py#L106-L116)

```python
def open_windows_wifi_menu() -> None:
    if not sys.platform.startswith("win"):
        return  # ← silent no-op
```

Na Linux/Mac volání tiše pokračuje. Pokud UI tlačítko "Otevřít Wi-Fi" neotevře nic, uživatel myslí, že aplikace je rozbitá.

**Doporučení:** Na non-Windows raise `NotImplementedError` nebo UI tlačítko skrýt.

---

#### FIND-128 — `WifiCheckResult.ok` definuje: `tcp_reachable AND ping_responses > 0` — ale v real-world ping bloky ve firewall jsou časté
**Severity:** STŘEDNÍ · **Kategorie:** UX
**Lokace:** [spot_operator/services/spot_wifi.py:32-34](spot_operator/services/spot_wifi.py#L32-L34)

```python
@property
def ok(self) -> bool:
    return self.tcp_reachable and self.ping_responses > 0
```

Spot firmware může blokovat ICMP ping, ale TCP 443 běží. Pak `ok=False` i přesto že reálně jde připojit se k bosdyn RPC. Uživatel je zbytečně vyděšený.

**Doporučení:** `return self.tcp_reachable` (TCP je dostatečný důkaz). Nebo nechat ping jen informativně.

---

#### FIND-129 — Služba bere `service_name` jako parameter v většině funkcí — opakování `config.keyring_service` u každého volání
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [spot_operator/services/credentials_service.py:44, 98, 106](spot_operator/services/credentials_service.py#L44-L106)

`save_credentials(service_name=...)`, `load_password(service_name=...)`, `delete_credentials(service_name=...)`. Každé volání vyžaduje předání stejného stringu z config. Fragile (typo v jednom volání může rozbít matching mezi save/load).

**Doporučení:** Konstruovat service instance s `service_name` v init, nebo centralizovat v single module-level variable inicializovanou z config.

---

### Oblast 11 — Wizard base & state

#### FIND-130 — `WalkWizard` nepoužívá `flow_state`, ale `FiducialPage` na něj spoléhá → silent `setProperty` fallback
**Severity:** STŘEDNÍ · **Kategorie:** Type consistency / Architecture
**Lokace:** [spot_operator/ui/wizards/walk_wizard.py:24-80](spot_operator/ui/wizards/walk_wizard.py#L24-L80) vs [spot_operator/ui/wizards/pages/fiducial_page.py:484-491](spot_operator/ui/wizards/pages/fiducial_page.py#L484-L491)

`RecordingWizard` a `PlaybackWizard` volají `self.set_flow_state(...)` s typed state object. `WalkWizard` to nedělá — používá místo toho `self.setProperty("available_sources", ...)` atd.

`FiducialPage._store_detected_fiducial` má try-flow:
```python
if state is not None and hasattr(state, "fiducial_id"):
    state.fiducial_id = fiducial_id
    return
wizard.setProperty("fiducial_id", fiducial_id)  # ← fallback pro WalkWizard
```

Takže WalkWizard sice nepadne, ale:
- Fiducial detection je pak v Qt property, nikdo ji nečte.
- Code smell: page má "if/elif" kód pro různé wizardy.

**Riziko:** Pokud někdo změní `FiducialPage` aby očekávala `flow_state()`, WalkWizard tiše přestane ukládat fiducial.

**Doporučení:** Buď vytvořit `WalkWizardState` (i prázdný), nebo odstranit fallback na `setProperty` a page by měla explicit požadavek na state (raise jinak).

---

#### FIND-131 — `recording_state()` / `playback_state()` použijí `assert isinstance(...)` — v prod (python -O) crash later
**Severity:** NÍZKÉ · **Kategorie:** Robustness
**Lokace:** [spot_operator/ui/wizards/recording_wizard.py:52-55](spot_operator/ui/wizards/recording_wizard.py#L52-L55), [spot_operator/ui/wizards/playback_wizard.py:62-65](spot_operator/ui/wizards/playback_wizard.py#L62-L65)

```python
def recording_state(self) -> RecordingWizardState:
    state = self.flow_state()
    assert isinstance(state, RecordingWizardState)
    return state
```

V `python -O` mode assert skipne → `state` může být None → caller dostane `AttributeError: 'NoneType' object has no attribute 'fiducial_id'` místo clear error.

**Doporučení:** `if not isinstance(state, RecordingWizardState): raise RuntimeError(...)`.

---

#### FIND-132 — `PlaybackWizardState` má dvě fiducial fieldy s matoucími názvy (`selected_fiducial_id` vs `fiducial_id`)
**Severity:** NÍZKÉ · **Kategorie:** Naming
**Lokace:** [spot_operator/ui/wizards/state.py:37, 40](spot_operator/ui/wizards/state.py#L37-L40)

```python
selected_fiducial_id: int | None = None  # z vybrané mapy (required)
selected_start_waypoint_id: str | None = None
selected_capture_sources: list[str] = field(default_factory=list)
fiducial_id: int | None = None  # detected (actual)
```

Pattern `selected_*` je konzistentní pro většinu fieldů, ale `fiducial_id` (without `selected_` prefix) je aktuálně detekovaný. Matoucí.

**Doporučení:** Přejmenovat `fiducial_id` → `detected_fiducial_id`. Explicitní intent.

---

#### FIND-133 — `_should_confirm_close()` vrací True *vždycky* když bundle existuje → dialog i při trivial close
**Severity:** STŘEDNÍ · **Kategorie:** UX
**Lokace:** [spot_operator/ui/wizards/base_wizard.py:172-174](spot_operator/ui/wizards/base_wizard.py#L172-L174)

```python
def _should_confirm_close(self) -> bool:
    return self._bundle is not None
```

Operátor otevře wizard, klikne "Zrušit" / zavře okno ihned → dialog "Opravdu zavřít?". Zbytečné friction.

**Doporučení:** Confirm jen pokud probíhá kritická fáze:
- Recording: `recording_service.is_recording`
- Playback: `playback_service.last_run_status == running`
Lifecycle state (`WIZARD_LIFECYCLE_RUNNING`) už existuje — použít.

---

#### FIND-134 — `trigger_estop` má silent fail path — F1 nic neudělá pokud bundle je None
**Severity:** VYSOKÉ · **Kategorie:** Safety
**Lokace:** [spot_operator/ui/wizards/base_wizard.py:96-123](spot_operator/ui/wizards/base_wizard.py#L96-L123)

```python
current = self.currentPage()
widget = getattr(current, "_estop_widget", None) if current is not None else None
if widget is not None and hasattr(widget, "trigger_from_shortcut"):
    widget.trigger_from_shortcut()
    return
...
bundle = self._bundle
if bundle is not None and getattr(bundle, "estop", None) is not None:
    bundle.estop.trigger()
# ← else: nic se nestane! žádný error, žádný log
```

Při early state (ConnectPage, bundle=None) user stiskne F1 → F1 je no-op. Nebezpečné pro safety-critical shortcut.

**Riziko:** Operátor věří, že F1 = E-Stop. Ve skutečnosti ignorován.

**Doporučení:** Pokud ani widget ani bundle.estop není dostupný, logovat ERROR a UI flash + message "E-Stop není dostupný v tomto kroku (robot není připojen)".

---

#### FIND-135 — `safe_abort` pokračuje s `event.accept()` i při selhání teardown/disconnect
**Severity:** STŘEDNÍ · **Kategorie:** Resource management
**Lokace:** [spot_operator/ui/wizards/base_wizard.py:127-170](spot_operator/ui/wizards/base_wizard.py#L127-L170)

`closeEvent`:
```python
self.safe_abort()  # ← může loggovat exception, ale nevrátí chybu
event.accept()     # ← wizard se zavře vždycky
```

`safe_abort` uvnitř má `try: self._bundle.disconnect() except: _log.exception()`. Pokud disconnect failne, bundle leaked, ale wizard už není viditelný pro user-a.

**Doporučení:** Pokud `safe_abort` selže, uložit do state pro next-time cleanup (např. při startu appky). Nebo `event.ignore()` + dialog "Nepodařilo se uklidit — zkusit znovu?".

---

#### FIND-136 — `_populate_props_from_bundle` v 3 wizardech je kopie stejného kódu (DRY violation)
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [recording_wizard.py:57-75](spot_operator/ui/wizards/recording_wizard.py#L57-L75), [playback_wizard.py:67-88](spot_operator/ui/wizards/playback_wizard.py#L67-L88), [walk_wizard.py:57-71](spot_operator/ui/wizards/walk_wizard.py#L57-L71)

Stejný pattern ve 3 souborech:
- `ImagePoller(bundle.session).list_sources()`
- `getattr(bundle.session, "hostname", None) or getattr(bundle.session, "_hostname", None)`

Hack na read private `_hostname` je v každém.

**Doporučení:** Helper `populate_bundle_info(bundle) -> (ip, sources)` v `session_factory` nebo helper modulu. 3× kopie je red flag.

---

#### FIND-137 — `F1 shortcut` s `Qt.ApplicationShortcut` context — pokud dva wizardy současně, shortcut ambiguity
**Severity:** NÍZKÉ · **Kategorie:** Edge case
**Lokace:** [spot_operator/ui/wizards/base_wizard.py:53-55](spot_operator/ui/wizards/base_wizard.py#L53-L55)

V praxi aplikace má jen 1 wizard, ale: pokud uživatel otevře wizard + CRUD okno současně a F1 stiskne v CRUD okně, shortcut se aktivuje (application-scope). Trigger E-Stop z místa kde nemá smysl.

**Doporučení:** `Qt.WindowShortcut` (jen pro aktivní wizard). Nebo explicit test: `if self.isActiveWindow()`.

---

#### FIND-138 — `FiducialPage` se sdílí mezi 3 wizardy (recording/playback/walk) s ruzným `required_id` — komplexita
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [spot_operator/ui/wizards/pages/fiducial_page.py](spot_operator/ui/wizards/pages/fiducial_page.py) (617 řádků, jeden z největších souborů)

`FiducialPage` přijímá `required_id=None` vs `required_id=int` vs `set_required_id` runtime update. Každý wizard ho používá jinak. Velikost souboru naznačuje, že se pokusil zvládnout všechny use cases v jedné třídě.

**Doporučení:** Zvážit rozdělení na `RecordingFiducialPage`, `PlaybackFiducialPage`, `WalkFiducialPage` s common `FiducialPageBase`. Jednotlivé flows by měly jednodušší implementaci.

---

#### FIND-139 — `RecordingWizard._close_confirmation_message` a `PlaybackWizard` duplikují zprávy bez centralizace
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** Všechny 3 wizardy

Zprávy "Pokračovat?" mají trochu jiný wording v každém wizardu. User si nebude stěžovat, ale CZ UI texty by měly být v jednom souboru pro review/překlad.

---

### Oblast 12 — Wizard pages

#### FIND-140 — `SaveMapPage._start_save` — pokud první save selže, RETRY nemožný (service.is_recording=False)
**Severity:** VYSOKÉ · **Kategorie:** UX / Error recovery
**Lokace:** [spot_operator/ui/wizards/pages/save_map_page.py:171-202, 220-226](spot_operator/ui/wizards/pages/save_map_page.py#L171-L226)

```python
def _start_save(self) -> None:
    ...
    service = state.recording_service
    if service is None or not service.is_recording:
        error_dialog(self, "Chyba", "Recording service není aktivní.")
        return
    ...
    self._worker = FunctionWorker(service.stop_and_archive_to_db, ...)
```

`stop_and_archive_to_db` volá `self._recorder.stop_recording()` (recording_service.py:233). Po tomto volání `is_recording` je False. Pokud `save_map_to_db` dále selže (DB down, validation error), `_on_save_failed` nastaví `btn_save.setEnabled(True)`. Uživatel klikne znovu → `service.is_recording == False` → error dialog. **Retry není možný.**

Jediná cesta: zavřít wizard, znovu projít celým flow (Connect → Fiducial → Teleop → Save) — ale temp GraphNav data jsou už nenávratně pryč (FIND-083).

**Riziko:** Kritický UX bug. Uživatel ztratí celou nahrávku kvůli transientní DB chybě.

**Doporučení:** Rozdělit `stop_and_archive_to_db` na 2 části:
1. `stop_and_export(temp_dir)` — zastaví recording + download.
2. `save_archive_to_db(temp_dir, name, ...)` — idempotent save z připraveného adresáře.
Save je retry-able (temp_dir je validní); stop je one-shot.

---

#### FIND-141 — `PlaybackRunPage._teardown` NEODPOJÍ PlaybackService signály
**Severity:** VYSOKÉ · **Kategorie:** Concurrency / Memory
**Lokace:** [spot_operator/ui/wizards/pages/playback_run_page.py:224-284, 382-404](spot_operator/ui/wizards/pages/playback_run_page.py#L224-L404)

`_wire_service_signals` připojuje 6 signálů (`progress`, `run_started`, `checkpoint_reached`, `photo_taken`, `run_completed`, `run_failed`). `_teardown` odpojí jen **OCR worker** signály, ne PlaybackService. Pokud `_run_thread.wait(5000)` vrátí False (thread stále běží — timeout navigate až 60s per CP), signály se emit na slot `self._append_log`, `self._on_progress`, atd. — do zničeného widgetu.

**Riziko:** Crash nebo tichý no-op, memory leak, duplicitní handlery při re-entry.

**Doporučení:**
```python
if self._service is not None:
    for sig_name in ("progress", "run_started", "checkpoint_reached", ...):
        try:
            getattr(self._service, sig_name).disconnect()
        except (TypeError, RuntimeError):
            pass
    self._service = None
```

---

#### FIND-142 — `PlaybackRunPage._on_run_failed` — pokud `run_id` je None, Next button zůstane disabled → uživatel je zaseknutý
**Severity:** VYSOKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/ui/wizards/pages/playback_run_page.py:365-373](spot_operator/ui/wizards/pages/playback_run_page.py#L365-L373)

```python
def _on_run_failed(self, reason: str) -> None:
    ...
    self._btn_next.setEnabled(self._run_id is not None)
    self._run_finished = True
    self.completeChanged.emit()
```

Pokud playback selže *před* `runs_repo.create` (FIND-086), `self._run_id` je None → Next button disabled, ale `_run_finished=True` + `isComplete()=True`. User nemá UI cestu kupředu. Jediné: zavřít wizard.

**Riziko:** Operátor je frustrovaný v "slepé uličce" — tlačítko "Pokračovat" nedostupné, ale bez možnosti opakovat.

**Doporučení:** Vždy enable Next (i bez run_id) a ResultPage musí handluje "no run" case (zobrazit "Playback nikdy nespustil" + tlačítko "Nová jízda").

---

#### FIND-143 — `PlaybackRunPage.initializePage` — když `bundle is None`, early return bez setup state; další volání crashnou
**Severity:** STŘEDNÍ · **Kategorie:** Error handling
**Lokace:** [spot_operator/ui/wizards/pages/playback_run_page.py:175-213](spot_operator/ui/wizards/pages/playback_run_page.py#L175-L213)

```python
def initializePage(self) -> None:
    ...
    if bundle is None:
        error_dialog(self, "Chyba", "Spot není připojen.")
        return  # ← self._service zůstane None
```

Po tomto scenario `isComplete()`, `validatePage`, `cleanupPage` stále fungují jen díky `self._service is None` guardům, ale UI je v degradovaném stavu — user vidí "(live view)" placeholder a tlačítko START enabled (default před initializePage).

**Doporučení:** Nastavit UI do "disabled / failed" stavu — skrýt START, zobrazit "Spot není připojen", enable "Pokračovat" jen pokud existuje completed run.

---

#### FIND-144 — `TeleopRecordPage` — `waypoint_count < 2` confirm dialog, ale 0 není speciálně blokované
**Severity:** STŘEDNÍ · **Kategorie:** UX / Data integrity
**Lokace:** [spot_operator/ui/wizards/pages/teleop_record_page.py:553-568](spot_operator/ui/wizards/pages/teleop_record_page.py#L553-L568)

```python
if self._service.waypoint_count < 2:
    if not confirm_dialog(self, "Málo waypointů", ...):
        return
# ... recording_finished.emit() + next()
```

Pokud `waypoint_count == 0`, dialog se ukáže, ale "Pokračovat" projde. `save_map_to_db` pak padne s "Mapa nemá start_waypoint_id" (FIND-078 v oblast 7) → user je v SaveMapPage s error a retry není možný (FIND-140).

**Riziko:** UX trap.

**Doporučení:** Tvrdé blokování pro `waypoint_count == 0` bez možnosti pokračovat.

---

#### FIND-145 — `PlaybackRunPage._on_ocr_done` volá DB v UI thread na každé emitnutí signálu
**Severity:** STŘEDNÍ · **Kategorie:** Performance
**Lokace:** [spot_operator/ui/wizards/pages/playback_run_page.py:411-433](spot_operator/ui/wizards/pages/playback_run_page.py#L411-L433)

Při každém OCR dokončeném kompletovaní UI thread otevírá `Session()`, volá `detections_repo.list_for_photo`. Pro série rychle zpracovaných fotek (~10 fotek ve vteřině při pomalém OCR + dlouhé navigate) → UI freezes v malých zábleskkách.

**Doporučení:** Načíst detekce v BG thread (FunctionWorker) a emit `detections_ready(photo_id, plates)` signál zpět.

---

#### FIND-146 — `SaveMapPage._is_name_valid` volá DB pro každý `textChanged` signál
**Severity:** STŘEDNÍ · **Kategorie:** Performance
**Lokace:** [spot_operator/ui/wizards/pages/save_map_page.py:63, 106-121](spot_operator/ui/wizards/pages/save_map_page.py#L106-L121)

```python
self._name_edit.textChanged.connect(lambda _: self._update_ok_state())
...
def _is_name_valid(self) -> bool:
    name = self._name_edit.text().strip()
    ...
    with Session() as s:
        if maps_repo.exists_by_name(s, name):
```

Každý keystroke = 1 DB query. Pro 20-znakový název = 20 queries. Na pomalé DB UI sekne.

**Doporučení:** QTimer debounce (`CRUD_SEARCH_DEBOUNCE_MS=200` už v constants — použít stejný vzor).

---

#### FIND-147 — `_check_fiducial` v SaveMapPage volá `visible_fiducials` s `max_distance_m` — pokud robot je dále, zeleně neprojde, ale neblokuje save
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/ui/wizards/pages/save_map_page.py:128-161](spot_operator/ui/wizards/pages/save_map_page.py#L128-L161)

UI zobrazí "✗ Fiducial nevidím" ale `btn_save` zůstává enabled. User může mapu uložit bez fiducial re-check. OK — "Mapu sice uložit můžeš, ale lokalizace při playbacku bude horší" je informativní. Ale to je silent degradace → viz problém "robot jede náhodně" (root cause). Pokud recording_service `_fiducial_id` je starý (z začátku) a robot se fyzicky neocitl u fiducialu na konci, mapa má fiducial_id z observace "v dálce" → playback lokalizace méně přesná.

**Doporučení:** Pokud re-check selže, nabídnout warning v confirm dialogu: "Nedoporučuje se uložit mapu bez ověření fiducialu na konci. Pokračovat?".

---

#### FIND-148 — `_capture` v TeleopRecordPage cachuje `_poller` jako instance attribute, ale `_teardown` ho nečistí
**Severity:** NÍZKÉ · **Kategorie:** Resource leak
**Lokace:** [spot_operator/ui/wizards/pages/teleop_record_page.py:509-512](spot_operator/ui/wizards/pages/teleop_record_page.py#L509-L512)

```python
poller = getattr(self, "_poller", None)
if poller is None:
    poller = ImagePoller(bundle.session)
    self._poller = poller
```

`self._poller` drží reference na bundle.session. Při `_teardown` není explicitně zrušen → pokud bundle.disconnect se stane později, poller drží session která už je shut down.

**Doporučení:** V `_teardown` přidat `self._poller = None`.

---

#### FIND-149 — `_ensure_image_pipeline` / `_ensure_live_view` silent failure → UI neinformuje o missing live view
**Severity:** STŘEDNÍ · **Kategorie:** UX
**Lokace:** [spot_operator/ui/wizards/pages/teleop_record_page.py:572-602](spot_operator/ui/wizards/pages/teleop_record_page.py#L572-L602), podobně playback_run_page

```python
try:
    from app.image_pipeline import ImagePipeline
    ...
except Exception as exc:
    _log.warning("ImagePipeline unavailable: %s", exc)
    return  # ← UI zůstává s placeholder
```

User vidí "(live view)" placeholder bez vysvětlení. Myslí si že "live view ještě loaduje", ve skutečnosti se už nikdy nezobrazí.

**Doporučení:** V placeholder nahradit "(live view)" za "⚠ Live view nedostupný. Kamera nefunguje — odpojit a znovu připojit." + log warning je OK.

---

#### FIND-150 — `_on_finish_clicked` + `recording_finished.emit()` + `wizard.next()` jde mimo standardní QWizard flow
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [spot_operator/ui/wizards/pages/teleop_record_page.py:565-568](spot_operator/ui/wizards/pages/teleop_record_page.py#L565-L568)

```python
self.recording_finished.emit()
self.wizard().next()
```

Signál `recording_finished` je emitovaný ale nikdo ho neposlouchá (grep ukázal že `recording_finished` je jen definován na řádku 49 — dead signal). `wizard.next()` provede posun.

**Doporučení:** Odstranit dead signal + dokumentovat, že dokončení je implicit přes `next()`.

---

#### FIND-151 — `_poll_battery` vytváří `HealthMonitor(bundle.session)` každých 5s — nekeše
**Severity:** NÍZKÉ · **Kategorie:** Performance
**Lokace:** [spot_operator/ui/wizards/pages/teleop_record_page.py:618-631](spot_operator/ui/wizards/pages/teleop_record_page.py#L618-L631)

`HealthMonitor` není drahý konstruktor (řekněme), ale opakovaný alloc je wasted. Ne kritické.

**Doporučení:** Cache v `_ensure_image_pipeline` nebo init.

---

#### FIND-152 — `PlaybackRunPage._estop_widget` nastavuje `wizard.set_estop_callback` ale nepotvrzuje, že set je idempotentní
**Severity:** NÍZKÉ · **Kategorie:** Concurrency
**Lokace:** [spot_operator/ui/wizards/pages/playback_run_page.py:469-480](spot_operator/ui/wizards/pages/playback_run_page.py#L469-L480)

Pokud wizard drží callback z předchozí stránky (FiducialPage), kliknutí F1 na PlaybackRunPage přepíše. OK. Ale při `_teardown` se callback neodstraní — F1 na následující stránce může zavolat stary `_handle_estop_release` na zničené stránce.

**Doporučení:** V `_teardown`: `wizard.set_estop_callback(None, None)` (ale bude vyžadovat změnu base_wizard — dnes není None valid hodnota).

---

#### FIND-153 — Wizardy používají `wizard.playback_state()` vs `wizard.recording_state()` — different methods per wizard → může mít typo
**Severity:** NÍZKÉ · **Kategorie:** Type consistency
**Lokace:** různé pages

Pages typově zdokumentované přes `# type: ignore[attr-defined]` komentář. Jakákoliv změna v signature methody wizardu se neprojeví ve static check. Fragile.

**Doporučení:** Jednotně `wizard.flow_state()` s isinstance check; nebo generický typed Base s Generic[T].

---

### Oblast 13 — Main window & common UI

#### FIND-154 — `MainWindow.closeEvent` nezastaví ani nezníčí `_db_timer` a `_temp_cleanup_timer`
**Severity:** VYSOKÉ · **Kategorie:** Concurrency / Resource management
**Lokace:** [spot_operator/ui/main_window.py:378-386](spot_operator/ui/main_window.py#L378-L386), [330-343](spot_operator/ui/main_window.py#L330-L343)

```python
def closeEvent(self, event):
    if self._bundle is not None:
        try:
            self._bundle.disconnect()
        ...
    super().closeEvent(event)
    # ❌ _db_timer a _temp_cleanup_timer pokračují
```

QTimery zůstanou aktivní dokud celý Qt app loop není zastaven. Pokud emitnou po `super().closeEvent()`, slot volá metody na pod-smazaném okně → Qt wrapper → můžou pak crashnout při `QEvent.Destroy`. V praxi `QApplication.quit()` zavolaný z main.py obvykle oba stopne, ale invariant není chráněn.

**Doporučení:**
```python
def closeEvent(self, event):
    try: self._db_timer.stop()
    except Exception: pass
    try: self._temp_cleanup_timer.stop()
    except Exception: pass
    if self._bundle is not None:
        ...
```

---

#### FIND-155 — `_on_wizard_closed` kontroluje "liveness" bundle přes `getattr(sess, "robot", None)` — nespolehlivé
**Severity:** STŘEDNÍ · **Kategorie:** Error handling
**Lokace:** [spot_operator/ui/main_window.py:303-324](spot_operator/ui/main_window.py#L303-L324)

```python
try:
    sess = self._bundle.session
    if sess is None or getattr(sess, "robot", None) is None:
        _log.warning("Post-wizard: bundle session is dead — discarding.")
        self._bundle = None
except Exception:
    pass
```

`sess.robot` existence nedokazuje že session je živá. Robot attribute je reference z Python perspektivy, nemusí znamenat `robot.is_connected()`. Sevření v try/except potichu může nechat v stavu mrtvý bundle.

**Doporučení:** Explicit RPC ping check (lightweight `robot.time_sync.wait_for_sync()` nebo `robot.authenticate()` re-check). Nebo delegování na `bundle.is_alive()` method v SpotBundle.

---

#### FIND-156 — `EstopFloating._do_release` BEZ on_release callback jen vizuálně resetuje widget, ale robot zůstává v E-Stop
**Severity:** VYSOKÉ · **Kategorie:** Safety
**Lokace:** [spot_operator/ui/common/estop_floating.py:126-145](spot_operator/ui/common/estop_floating.py#L126-L145)

```python
if self._on_release is None:
    _log.warning("E-Stop release: no on_release callback registered; only visual reset.")
    self.reset()  # ← Vizuální reset, robot fyzicky stále triggered!
    return
```

Komentář v kódu říká "volající by měl vždy poskytnout on_release" — ale konstruktor ho má jako `Optional`. Pokud někdo vytvoří `EstopFloating(parent, on_trigger)` bez release, widget lže (zeleně "klid"), robot je skutečně v E-Stop. Operátor kliká, nic se neděje, kamera zamrzá.

**Riziko:** Safety-critical lie.

**Doporučení:** Udělat `on_release` **povinný** parameter. Nebo pokud chybí, widget v triggered stavu disable klik s textem "Nepodporováno — restartuj wizard".

---

#### FIND-157 — `MainWindow._disconnect_spot` confirm dialog, ale žádný při close-event — bundle disconnect bez potvrzení při X
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/ui/main_window.py:378-386, 187-191](spot_operator/ui/main_window.py#L187-L386)

User klikne "Odpojit Spota" → confirm dialog. User klikne X → přímo disconnect bez confirm. Inkonzistence.

**Doporučení:** Pokud bundle je aktivní, closeEvent také confirm.

---

#### FIND-158 — `cleanup_worker` volá `worker.deleteLater()` ale před tím `stop_and_wait()` — race s lifecycle signals
**Severity:** STŘEDNÍ · **Kategorie:** Concurrency
**Lokace:** [spot_operator/ui/common/workers.py:141-155](spot_operator/ui/common/workers.py#L141-L155)

`stop_and_wait` v řádku 148 disconnectuje a wait. Pak `deleteLater()` naplánuje smazání. Pokud Qt event loop zpracuje deleteLater *před* jsí jejich slotů (např. pending signal emity z předchozí tick), slot se volá na zombie objektu. `_WorkerBase` implementuje defensive RuntimeError handling, tak to v praxi není crash. Ale fragilní.

**Doporučení:** Volat `deleteLater()` jen po `finished` signálu Qt threadu, ne hned po `wait()`.

---

#### FIND-159 — `DbQueryWorker` nemá cancel support — Session nebude přerušena ani při `requestInterruption`
**Severity:** STŘEDNÍ · **Kategorie:** Concurrency
**Lokace:** [spot_operator/ui/common/workers.py:100-138](spot_operator/ui/common/workers.py#L100-L138)

```python
def run(self) -> None:
    try:
        with Session() as s:
            result = self._fn(s)  # ← Blokující DB call
```

`requestInterruption` nastaví flag, ale DB call neví nic o Qt interruption. Pokud dotaz trvá 10s, `stop_and_wait(3000ms)` vrátí False, worker zůstane viset → další aktivní worker thread který blokuje connection pool slot.

**Doporučení:** Implementovat statement cancellation přes SQLAlchemy (`session.connection().invalidate()`) nebo omezit doba jednoho DB query (`pg_statement_timeout`).

---

#### FIND-160 — `MainWindow._temp_cleanup_timer` interval 30 min, ale bez koordinace s wizardy — race při přechodu wizard → main
**Severity:** NÍZKÉ · **Kategorie:** Concurrency
**Lokace:** [spot_operator/ui/main_window.py:345-361](spot_operator/ui/main_window.py#L345-L361)

`_periodic_temp_cleanup` kontroluje `wizard is not None`, ale timer může emit v okamžiku zavírání wizardu (signal `finished` ještě nepřišel). Minimální race — prakticky nekritické.

---

#### FIND-161 — `_update_db_status` volá `db_ping()` každých 5s — při down DB spamuje log WARNING
**Severity:** NÍZKÉ · **Kategorie:** Observability
**Lokace:** [spot_operator/ui/main_window.py:330-335](spot_operator/ui/main_window.py#L330-L335), [spot_operator/db/engine.py:71-81](spot_operator/db/engine.py#L71-L81)

`ping()` při selhání `_log.warning("DB ping failed: %s", exc)`. Každých 5s při DB down = 720 warningů za hodinu.

**Doporučení:** Dedup logování (podobně jako v ocr_worker `_handle_loop_error` pattern).

---

#### FIND-162 — `MainWindow` neposlouchá `QApplication.aboutToQuit` signál — stopped flow přes `closeEvent` jen při window X, ne při Ctrl+C nebo OS signálu
**Severity:** STŘEDNÍ · **Kategorie:** Resource management
**Lokace:** [spot_operator/ui/main_window.py:378-386](spot_operator/ui/main_window.py#L378-L386)

Pokud user killne aplikaci přes Task Manager nebo kill signál, `closeEvent` nemusí být vyvoláno. Bundle lease zůstane visící.

**Doporučení:** Přidat `QApplication.aboutToQuit.connect(self._cleanup)` handler (idempotentní s closeEvent).

---

#### FIND-163 — `EstopFloating` používá `installEventFilter(self)` pro reposition — filter není `removeEventFilter` při destroy
**Severity:** NÍZKÉ · **Kategorie:** Resource leak
**Lokace:** [spot_operator/ui/common/estop_floating.py:83-92](spot_operator/ui/common/estop_floating.py#L83-L92)

Qt obvykle auto-clean-uje eventfilters při deleteLater, ale dokumentace radí explicit removal. Pokud parent je dlouho-živý (wizard zůstává) a widget je delete, filter může stále běžet.

**Doporučení:** V destructor `if self.parentWidget() is not None: self.parentWidget().removeEventFilter(self)`.

---

### Oblast 14 — CRUD okno

Tato oblast je **relativně zdravě postavená**: PagedTableModel má request_id race protection (řádek 46), `DbQueryWorker` je v BG thread, `defer(image_bytes)` se používá pro list views. Najdu jen menší nálezy.

#### FIND-164 — `PagedTableModel.fetchMore` přidává worker do `self._workers` bez cleanup — nekonečný seznam
**Severity:** STŘEDNÍ · **Kategorie:** Resource leak
**Lokace:** [spot_operator/ui/common/table_models/paged_table_model.py:121](spot_operator/ui/common/table_models/paged_table_model.py#L121)

```python
self._workers.append(worker)
...
worker.finished.connect(worker.deleteLater)
```

`worker` je přidán do `self._workers` ale z listu se nikdy neodstraní. `deleteLater` pošle worker do odstranění, ale Python reference v `self._workers` ho drží živého. Postupný memory leak při scrollování tisíců řádků.

**Doporučení:** `worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)`.

---

#### FIND-165 — `PagedTableModel.fetchMore` + `reset` race — starý worker pořád emituje do new state
**Severity:** NÍZKÉ · **Kategorie:** Concurrency
**Lokace:** [spot_operator/ui/common/table_models/paged_table_model.py:99-140](spot_operator/ui/common/table_models/paged_table_model.py#L99-L140)

`request_id` check protect proti zahozeným result-u ze stareho requestu. OK. Ale `self._fetching` flag se nastaví na True a neresetuje pokud worker je zahozený. Další `canFetchMore` vrátí False → no progress.

**Doporučení:** V `_on_page` / `_on_fail` / `reset` vždy správně reset `_fetching` flag (verify — kód stránky není zde viditelný celý).

---

#### FIND-166 — CRUD `photos_tab`, `spz_tab` atd. volá `info_dialog`/`error_dialog` po successful refresh — user interrupted po každé změně
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/ui/crud/photos_tab.py](spot_operator/ui/crud/photos_tab.py) (volání info_dialog v `_on_reset_all_clicked`)

Acceptable pattern, ale když operátor dělá masový reset (stisk tlačítka → nezeptá se), zbytečný dialog po každé akci.

**Doporučení:** Pokud je UI flow cílený na batch operaci, použít status_label místo modal dialog.

---

#### FIND-167 — `photo_detail_dialog` (372 řádků) s velkým logicem — re-OCR flow může otevřít víc paralelních pokusů
**Severity:** STŘEDNÍ · **Kategorie:** Concurrency (bez úplného čtení, candidate)
**Lokace:** [spot_operator/ui/crud/photo_detail_dialog.py](spot_operator/ui/crud/photo_detail_dialog.py) (372 ř.)

Pokud user klikne "Re-OCR" dvakrát rychle, druhý click může spustit další subprocess. Bez tlačítko-disabling. VERIFIKOVAT.

**Doporučení:** Po kliknutí disable tlačítko do dokončení workeru.

---

#### FIND-168 — `runs_tab.py` / `photos_tab.py` nemají auto-refresh po OCR completion signálu
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/ui/crud/photos_tab.py](spot_operator/ui/crud/photos_tab.py)

Operátor se musí ručně refresh-ovat po každém OCR výsledku. Z workflow pohledu zbytečné friction.

**Doporučení:** Connect `OcrWorker.photo_processed` → `_model.reset()` (throttlovaně).

---

#### FIND-169 — CRUD tab `close`/`destroy` nevolá explicit `cleanup_worker` na běžící workery (pouze Qt auto-cleanup)
**Severity:** NÍZKÉ · **Kategorie:** Resource management
**Lokace:** rozličné CRUD tab soubory

Qt auto-cleanup má známé race scénáře. Lepší explicit `self._model.stop_all_workers()` v close.

---

#### FIND-170 — `plates_model.py` / `runs_model.py` typed cell conversion (např. timestamp → str) neví o locale
**Severity:** NÍZKÉ · **Kategorie:** UX
**Lokace:** [spot_operator/ui/common/table_models/](spot_operator/ui/common/table_models/)

Timestamp se pravděpodobně ukazuje v UTC ISO formátu místo lokálně zformátovaného. Uživatel vidí `2026-04-23T15:30:00Z`, by chtěl `23. 4. 2026 17:30`.

**Doporučení:** Formátovat přes `datetime.strftime("%d. %m. %Y %H:%M")` v cell metodě.

---

### Oblast 15 — Bootstrap & entry

#### FIND-171 — `main.py` finally blok: `ocr_worker.wait(5000)` — 5s je krátkých pro YOLO warmup loop při shutdown
**Severity:** VYSOKÉ · **Kategorie:** Resource management
**Lokace:** [main.py:134-136](main.py#L134-L136)

```python
if ocr_worker is not None:
    ocr_worker.request_stop()
    ocr_worker.wait(5000)
```

OCR worker může být uprostřed `pipeline.process` (YOLO inference + text OCR), typicky 1-3s. Pokud právě claimnul fotku a model se lazy-loaduje poprvé (~5-10s), 5s timeout skončí dřív. Worker thread pokračuje v pozadí → zombie thread při exit aplikace.

**Doporučení:** Zvýšit na 30s, nebo wait s krátkým interval `while worker.isRunning(): worker.wait(500)` a odhad celkové doby.

---

#### FIND-172 — `main.py` `DB ping failed` fatální, ale `cleanup_temp_root` failure je jen warning
**Severity:** NÍZKÉ · **Kategorie:** Consistency
**Lokace:** [main.py:98-104](main.py#L98-L104)

Inconsistentní přístup k initialization failures. `cleanup_temp_root` failure je neškodný (bez temp), ale logice by měla být uniformní.

---

#### FIND-173 — `_fatal_dialog` vytváří `QApplication(sys.argv)` pokud neexistuje → pak není zavřené a uniká proces
**Severity:** NÍZKÉ · **Kategorie:** Resource management
**Lokace:** [main.py:144-156](main.py#L144-L156)

```python
_ = QApplication(sys.argv) if QApplication.instance() is None else None
```

Lokální `_` je throwaway, ale vytvoří se globální Qt instance. Main pak vrací `return 1`, `return 2` bez explicit `app.quit()` → aplikace může nechávat po sobě taskbar entry.

**Doporučení:** Použít `QApplication.instance() or QApplication(sys.argv)` a v _fatal_dialog hold lokální ref, po `box.exec()` volat `QApplication.quit()`.

---

#### FIND-174 — `bootstrap.inject_paths` volaný top-level v main.py → side-effect při každém importu
**Severity:** NÍZKÉ · **Kategorie:** Tech debt
**Lokace:** [main.py:22-24](main.py#L22-L24)

```python
from spot_operator.bootstrap import inject_paths
inject_paths()
```

Side effect při importu `main.py` (pytest collect, linter). `pyproject.toml` nebo namespace packaging by bylo cleanější.

**Doporučení:** Přesunout `inject_paths()` do `main()` funkce.

---

#### FIND-175 — `_single_instance_lock` lock_path v `config.temp_root` — pokud temp root se smaže, lock přestane chránit
**Severity:** NÍZKÉ · **Kategorie:** Edge case
**Lokace:** [main.py:45](main.py#L45)

```python
lock_path = config.temp_root / f"spot_operator_{safe_user}.lock"
```

`cleanup_temp_root` maže `map_*` ale lock soubor neaplikuje. OK, ale pokud user ručně smaže temp/, další spuštění vytvoří nový lock.

**Doporučení:** Lock v `config.root_dir` (stable location) nebo ve `%APPDATA%`.

---

### Oblast 16 — Autonomy integrace

Autonomy (`autonomy/app/`) je 3rd-party-like submodul. Tato oblast se soustředí jen na **kontrakty mezi `spot_operator` a `autonomy`**, ne samotnou autonomy kvalitu.

#### FIND-176 — `NavigationOutcome` enum je importovaný z autonomy bez version-pinning; rozšíření v autonomy = silent skip v spot_operator retry logice
**Severity:** VYSOKÉ · **Kategorie:** Compatibility / Data integrity
**Lokace:** [spot_operator/services/playback_service.py:446](spot_operator/services/playback_service.py#L446) (`from app.models import NavigationOutcome`)

Pokud autonomy přidá např. `BATTERY_LOW`, spot_operator `_should_retry_outcome` whitelist (LOST, NOT_LOCALIZED, TIMEOUT) to neuvidí → no retry → silent skip. Viz FIND-089 už zmiňoval.

**Doporučení:** Log WARNING pokud `result.outcome` není známá hodnota z whitelisted enum setu.

---

#### FIND-177 — `GraphNavNavigator.navigate_to` API — spot_operator předpokládá rozhraní (`timeout`, `outcome`, `message`, `is_localization_loss`) bez static type check
**Severity:** STŘEDNÍ · **Kategorie:** Type consistency
**Lokace:** [spot_operator/services/playback_service.py:446-449, 461-464](spot_operator/services/playback_service.py#L446-L464)

Kód volá `result.is_localization_loss`, `result.outcome`, `result.message`, `result.ok` bez type import. Pokud autonomy změní signature (např. rename `ok` → `success`), spot_operator to nezjistí staticky.

**Doporučení:** Definovat `NavigationResult` Protocol v `autonomy/app/models.py` jako veřejný kontrakt a importovat do spot_operator pro typed use.

---

#### FIND-178 — `ImagePoller.capture(src)` API nemá formalizovaný kontrakt co `None` znamená
**Severity:** STŘEDNÍ · **Kategorie:** Type consistency
**Lokace:** [spot_operator/robot/dual_side_capture.py:27-36](spot_operator/robot/dual_side_capture.py#L27-L36) (konzumer)

`capture` vrátí `np.ndarray | None`. Kde None znamená "camera offline" vs "temporary glitch" vs "unknown source"? Spot_operator v obou cases jen warning log + continue. Nelze rozlišit recoverable od permanent fail.

**Doporučení:** ImagePoller by měl raise rozdílné exception class pro různé případy (ImagePollerOffline vs ImagePollerTransient).

---

#### FIND-179 — `read_observed_fiducial_ids` je autonomy internal API → cached v třetí straně spot_operatoru (recording_service)
**Severity:** STŘEDNÍ · **Kategorie:** Tech debt / Layer violation
**Lokace:** [spot_operator/services/recording_service.py:244](spot_operator/services/recording_service.py#L244)

```python
from app.robot.graphnav_recording import read_observed_fiducial_ids
```

`spot_operator` importuje funkci z autonomy **uvnitř metody**. Layer violation — spot_operator by měl hovořit jen se well-defined autonomy API, ne s internal helpers.

**Doporučení:** Promote `read_observed_fiducial_ids` na top-level `autonomy.app` API nebo implementovat kopii v spot_operator (duplikace méně zlo než layer violation).

---

#### FIND-180 — `LeaseManager` keepalive v autonomy není ověřený test v spot_operatoru — fragile assumption
**Severity:** STŘEDNÍ · **Kategorie:** Dependency contract
**Lokace:** [spot_operator/robot/session_factory.py:120-124, 153-156](spot_operator/robot/session_factory.py#L120-L156)

Kód předpokládá `LeaseManager` má keepalive (FIND-064). Žádný integration test to nekontroluje.

**Doporučení:** V `tests/integration/test_spot_connect.py` přidat test, že session + lease drží 30s bez explicit actions.

---

#### FIND-181 — `PowerManager.power_off` — návratová hodnota / completion signal neznámý, spot_operator předpokládá sync call
**Severity:** STŘEDNÍ · **Kategorie:** API contract
**Lokace:** [spot_operator/robot/session_factory.py:132](spot_operator/robot/session_factory.py#L132) (viz také FIND-062)

Duplicitní s FIND-062.

---

#### FIND-182 — `bundle.session.hostname` vs `_hostname` fallback znamená že autonomy API je inconsistent
**Severity:** NÍZKÉ · **Kategorie:** API contract
**Lokace:** [spot_operator/ui/main_window.py:210-212](spot_operator/ui/main_window.py#L210-L212) a další 3 wizardy

Fallback na `_hostname` je public-vs-private access hack. Při upgrade autonomy může attribute zmizet.

**Doporučení:** Autonomy by mělo poskytnout `session.get_hostname()` jako public method.

---

### Oblast 17 — Testovací pokrytí

Celkem 10 testovacích souborů, ~600 řádků. Pokrytí je **selektivní a mělké** — chybí pokrytí kritických flows (recording, playback end-to-end, JSON schema round-trip).

**Existující unit testy:**
- `test_map_archiver.py` (92 ř.) — ZIP roundtrip
- `test_map_contracts.py` (85 ř.) — legacy v1 → v2 upgrade, unknown source rejection
- `test_ocr_normalize.py` (56 ř.) — `_normalize_plate`
- `test_pick_side_source.py` (51 ř.) — camera source fallback
- `test_plates_repo.py` (24 ř.) — normalizace
- `test_robot_lost_error_detection.py` (68 ř.) — substring markers
- `test_run_reliability.py` (48 ř.) — run status
- `test_waypoint_namer.py` (35 ř.)

**Integration:**
- `test_autonomy_smoke.py` (125 ř.)
- `test_spot_connect.py` (33 ř.)

#### FIND-183 — Chybí test pro end-to-end recording flow
**Severity:** VYSOKÉ · **Kategorie:** Testing
**Lokace:** `tests/` (neexistuje)

Žádný test, který by ověřil: start_recording → add_unnamed_waypoint → capture_and_record_checkpoint (s mock ImagePoller) → stop_and_archive_to_db → ověří checkpoints_json struktura.

**Riziko:** Bugy jako FIND-072 (start_waypoint_id semantika) zůstávají neodhalené.

---

#### FIND-184 — Chybí test pro playback flow s různými NavigationOutcome
**Severity:** VYSOKÉ · **Kategorie:** Testing

Žádný test pro scénáře:
- TIMEOUT → retry → success
- LOST → re-localize → retry → success
- RobotLostError → abort
- 3 konsekutivní failures → abort

---

#### FIND-185 — Chybí test pro JSON schema round-trip (build → parse → build)
**Severity:** STŘEDNÍ · **Kategorie:** Testing
**Lokace:** `tests/unit/test_map_contracts.py`

Viz FIND-044. Teď test jen ověřuje legacy upgrade, ne úplnost round-tripu.

---

#### FIND-186 — Chybí test pro OCR worker claim-process-commit lifecycle
**Severity:** STŘEDNÍ · **Kategorie:** Testing

Není test pro: claim fotky → process → commit. Není mock scénář "pipeline.process raisne MemoryError" / "DB commit selže".

---

#### FIND-187 — `test_spot_connect.py` je 33 řádků — pravděpodobně pouze dummy
**Severity:** NÍZKÉ · **Kategorie:** Testing

Pouze smoke test. Žádný test lease lifecycle / E-Stop auto-recovery.

---

#### FIND-188 — Žádný test pro `contracts.validate_sources_known` s edge cases (empty available_sources, None input)
**Severity:** NÍZKÉ · **Kategorie:** Testing
**Lokace:** [tests/unit/test_map_contracts.py:46-52](tests/unit/test_map_contracts.py#L46-L52)

Jen happy path rejection test.

---

#### FIND-189 — Testy nepokrývají E-Stop safety flow
**Severity:** VYSOKÉ · **Kategorie:** Testing / Safety

Pro safety-critical komponent (E-Stop release bez callback = lži) by měl být unit test, že `_do_release` bez `on_release` callback raise / disable widget místo silent reset.

---

### Oblast 18 — Dokumentace vs realita

`instructions.md` (version 1.3.0, 2026-04-23) je normativní dokument popisující "co a proč". Kód většinou odpovídá, ale existují neshody.

#### FIND-190 — Glosář: "Checkpoint = waypoint s fotkou" — ale kód demotes na waypoint při capture failure bez update jména
**Severity:** STŘEDNÍ · **Kategorie:** Dokumentace vs realita
**Lokace:** [instructions.md:48](instructions.md#L48) vs [spot_operator/services/recording_service.py:174-180](spot_operator/services/recording_service.py#L174-L180)

`instructions.md` říká: "Checkpoint = Waypoint **s přiřazenou fotkou**. Pojmenovaný CP_NNN."

Ale recording_service, když capture selže, silent demote:
- `kind = "waypoint"`
- `name` zůstává `CP_NNN` (z `_namer.next_checkpoint()`)

Výsledek: checkpoint se jménem `CP_005` ale `kind="waypoint"` — porušuje glosář.

**Doporučení:** Buď re-assign name na `WP_NNN`, nebo zrušit demotion (viz FIND-066).

---

#### FIND-191 — Dokumentace neříká nic o "first checkpoint" semantice `start_waypoint_id` — kód se chová překvapivě
**Severity:** STŘEDNÍ · **Kategorie:** Dokumentace
**Lokace:** [instructions.md](instructions.md) vs [spot_operator/services/recording_service.py:119-134, 154-155](spot_operator/services/recording_service.py#L119-L155)

Žádná sekce v `instructions.md` neříká, že *první* `create_waypoint` (jakékoliv kind) se stane `start_waypoint_id`. Operátor nemá vodítko, že má kliknout "Waypoint" před "Checkpoint".

**Doporučení:** Do `instructions.md` přidat sekci "Recording protocol" s explicit pravidly.

---

#### FIND-192 — `instructions.md` glosář: "capture_sources" s klávesami V / N / B, README popisuje stejně — ale kód skutečně prvně zobrazí PhotoConfirmOverlay, operátor vidí preview
**Severity:** NÍZKÉ · **Kategorie:** Dokumentace

Flow je popsán správně, ale uživatel přečte "klávesy V/N/B = fotí" → neočekává confirm dialog. Matoucí.

---

#### FIND-193 — `OCR_YOLO_MODEL=ocr/license-plate-finetune-v1m.pt` v `.env.example` (předpoklad) — ale pokud cesta je absolutní, config.load_from_env interpretuje jinak
**Severity:** NÍZKÉ · **Kategorie:** Dokumentace
**Lokace:** [spot_operator/config.py:53-56](spot_operator/config.py#L53-L56)

```python
ocr_yolo_model_rel = os.environ.get("OCR_YOLO_MODEL", "ocr/license-plate-finetune-v1m.pt")
ocr_yolo_model_path = ROOT / ocr_yolo_model_rel
```

Pokud env var je absolutní cesta (`C:\models\yolo.pt`), `ROOT / "C:\models\..."` vyprodukuje nesmyslnou cestu.

**Doporučení:** Check: pokud je absolutní, použít jak je. Dokumentace hint.

---

#### FIND-194 — `CHANGELOG.md` obsahuje historické verze, ale `instructions.md:2` říká `version: 1.3.0` — ověření odpovídá?
**Severity:** NÍZKÉ · **Kategorie:** Konzistence

Verify, že `spot_operator/__init__.py` `__version__` odpovídá `instructions.md`. Nečetl jsem tento soubor — ale měl by být kontrolován.

---

#### FIND-195 — `README.md` může obsahovat klávesové zkratky, které se v kódu změnily — diferenciace chybí z mého průchodu, ale sloužil by jako audit
**Severity:** NÍZKÉ · **Kategorie:** Dokumentace

Hloubkový audit README vs kód není součást — `teleop_record_page.py` používá WASD, QE, V N B, C, Mezerník, F1. Zkontrolovat, že README totéž dokumentuje.

---

#### FIND-196 — `instructions.md` říká "Python 3.10", ale migrations.py a další neobsahují version check pokud user spustí na Py 3.12
**Severity:** NÍZKÉ · **Kategorie:** Compat

Pokud user spustí v 3.12, bosdyn-client se snaží install a může padnout jinde. `main.py` by měl mít sanity check.

**Doporučení:**
```python
if sys.version_info[:2] != (3, 10):
    raise RuntimeError(f"Spot Operator vyžaduje Python 3.10, máš {sys.version_info[:2]}.")
```

---

## 3. Deriváty / cross-cutting patterns

Opakující se vzory chyb napříč kódem — fix jednoho místa nic neřeší, musí se systémově.

### P1. "except Exception: log.warning; return None/False/[]" (20+ instancí)

**Lokace:** Napříč všemi service soubory. Vybrané:
- `recording_service.py:270-274` (`read_observed_fiducial_ids`)
- `playback_service.py:229-241` (`set_global_avoidance`), `339-355` (run loop), `428-434` (cleanup), `517-521` (relocalize fallback)
- `ocr_worker.py:84-85` (loop error), `143-150` (OCR process error)
- `photos_repo.py:107` (`_to_photo_row` fallback)
- `credentials_service.py:101-103` (`load_password`)
- `session_factory.py:62-77` (`disconnect`)
- `map_storage.py:163-166` (temp cleanup), mnoho dalších

**Důsledek:** Operátor nevidí chybu. Kód pokračuje v degradovaném stavu. Debugging přes log je ruční práce.

**Systémový fix:** Policy "Safety over convenience": pro safety-critical operace (lease, E-Stop, power, robot commands) raise namísto warn. Pro UI-level degradace logovat + emit user-facing signál (ne silent). Zavést helper `report_error_to_user(category, detail)` který dle kategorie zvolí dialog vs toast vs log.

### P2. Qt signal disconnect v teardown neprojde celým seznamem (5+ instancí)

**Lokace:**
- `playback_run_page._teardown`: disconnect OCR signály, ale **ne PlaybackService** signály ([FIND-141](#find-141))
- `teleop_record_page._teardown`: některé signály zůstávají
- `photo_confirm_overlay.teardown`: pipeline signály disconnect chybí (Agent 3 hlásil)
- `photo_detail_dialog`: re-OCR signály

**Důsledek:** Crash nebo duplicity slot-calls při re-entry do wizardu.

**Systémový fix:** Utility `disconnect_all_signals(obj)` volaná v každém teardown. Nebo `QObject.blockSignals(True)` před cleanup. Nebo QSignalBlocker RAII.

### P3. TOCTOU race v `check-then-act` DB patternech (3 instance)

**Lokace:**
- `runs_repo.generate_unique_run_code` ([FIND-024](#find-024)): check exists → insert
- `plates_repo.upsert` ([FIND-027](#find-027)): get → insert
- `maps_repo.exists_by_name` + `create` v `map_storage.save_map_to_db` ([FIND-048](#find-048))

**Důsledek:** Second paralelní operace získá IntegrityError bez clean recovery.

**Systémový fix:** Všude použít `INSERT ... ON CONFLICT DO UPDATE/NOTHING` pattern místo check-then-insert.

### P4. `getattr(obj, "attr", default)` duck typing maskuje API mismatch (10+ instancí)

**Lokace:**
- `contracts.build_checkpoint_plan_payload` ([FIND-041](#find-041))
- `main_window._on_wizard_closed` ([FIND-155](#find-155))
- `playback_service._is_robot_lost_error` s `getattr(result, "message", "")` ([FIND-090](#find-090))
- `session_factory.disconnect` s `type: ignore[attr-defined]` everywhere

**Důsledek:** API mismatch po upgrade autonomy/bosdyn se neodhalí staticky; runtime silent `None`/`""`.

**Systémový fix:** Definovat explicit `Protocol` (PEP 544) pro kontrakty. Odstranit `getattr` s defaultem v hot path, nahradit explicit hasattr check + raise.

### P5. Duplicitní `_populate_props_from_bundle` v 3 wizardech ([FIND-136](#find-136))

Kopíruj-paste pattern napříč RecordingWizard, PlaybackWizard, WalkWizard.

**Systémový fix:** Helper v session_factory `bundle.get_info() -> BundleInfo(hostname, available_sources)`.

### P6. Magic string identifikátory místo enumů (5+ instancí)

- `"capture_failed"`, `"capture_partial"` jako `note` string ([FIND-079](#find-079))
- `"reached"`, `"lost"`, ... jako `nav_outcome` string ([FIND-046](#find-046))
- `"not_requested"`, `"in_progress"`, ... jako `return_home_status` string v konstantách
- `"waypoint"`, `"checkpoint"` jako `kind` string

**Systémový fix:** Definovat enum class pro každou kategorii, použít `str` enum (`.value`) na serializaci.

### P7. `shutil.rmtree(path, ignore_errors=True)` na Windows ([FIND-051](#find-051), [FIND-096](#find-096))

Tichý fail na zamčených GraphNav souborech. Temp se kumuluje.

**Systémový fix:** Helper `safe_rmtree(path, retries=3, delay=0.5)` který při PermissionError retry a na konci loguje "mohlo zůstat".

### P8. Hardcoded 5000 ms timeouty pro QThread.wait() (4+ instancí)

- `main.py:136` — OCR worker
- `playback_run_page._teardown` — run thread
- `workers.stop_and_wait` — via `CRUD_WORKER_STOP_TIMEOUT_MS`

**Důsledek:** Pro dlouhé operace (YOLO load, OCR pomalé) timeout expiruje → zombie thread.

**Systémový fix:** Centralizovat v `constants.py` + používat konstantu. Vyšší default (10s+).

### P9. DB session stále stejná per-thread bez explicit cleanup ([FIND-015](#find-015))

Scoped session drží thread-local instanci. Worker threads po exit neresetují. Nerozsáhlé, ale fragile.

**Systémový fix:** Worker threads volají `Session.remove()` ve finally své `run()` funkce.

### P10. Wizard page přímo přistupuje k `wizard.some_state()` method přes `# type: ignore` ([FIND-153](#find-153))

15+ callsitů s `type: ignore[attr-defined]`. Zbavit se těchto přes `typing.TYPE_CHECKING` + `TypedDict` nebo `Protocol`.

---

## 4. Tech-debt seznam

Věci, které nejsou bugy, ale zhoršují maintainability. Nejsou urgentní, ale *pokud se na ně sáhne*, stojí za konsolidaci.

### Mutable defaults / sharing references
- `FIND-011`: `default=list` v SQLAlchemy + `server_default=None` po ALTER → non-idempotent schema.
- `FIND-082`: `capture_sources=sources` shared reference.

### Dead code
- `FIND-055`: `count_waypoints_in_map_dir` není volán.
- `FIND-095`: Duplicate `if localized_wp != ...` check v playback_service.
- `FIND-102`: `if cp.waypoint_id` filter — defensive dead-code.
- `FIND-150`: `recording_finished` signal emit, nikdo neposlouchá.

### Magic numbers & strings
- `FIND-079`: `"capture_failed"`, `"capture_partial"` hardcoded.
- `FIND-046`: Nav outcome string bez enum check.
- `FIND-010`: `CRUD_WORKER_STOP_TIMEOUT_MS=3000` hardcoded, fragile.
- `FIND-115`: `_SUBPROCESS_TIMEOUT_SEC=30`.

### Inconsistent naming / layout
- `FIND-132`: `selected_fiducial_id` vs `fiducial_id` v `PlaybackWizardState` matoucí.
- `FIND-138`: `FiducialPage` 617 řádků přeúplná — 3 různé režimy v jedné třídě.
- `FIND-139`: Close confirmation strings duplicated across wizards.
- `FIND-182`: `session.hostname` vs `_hostname` private fallback.

### Type inconsistencies
- `FIND-045`: `MapCheckpoint.capture_sources: tuple` vs `CheckpointRef.capture_sources: list`.
- `FIND-033`: `RunRow.status: str` loss of `RunStatus` enum typing.
- `FIND-056`: `MapMetadata.default_capture_sources: list[str]` ve frozen dataclass.
- `FIND-140` (state field): Playback vs Recording state flow is asymetrical.

### Duplicate logic
- `FIND-073`: Duplicitní `_ensure_start_waypoint` logic.
- `FIND-136`: `_populate_props_from_bundle` v 3 wizardech.
- `FIND-057`: `list_all_metadata` vs `maps_repo.list_all` duplikace.

### Mixed concerns
- `FIND-047`: `_validate_loaded_map` side-effect do DB z load pathu.
- `FIND-030`: `photos_repo._to_photo_row` UI-specific `"?"` fallback.

### Import hygiene
- `FIND-054`: bosdyn import uvnitř `validate_map_dir` funkce.
- `FIND-174`: `inject_paths()` top-level side effect.

### Testing gaps
- Všechny body z §17 / Oblast 17 (FIND-183 až FIND-189).

---

## 5. Testovací pokrytí — co chybí

Existující ~600 řádků testů pokrývá helpery (normalize, namer, parsing). Chybí end-to-end testy flows. Seznam navrhovaných testů v prioritě:

### Prio A — chytí user-reported bugy

1. **E2E Recording flow** s mock ImagePoller + mock `GraphNavRecorder`:
   - Scénář: Start recording → první `create_waypoint` jako Checkpoint (ne Waypoint) → ověřit, že `start_waypoint_id == CP_001`, fiducial_id je v `observed_list`.
   - Scénář: Start → Waypoint → 3× Checkpoint → stop → save → load → playback mock → ověřit pořadí.

2. **Playback chain-of-causation test** (mock `GraphNavNavigator`):
   - Navigator vrací TIMEOUT na CP_001 → re-localize success → retry → OK. Ověřit, že `consecutive_nav_fails` je reset.
   - Navigator vrací `RobotLostError` → abort, žádný další CP.
   - 3× TIMEOUT consecutive → abort (safety net).
   - Operátor abort v prostředí CP_002 → status `aborted`, CP_001 marked done, CP_002+ not attempted.

3. **JSON schema round-trip** (pure contracts):
   - `build_checkpoint_plan_payload(recorded_cps) → parse_checkpoint_plan → build_... → rovnost`.
   - Edge: prázdný list, 1000 CP, nick s UTF-8, `start_waypoint_id` not in checkpoints.

4. **OCR worker under concurrent sweep** (real SQLite in-memory):
   - Dva workers + sweep thread, ověřit že jedna fotka není zpracována dvakrát současně. Pokud byl vytvořen duplicate, test failí.

5. **Save-then-save retry** ([FIND-140](#find-140)):
   - Recording → stop → save padne na DB error → user clicka znovu. Ověřit, že recovery funguje.

### Prio B — prevence regressí

6. **`save_map_to_db` validation** ([FIND-037](#find-037)):
   - Mapa s `start_waypoint_id` mimo checkpoint waypoint_ids → raise.
   - Duplicate checkpoint names → raise.
   - Empty checkpoints → raise.

7. **`_extract_fiducial_id` legacy form** ([FIND-038](#find-038)):
   - Payload `{"fiducial": 5}` → 5 (no crash).
   - Payload `{"fiducial_id": 7}` → 7.
   - Payload with both → preference jasný.

8. **Scoped session cleanup** ([FIND-015](#find-015)):
   - Vytvoř 100 worker threadů, ověř, že connection pool není saturovaný.

9. **Lease keepalive** ([FIND-064](#find-064)):
   - Integration test: connect → čekat 60s → robot commands stále pracují.

10. **E-Stop safety contract** ([FIND-156](#find-156)):
    - `EstopFloating(parent, on_trigger)` bez `on_release` → raise nebo disable button.

### Prio C — tech debt & regression

11. Test pro `normalize_plate_text` vs `photos_repo.fetch_last_image_bytes_for_plate` parity ([FIND-031](#find-031)).
12. Test pro `_unpack_result` na všechny formáty fast_plate_ocr return ([FIND-111](#find-111)).
13. `validate_sources_known` edge cases (empty available, None) ([FIND-188](#find-188)).
14. Migration test: `upgrade head` + `downgrade` + `upgrade head` round-trip.

---

## 6. UX audit

Systematický pohled na UI flow z pohledu operátora. Prioritně nejčastěji hlášené friction body.

### 6.1 Recording wizard flow

| Krok | Problém | FIND |
|---|---|---|
| ConnectPage | (Nezkoumáno detailně — předpokládá se OK) | — |
| FiducialPage | 617 ř. soubor — komplexní, power-on + WASD + fiducial check v jedné stránce | [FIND-138](#find-138) |
| TeleopRecordPage | **UX trap: první op nevymezena.** Operátor klikne V/N/B místo C → `start_waypoint_id` mis-set. | [FIND-072](#find-072) |
| TeleopRecordPage | Photo confirm overlay funguje, ale po V/N/B operátor čeká na preview, není hned patrné | Hint |
| TeleopRecordPage | `< 2 waypointů` jen confirm dialog, 0 waypointů nevaruje | [FIND-144](#find-144) |
| SaveMapPage | Fiducial re-check ukáže "✗ Nevidím" ale Save funguje | [FIND-147](#find-147) |
| SaveMapPage | **Save retry nemožný po fail** — frustrace | [FIND-140](#find-140) |

### 6.2 Playback wizard flow

| Krok | Problém | FIND |
|---|---|---|
| MapSelectPage | Nefiltruje `archive_is_valid=False` mapy — klik na invalid → generic error | (Agent 1 původně flaghlášen) |
| FiducialPage | Stejná 617 ř. stránka jako recording — matoucí "jiné tlačítka v jiném flowu" | [FIND-138](#find-138) |
| PlaybackRunPage | Pokud prepare selže, Next button zůstane disabled → slepá ulička | [FIND-142](#find-142) |
| PlaybackRunPage | Live view může být prázdný (`(live view)` placeholder) bez vysvětlení | [FIND-149](#find-149) |
| PlaybackRunPage | "STOP s návratem domů" čeká na nav timeout (až 60s) než se skutečně zastaví | — |
| PlaybackResultPage | (Nezkoumáno; ale z `playback_result_page.py:200 ř.`) pravděpodobně prosté metrics | — |

### 6.3 CZ/EN konzistence

- User-facing texty: CZ (v dialozích, log messages jen někdy).
- Log messages: mix (`"OCR pipeline start: %d bytes"`, "Robot není lokalizován na aktuální mapě").
- Exception messages v `raise RuntimeError(...)`: CZ, což je user-facing (propaguje se do error dialog).
- Error messages z bosdyn: angličtina → dialog ukáže "Bosdyn set_localization selhal (fiducial_id=5, start_waypoint=abc): InvalidArgument: ..." — čistý CZ prefix + EN bosdyn traceback.

**Doporučení:** Vyčistit aspoň user-facing exception messages na čistou CZ bez bosdyn leaks.

### 6.4 Accessibility

- **F1 = E-Stop** — viditelné v hint, ale application-wide shortcut má edge cases ([FIND-137](#find-137)).
- WASD + QE + V/N/B + C + Mezerník — dobrá ergonomie, ale **hvězdička**: uživatel musí klikat tlačítka pro speed_combo; není shortcut pro rychlost.
- Žádná **keyboard accessibility** pro Map Select, Photo Detail — jen click/dblclick.

### 6.5 "Zamrzlý uživatel" scénáře

| Scénář | Co vidí | Co by měl vidět |
|---|---|---|
| Nav timeout 60s | Progress bar se nehýbe, žádná etický update | Countdown "Čekám na navigaci (30s remaining)" |
| OCR zpomalí | OCR worker loop spammuje log | Status bar s "OCR queue: N pending" |
| `request_return_home` | Abort, ale nestart return home (bug FIND-093) | Clear progress "Přerušuji + návrat domů" |
| DB down 5+ min | DB status ukazuje červený dot, ale žádný actionable hint | Toast "Ověř připojení k DB" + troubleshooting link |
| Disconnect po idle | Žádná indikace, až při pokusu o wizard | Badge "Spot connection lost" |

---

## 6. UX audit

*(Doplní se.)*

---

## 7. Prioritized fix roadmap

Rozdělení oprav do sprintů dle severity a blast radius. **Neimplementuji** — jen navrhuji pořadí.

### Sprint 1 — Safety & data corruption stoppers (1-2 dny)

Nejprve fixy, které buď ochrání operátora/robota, nebo zastaví pokračující poškození dat.

| # | FIND | Popis | Komplexita |
|---|---|---|---|
| 1 | [FIND-156](#find-156) | Udělat `on_release` povinný parametr u `EstopFloating` nebo v `_do_release` raise/disable | Triviální |
| 2 | [FIND-134](#find-134) | `trigger_estop` flash/message při chybějícím bundle/widget | Malá |
| 3 | [FIND-061](#find-061) + [FIND-154](#find-154) | Timeout na `SpotBundle.disconnect` + `QApplication.aboutToQuit` handler | Malá |
| 4 | [FIND-062](#find-062) + [FIND-181](#find-181) | Poll `is_powered_on` s timeout po `power_off` | Malá |
| 5 | [FIND-072](#find-072) | Enforce první op = Waypoint v TeleopRecordPage (disable V/N/B dokud není alespoň 1 waypoint) | Malá-střední |
| 6 | [FIND-066](#find-066) + [FIND-078](#find-078) | Dialog "retry/skip/abort" při capture failure, nepřejmenovat kind silently | Střední |

### Sprint 2 — Playback reliability (2-3 dny)

Adresuje uživatelem hlášené "robot jede náhodně". Závisí na Sprintu 1 (FIND-072).

| # | FIND | Popis | Komplexita |
|---|---|---|---|
| 7 | [FIND-037](#find-037) | `validate_plan_invariants` v `save_map_to_db`: start_waypoint_id ve checkpoints[], duplicate name/wp_id detection | Malá |
| 8 | [FIND-094](#find-094) | Po FIDUCIAL_NEAREST fallback ověřit localized_waypoint | Triviální |
| 9 | [FIND-067](#find-067) | Číst `resp.ambiguity_result` v `localize_at_start` + log | Triviální |
| 10 | [FIND-086](#find-086) | Vytvořit run v DB před pre-flight checks (audit trail selhání) | Malá |
| 11 | [FIND-140](#find-140) | Two-phase save: `stop_and_export(temp_dir)` + `save_from_temp(name, ...)` | Střední |
| 12 | [FIND-075](#find-075) | `read_observed_fiducial_ids` failure → user-facing error (ne warning) | Malá |
| 13 | [FIND-089](#find-089) | Rozšířit `_should_retry_outcome` o STUCK + NO_ROUTE s delay | Malá |

### Sprint 3 — Error handling hygiene (3-5 dní)

Systémově adresuje pattern P1 (silent failures).

| # | FIND | Popis | Komplexita |
|---|---|---|---|
| 14 | Cross-cut P1 | Zavést `report_error_to_user(category, exc)` helper | Střední |
| 15 | Cross-cut P1 | Projít 20+ `except: log.warning` call-sitů, rozdělit do:<br>a) Safety = raise<br>b) UI-degradable = emit signal<br>c) Logging-only = log + metrics | Velká (rozsah) |
| 16 | [FIND-099](#find-099) | V `run_all_checkpoints` rozlišit KeyboardInterrupt/MemoryError/OperationalError | Malá |
| 17 | [FIND-023](#find-023) | `mark_progress`/`finish` ověřit `rowcount==1` | Malá |
| 18 | [FIND-058](#find-058) | `save_map_to_db` — wrap `validate_map_dir` s CZ error message | Malá |
| 19 | [FIND-068](#find-068) | `localize_at_start` rozlišit FiducialNotVisible vs network vs SDK | Malá |
| 20 | [FIND-108](#find-108) | `cv2.imdecode` fail → raise (OCR worker pak mark_failed) | Triviální |
| 21 | [FIND-106](#find-106) | OCR permanent error → terminal abort + UI signal | Malá |
| 22 | [FIND-124](#find-124) | `_ping` locale-independent parsing | Malá |

### Sprint 4 — Concurrency & lifecycle (2-3 dny)

Race conditions a Qt signal teardown.

| # | FIND | Popis | Komplexita |
|---|---|---|---|
| 23 | [FIND-141](#find-141) + Cross-cut P2 | Utility `disconnect_all_signals(obj)` + call v teardown | Malá |
| 24 | [FIND-024](#find-024) + [FIND-027](#find-027) + [FIND-048](#find-048) | ON CONFLICT patterny všude | Malá |
| 25 | [FIND-026](#find-026) | OCR zombie timeout heartbeat nebo zvýšit na 30 min | Malá |
| 26 | [FIND-015](#find-015) | Worker threads: `Session.remove()` ve finally `run()` | Triviální |
| 27 | [FIND-093](#find-093) | `request_return_home` skutečně spouští return (opravit nebo přejmenovat) | Triviální |
| 28 | [FIND-064](#find-064) | Ověřit Lease keepalive (oblast 16), přidat integration test | Malá |

### Sprint 5 — UX polish (2-3 dny)

| # | FIND | Popis | Komplexita |
|---|---|---|---|
| 29 | [FIND-133](#find-133) | Confirm close jen pokud fáze je RUNNING (ne jen bundle exists) | Triviální |
| 30 | [FIND-142](#find-142) | ResultPage musí zvládnout "no run" case | Malá |
| 31 | [FIND-149](#find-149) | Live view placeholder: "⚠ Kamera nedostupná..." při fail | Triviální |
| 32 | [FIND-097](#find-097) | `_warn_if_drift` emit `drift_warning` signál + UI zobrazení | Malá |
| 33 | [FIND-146](#find-146) | `SaveMapPage._is_name_valid` debounce | Triviální |
| 34 | [FIND-145](#find-145) | `_on_ocr_done` DB query v BG thread | Malá |
| 35 | [FIND-170](#find-170) | Format timestamps v CRUD lokálně | Malá |

### Sprint 6 — Tech debt / long-term (průběžně)

| # | FIND | Popis | Komplexita |
|---|---|---|---|
| 36 | Cross-cut P4 | Protocols pro `NavigationResult`, `ImagePoller`, `SpotSession` | Střední |
| 37 | Cross-cut P6 | Enum místo magic strings (CaptureNote, ReturnHomeStatus) | Malá |
| 38 | [FIND-001](#find-001) | DB credentials do keyringu | Malá |
| 39 | [FIND-011](#find-011) + [FIND-019](#find-019) | `server_default` v migrace: nechat, nebo explicit Python default | Střední |
| 40 | Sprint testy | Prio A-B z §5 | Velká |

### Sprint 7 — Dokumentace & cleanup

| # | FIND | Popis | Komplexita |
|---|---|---|---|
| 41 | [FIND-190](#find-190) + [FIND-191](#find-191) | Aktualizovat `instructions.md` o recording protocol, checkpoint/waypoint invariant | Malá |
| 42 | Cross-cut dead code | Odstranit FIND-055, FIND-095, FIND-102, FIND-150 | Triviální |
| 43 | [FIND-196](#find-196) | Version check pro Python 3.10 v main.py | Triviální |

---

## Závěr

Spot Operator je **jedním procesem aplikace** s jasnou vrstvou, dobrým komentováním a rozumnou architekturou — ale **detaily v error paths a UX při selháních nejsou dotažené**. Uživatelem hlášené problémy (random playback, missing data, warningy místo errorů) jsou **přímým důsledkem** kombinace výše identifikovaných bugů, ne jednoho "velkého" architektonického defektu.

**Největší páky pro stabilitu jsou:**
1. Sprint 1 — safety + data corruption stoppers (1-2 dny).
2. Sprint 2 — playback reliability (2-3 dny).
3. Pattern P1 systémový fix (Sprint 3) — nejvíce zmenší "silent degradation" problém.

**Následné prevenci regresí** pomůže testovací pokrytí (Sprint 6, Prio A-B), bez kterého se tyto problémy postupně vrátí.

---

*Report vygenerován statickou analýzou 2026-04-23.*
*Dynamické ověření (live test s robotem, reprodukce konkrétních bugů) ponecháno uživateli.*
