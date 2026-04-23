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

## [1.3.0] — 2026-04-23

Nová UX feature: photo confirm overlay. Breaking change ve shortcutech
(`[` `]` `P` → **V / B / N**). E-Stop recovery pro motors-on scénář.

### Added

- **`PhotoConfirmOverlay`** (`spot_operator/ui/common/photo_confirm_overlay.py`)
  — non-modal widget zobrazený po kliknutí "Foto vlevo/vpravo/obě" v
  TeleopRecordPage. Ukazuje **live video** z dotyčných kamer (1 nebo 2
  pod sebou), operátor vizuálně ověří SPZ a potvrdí uložení tlačítkem
  "✓ Vyfotit a uložit" (nebo zruší tlačítkem "✗ Zrušit"). WASD funguje
  dál — overlay má `Qt.NoFocus` a klávesy propadnou do parent widgetu.
- Capture se děje až **po potvrzení** (fresh gRPC volání) — overlay slouží
  jen jako preview, ne buffer. Pokud operátor zruší, žádný waypoint ani
  fotka se neuloží.
- Tlačítka Foto vlevo/vpravo/obě jsou dočasně disabled během overlay
  (prevence double-click, `_set_photo_buttons_enabled(False)`).

### Changed

- **Breaking — klávesové zkratky focení**: `[` / `]` / `P` →
  **V / N / B** (Vlevo / pravá-N / oBě strany). Hranaté závorky na české
  klávesnici vyžadují AltGr; V/B/N jsou přímo dostupné. Tlačítka v
  TeleopRecordPage pojmenovaná s novými zkratkami v závorce.
- Hint label v TeleopRecordPage má rozšířený druhý řádek s mapováním
  V/N/B/C → foto vlevo/vpravo/obě/waypoint.
- README sekce 9 (klávesy) a 10 (postup nahrávání mapy) aktualizováno.
- Glosář `capture_sources` v instructions.md zmínit V/N/B a
  PhotoConfirmOverlay.

### Fixed

- **E-Stop `MotorsOnError` auto-recovery** v `session_factory.connect`:
  pokud při startu `estop.start()` → `force_simple_setup()` vyhodí bosdyn
  `MotorsOnError` (motory Spota běží z předchozí instance / crashu /
  jiného klienta), aplikace **automaticky** zavolá
  `PowerManager.power_off()` a retry `estop.start()`. Dříve se aplikace
  zasekla s warning v logu + `LeaseUseError` spam a operátor musel
  restartovat celý spot_operator.
- `TeleopRecordPage._teardown` nyní zavře overlay pokud existuje
  (prevence thread leaku při zavření wizardu uprostřed preview).

## [1.2.2] — 2026-04-22

Drobný default fix. Live view používá `front_composite` (stitched přední
obraz) místo single-camera `frontleft_fisheye_image`, jak je to v autonomy.

### Changed

- **FiducialPage / TeleopRecordPage / PlaybackRunPage** — po instanciaci
  `ImagePipeline` voláme `set_source(CAMERA_FRONT_COMPOSITE)`. Operátor vidí
  široký záběr přední části Spota (stitched frontleft + frontright kamery)
  místo jen jedné kamery. Konzistentní s autonomy UI.
- Pokud Spot pravou přední kameru nemá (firmware / konfigurace), autonomy
  `ImagePoller.capture_front_composite()` fallne na samotnou levou — žádný
  crash, žádné None frames (ověřeno v `autonomy/tests/test_image_poller.py`).

### Fixed

- V 1.2.0 byl `set_source(front_composite)` volaný, ale kvůli bugu
  `ImagePipeline(session)` místo `ImagePipeline(poller)` celý live view
  nefungoval (root cause 1.2.1 bug #3).
- V 1.2.1 jsme `set_source` preventivně vypnuli jako safety — zbytečně,
  protože jakmile pipeline dostal správný `ImagePoller`, poller má pro
  `front_composite` special-case a funguje to out-of-the-box.

### Trade-off

- Stitched kompozit = 2× gRPC capture per frame → potenciálně polovička
  FPS oproti single source. Při dobré Wi-Fi stále ~10 FPS, pro operátorský
  teleop (~0.5 m/s) dostatečné.

## [1.2.1] — 2026-04-22

Bugfix-only release. 5 problémů z prvního reálného testu FiducialPage.

### Fixed

- **Live view se konečně zobrazuje.** `ImagePipeline` přijímá v konstruktoru
  `ImagePoller` instanci, ne `SpotSession`. Všechny 3 pages (FiducialPage,
  TeleopRecordPage, PlaybackRunPage) předávaly `session` místo poller →
  `poller.capture(source)` tiše selhávalo s `AttributeError` a `frame_ready`
  nikdy nepřišel. Nyní `_ensure_image_pipeline` / `_ensure_live_view` vytvoří
  `ImagePoller(bundle.session)` a předá ji pipeline.
- **WASD drží plynule.** Přidán QTimer 5 Hz (`_velocity_timer`) v FiducialPage
  a TeleopRecordPage. Periodicky re-publishuje aktuální velocity pokud operátor
  drží klávesy. Bez toho Spot zastavoval po ~10 cm — Spot SDK velocity má
  default `end_time_secs ≈ 0.6 s` a autonomy `_CommandDispatcher` neopakuje
  last command sám.
- **E-Stop release funguje bez restartu aplikace.** `EstopFloating` teď
  toggluje: klik v aktivním stavu → `on_trigger`, klik v triggered stavu →
  `on_release`. F1 shortcut taky toggluje (`trigger_from_shortcut`).
  FiducialPage/TeleopRecordPage/PlaybackRunPage registrují
  `_handle_estop_release` jako `on_release` callback — volá
  `EstopManager.release()` a resetuje stav stránky přes `_mark_spot_off`.
- **Tlačítko "Zapnout a postavit Spota" je znovupoužitelné.** Bylo trvale
  disabled po prvním úspěšném power_on → pokud Spot vypne (E-Stop release,
  battery), operátor nemohl znovu zapnout. Nyní zůstává vždy enabled (krom
  během worker threadu). Stav "Spot stojí / vypnutý" je v samostatném
  `_power_state_label` vedle tlačítka. Idempotentní click — Spot SDK
  `power_on` na running robot vrací rychle bez efektu.
- **E-Stop widget už nepřekrývá tlačítka.** `EstopFloating._reposition` nyní
  umisťuje widget do pravého **dolního** rohu parent widgetu (dříve horní →
  překrývalo side panel s tlačítky "Zapnout Spota" / "Foto ..."). Widget
  zvětšen na 220×70 px kvůli delšímu textu "⚠ AKTIVNÍ — klik uvolnit".

### Changed

- **FiducialPage nepoužívá `set_source(CAMERA_FRONT_COMPOSITE)`** — používá
  default source z autonomy `ImagePipeline` (`frontleft_fisheye_image`).
  Jednodušší cesta, 1 gRPC roundtrip místo 2, pro fiducial navigaci stačí.
- **`SpotWizard.set_estop_callback(on_trigger, on_release=None)`** — nově
  dva parametry. `trigger_estop` (F1 shortcut handler) nejprve zkouší
  delegovat na `_estop_widget.trigger_from_shortcut` (který zná triggered
  stav a toggluje), fallback na callbacky.
- **`FiducialPage` má dva status labely** místo jednoho: `_power_state_label`
  (trvalý stav Spot stojí/vypnutý, barevná tečka) a `_power_status` (průběžné
  zprávy typu "Zapínám motory…", chybové hlášky).

## [1.2.0] — 2026-04-22

Opravy 4 problémů z prvního reálného testu na Windows stroji. **Breaking
change ve wizardu** (recording 6 → 5 kroků; volba strany focení per-checkpoint).

### Changed

- **Recording wizard má nyní 5 kroků** (Wi-Fi, Login, Fiducial-s-teleopem,
  Teleop-recording, Save) místo 6. `RecordingSidePage` smazán — volba strany
  focení se dělá **per-checkpoint** v TeleopRecordPage přes tlačítka
  "Foto vlevo" (`[`) / "Foto vpravo" (`]`) / "Foto z obou stran" (`P`).
  Sémantika `maps.default_capture_sources` změněna: teď je to "obě strany, co
  robot umí" (fallback); per-checkpoint přesné info je v
  `checkpoints_json.checkpoints[*].capture_sources`.
- **FiducialPage umožňuje dovézt Spota k fiducialu** — přidán live view
  (`front_composite` source), WASD/QE teleop, floating E-Stop widget v pravém
  horním rohu, explicitní tlačítko "Zapnout a postavit Spota" (volá
  `bundle.power.power_on()` + `stand()` v FunctionWorker, ~20 s). WASD je
  aktivní jen po úspěšném power-on. Operátor může na stránku vstoupit s
  jakkoliv umístěným Spotem. Sdílená class pro recording i playback.
- **Wi-Fi check přestal zobrazovat SSID** — `netsh wlan show interfaces`
  vracelo SSID první Wi-Fi karty, což při multi-Wi-Fi setupu nebylo nutně ta,
  na které běží Spot spojení. Ping + TCP test jsou dostatečný důkaz.
  `_current_ssid()` smazán, `WifiCheckResult.current_ssid` odstraněn.
- **Glosář `capture_sources`** v `instructions.md` upřesněn jako per-checkpoint
  seznam (ne per-map).
- **`instructions-reference.md`**: strom složek aktualizován (bez
  `recording_side_page.py`), implementační pořadí bod 11 přeformulován.

### Fixed

- **`AttributeError: 'MoveCommandDispatcher' object has no attribute 'start'`**
  při login — dispatcher si spouští thread sám v `__init__`, odstraněn chybný
  `dispatcher.start()` volání v `session_factory.connect`.
- **`SpotBundle.disconnect`** volal `move_dispatcher.stop()` → nyní `.shutdown()`
  (autonomy: `.stop()` znamená "zastav robota", `.shutdown()` znamená "zastav
  thread dispatcheru").
- **`TeleopRecordPage._send_velocity`** volalo neexistující `dispatcher.send(vx, vy, vyaw)`
  → nyní `.send_velocity(vx, vy, vyaw)` (správný API z autonomy).
- **`TeleopRecordPage._teardown`** používal `dispatcher.send(0, 0, 0)` →
  nyní `.stop()` (idiomatičtější).

### Removed

- **`spot_operator/ui/wizards/pages/recording_side_page.py`** — smazán, funkce
  nahrazena per-checkpoint tlačítky v TeleopRecordPage.

### Added

- **`tests/integration/test_autonomy_smoke.py`** — rozšířeno o explicitní
  asserty pro `MoveCommandDispatcher.send_velocity` / `.stop` / `.shutdown`,
  a `assert not hasattr(MoveCommandDispatcher, "start")` aby se detekovalo,
  kdyby autonomy v budoucnu zavedla `.start()` metodu (mělo by to vyvolat
  review v spot_operatoru).

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
