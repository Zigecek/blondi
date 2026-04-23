# Fix Plan — Spot Operator

**Datum:** 2026-04-23
**Navazuje na:** [code_review.md](code_review.md) (196 nálezů, FIND-001 až FIND-196)
**Omezení:** Aplikace se testuje bez fyzického robota → spoléháme na unit testy, static analysis, mock bosdyn/autonomy API a headless Qt test runs. Flows vyžadující reálného robota (live nav, lease keepalive, E-Stop fyzická reakce) jsou označeny **[robot-test]** a musí se ověřit po dodání Spota.

---

## 0. Princip

Každý nález z `code_review.md` má být adresován konkrétní změnou kódu (nebo explicitně **vědomě odložen** s důvodem). Tento dokument je normativní — říká *co a proč*. Detaily implementace patří do commit messages a PR description.

Práce je rozdělena do **15 PR-size batchů**. Každý batch:

- Má jasný rozsah (file set + findings).
- Je samostatně mergeable.
- Má test plán proveditelný **bez robota**.
- Má explicit regression risk assessment.
- Má clear rollback path (branch, revert-safe).

**Posloupnost** je důležitá — některé batche závisí na jiných. Graf závislostí je v §16.

---

## 1. Globální pravidla

### 1.1 Branch & commit style

- Jedna feature branch per batch: `fix/pr-NN-short-name` (např. `fix/pr-01-safety-stoppers`).
- Commit message formát:

  ```
  <area>: <verb in CZ imperative>

  Addresses: FIND-xxx, FIND-yyy, FIND-zzz

  <podrobnosti — proč, jak, co může rozbít>
  ```

- Každý PR obsahuje *několik* commitů (1 commit = 1 finding je OK, agregovat do logic-related batches).
- **Nesmí se kombinovat** více batchů do jednoho PR, aby byl review-able.

### 1.2 Testing policy

- **Unit testy povinné** pro každý nový helper / metodu / invariant check.
- **Mock-based testy** pro autonomy/bosdyn kontrakty (NavigationOutcome, ImagePoller, GraphNavNavigator).
- **Žádné integration testy proti reálnému robotu** v tomto cyklu (robot není).
- `pytest -xvs` musí projít lokálně před mergem.
- Nové testy jsou v `tests/unit/` nebo `tests/integration/` (ale "integration" zde znamená "víc modulů najednou", ne "s externím systémem").

### 1.3 Type safety

- Nové metody: plné type hints (user memory "type hints všude").
- Odstraňování `# type: ignore[attr-defined]` je součást cílů PR-09 (Protocol pro wizard state).
- Mypy strict mode zvážit po dokončení všech PR.

### 1.4 Dokumentace updates

- Každý PR, který mění behavior popsané v `instructions.md`, musí aktualizovat tento dokument.
- CHANGELOG.md entry per PR (krátký, user-facing popis).

### 1.5 Rollback plan

- Každý PR musí být revert-safe. Žádné db-destructive migrace bez `downgrade()`.
- Alembic migrace jsou forward-only? — ne, pokud to není explicit. Každá nová migrace musí mít funkční `downgrade()`.

### 1.6 Co implementujeme hned, co odkládáme

- **Odkládáme explicit** (kvůli absenci robota):
  - FIND-064 (Lease keepalive ověření) — potřeba live test; přidáme *jen test*, fix je ovrějet.
  - FIND-070 (MotorsOnError + aktivní klient) — potřeba druhý klient na robotovi.
  - FIND-180 (lease keepalive integration test) — stejný důvod.
  - FIND-187 (integration test spot_connect) — stejný důvod.
- **Implementujeme celé** — všechny ostatní findings. Bez robota ověříme přes unit testy + mock.

---

## 2. PR-01 — Safety stoppers (E-Stop + disconnect + power_off)

### Cíl

Robot a uživatel musí být v bezpečí i když cokoli selže. Žádný signal má říkat "OK" když reálný stav je "nebezpečný".

### Addresses findings

FIND-061, FIND-062, FIND-071, FIND-134, FIND-154, FIND-156, FIND-162, FIND-181

### Změny v kódu

1. **[spot_operator/ui/common/estop_floating.py](spot_operator/ui/common/estop_floating.py) — povinný `on_release`**
   - V `__init__`: změnit `on_release: Optional[Callable[[], None]] = None` na `on_release: Callable[[], None]` (povinný).
   - Pokud někdo předá None explicit → `raise TypeError("on_release is required")`.
   - Odstranit fallback na "only visual reset" — `_do_release` bez callback pryč.
   - Addresses FIND-156.

2. **[spot_operator/ui/wizards/base_wizard.py](spot_operator/ui/wizards/base_wizard.py) — `trigger_estop` fallback**
   - Pokud bundle je None a žádný widget: UI flash + `_log.error` + emit `estop_unavailable` signál (nový).
   - `_log.error("E-Stop stisknut, ale není dostupný v tomto kroku (robot není připojen)")`.
   - Addresses FIND-134.

3. **[spot_operator/robot/session_factory.py](spot_operator/robot/session_factory.py) — timeout na `disconnect`**
   - Každý teardown step v `SpotBundle.disconnect()` obalit `concurrent.futures.ThreadPoolExecutor` + `wait(timeout=3)`. Pokud timeout → log WARNING s konkrétním krokem.
   - Pomocná funkce `_teardown_with_timeout(name: str, fn: Callable[[], None], timeout_s: float = 3.0) -> None`.
   - Addresses FIND-061.

4. **[spot_operator/robot/session_factory.py](spot_operator/robot/session_factory.py) — `power_off` + poll**
   - Po `PowerManager(session).power_off()` přidat: `wait_until_powered_off(robot, max_wait_s=10)`.
   - Nová pomocná funkce v `spot_operator/robot/power_state.py`:

     ```python
     def wait_until_powered_off(robot: Any, *, max_wait_s: float = 10.0, poll_interval_s: float = 0.2) -> bool:
         """Poll robot.is_powered_on() dokud není False nebo timeout. Vrací True pokud se podařilo."""
     ```

   - V `connect_partial` auto-recovery path: po `power_off()` volat wait. Pokud False → `raise RuntimeError("Power-off timed out")`.
   - Addresses FIND-062, FIND-181.

5. **[spot_operator/robot/session_factory.py](spot_operator/robot/session_factory.py) — `SpotBundle.slots=True`**
   - Změnit `@dataclass` na `@dataclass(slots=True)`.
   - Otestovat, že optional field s None defaultem stále funguje.
   - Addresses FIND-071.

6. **[spot_operator/ui/main_window.py](spot_operator/ui/main_window.py) — `closeEvent` timers + aboutToQuit**
   - Před `super().closeEvent(event)` zavolat `self._db_timer.stop()` a `self._temp_cleanup_timer.stop()` v try/except.
   - V `__init__`: `QApplication.instance().aboutToQuit.connect(self._emergency_cleanup)`.
   - `_emergency_cleanup(self)` — idempotentní varianta `closeEvent` cleanup (stop timers + disconnect bundle). Používá module-level `_cleanup_done` flag.
   - Addresses FIND-154, FIND-162.

### Testy (bez robota)

- `tests/unit/test_estop_floating.py` — nový
  - `test_estop_requires_on_release_callback()` — `EstopFloating(parent, on_trigger, on_release=None)` → `TypeError`.
  - `test_trigger_release_cycle()` — mock callbacks, ověřit trigger → triggered, release → reset.
  - `test_release_callback_failure_keeps_triggered()` — pokud on_release raise, widget zůstane triggered (neresetuje).

- `tests/unit/test_session_factory_disconnect.py` — nový
  - Mock `SpotBundle` s `move_dispatcher.shutdown` který blokuje > 5s. Ověřit, že `disconnect` skončí do ~3.5s.
  - Mock `PowerManager` se `power_off` + `robot.is_powered_on` postupně vracející True→False. Ověřit, že `wait_until_powered_off` vrátí True.
  - Mock, kde `is_powered_on` zůstává True → timeout → `wait_until_powered_off` vrátí False.

- `tests/unit/test_main_window_lifecycle.py` — nový (headless Qt)
  - QTest simulovat `QApplication.aboutToQuit.emit()` → ověřit, že `_emergency_cleanup` běží idempotentně.
  - `closeEvent` volá stop na timerech (mock timery + spy).

### Rizika

- `concurrent.futures.ThreadPoolExecutor` při shutdown musí být cleanup. Riziko: thread leak. Mitigace: ExecutorContext manager v teardown.
- `aboutToQuit` a `closeEvent` běží v *nějakém* pořadí; idempotence je důležitá.

### Rollback

- Samostatné commity per finding → snadný revert.

---

## 3. PR-02 — Recording UX + `start_waypoint_id` (root cause "robot jede náhodně")

### Cíl

Eliminovat kořenovou příčinu hlášeného bugu "robot jede na vzdálený CP". Enforce správný recording protokol v UI.

### Addresses findings

FIND-066, FIND-072, FIND-073, FIND-077, FIND-078, FIND-082, FIND-085, FIND-144

### Změny v kódu

1. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — helper `_ensure_start_waypoint`**
   - Nový private method:

     ```python
     def _ensure_start_waypoint(self, wp_id: str) -> None:
         if self._start_waypoint_id is None:
             self._start_waypoint_id = wp_id
     ```

   - Volat místo duplicate `if self._start_waypoint_id is None: ...` bloků v `add_unnamed_waypoint` (řádek 123-124) a `capture_and_record_checkpoint` (řádek 154-155).
   - Addresses FIND-073.

2. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — `start()` reset state**
   - `start(...)` jako první krok: `self._checkpoints.clear(); self._start_waypoint_id = None`.
   - Dokumentovat v docstring: "Start vždy čistí předchozí stav."
   - Addresses FIND-077.

3. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — `abort()` robot state recovery**
   - Pokud `stop_recording()` raise, přidat fallback: po catch zkusit `graph_nav_recording_client.get_record_status()` a pokud je active, retry `stop_recording()` s delay 1s.
   - Addresses FIND-085.

4. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — `capture_and_record_checkpoint` nemění kind silently**
   - Odstranit blok `if not photos: kind = "waypoint"` (řádek 177-180).
   - Místo toho: `raise CaptureFailedError(name=name, saved_sources=[], failed_sources=list(sources))` — nový typ exception.
   - Volající (TeleopRecordPage) rozhoduje: dialog retry/skip/abort. Recording service *nerozhoduje* samo.
   - Addresses FIND-066, FIND-078.

5. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — `capture_sources=list(sources)` defensive copy**
   - V `capture_and_record_checkpoint` řádek 189: změnit `capture_sources=sources` na `capture_sources=list(sources)`.
   - Addresses FIND-082.

6. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — nová exception `CaptureFailedError`**
   - Přidat:

     ```python
     class CaptureFailedError(RuntimeError):
         def __init__(self, *, name: str, saved_sources: list[str], failed_sources: list[str]):
             self.name = name
             self.saved_sources = tuple(saved_sources)
             self.failed_sources = tuple(failed_sources)
             super().__init__(f"Capture failed for {name}: 0 saved, {len(failed_sources)} failed")
     ```

7. **[spot_operator/ui/wizards/pages/teleop_record_page.py](spot_operator/ui/wizards/pages/teleop_record_page.py) — enforce první op = Waypoint**
   - V `initializePage`: disable `_btn_photo_left/_btn_photo_right/_btn_photo_both` dokud `self._service.waypoint_count == 0`.
   - V `_add_waypoint` po úspěšném add: enable photo buttons (connect přes signal "first waypoint added" nebo direct update after).
   - Přidat `status_label` hint: "Stiskni **C** (Waypoint) na startu u fiducialu, pak projdi trasu a foť."
   - Klávesy V/N/B při 0 waypointech: pop-up "Nejdřív přidej startovní Waypoint (C)".
   - Addresses FIND-072.

8. **[spot_operator/ui/wizards/pages/teleop_record_page.py](spot_operator/ui/wizards/pages/teleop_record_page.py) — capture failure dialog**
   - `_capture` try/except okolo `capture_and_record_checkpoint`. Na `CaptureFailedError`:

     ```python
     choice = QMessageBox.question(
         self, "Focení selhalo",
         f"Nepodařilo se uložit žádný snímek. Zkusit znovu, přeskočit (uložit jako Waypoint bez fotky), nebo zrušit?",
         QMessageBox.Retry | QMessageBox.Discard | QMessageBox.Cancel
     )
     ```

   - Retry → znovu `_capture`. Discard → `add_unnamed_waypoint` (explicit waypoint). Cancel → žádná akce, operátor pokračuje nebo to vyřeší sám.
   - Addresses FIND-066, FIND-078.

9. **[spot_operator/ui/wizards/pages/teleop_record_page.py](spot_operator/ui/wizards/pages/teleop_record_page.py) — 0 waypointů block**
   - `_on_finish_clicked`: pokud `waypoint_count == 0`, **absolutně zablokovat** (ne jen confirm_dialog), error_dialog s "Musíš nahrát aspoň 1 waypoint (C) na startu".
   - Zachovat existující confirm pro `< 2`.
   - Addresses FIND-144.

### Testy

- `tests/unit/test_recording_service.py` — nový (s mock GraphNavRecorder)
  - `test_start_waypoint_id_set_by_first_waypoint_add()`.
  - `test_start_waypoint_id_set_by_first_checkpoint_add_if_no_waypoint()` — explicit test stávajícího chování.
  - `test_start_reset_clears_previous_state()`.
  - `test_capture_failure_raises_CaptureFailedError()`.
  - `test_abort_retries_stop_recording_on_error()`.

- `tests/unit/test_teleop_record_page.py` — nový (headless Qt)
  - `test_photo_buttons_disabled_before_first_waypoint()`.
  - `test_photo_buttons_enabled_after_add_waypoint()`.
  - `test_capture_failure_shows_dialog_with_retry()` — mock service + pytest-qt.
  - `test_finish_blocked_with_zero_waypoints()`.

### Rizika

- **Breaking UX change:** operátoři zvyklí klikat V/N/B hned dostanou error. Mitigace: jasný hint v sub-title + status_label.
- Při odstranění silent demotion se objeví error-path, kterou nikdo nikdy netestoval. Mitigace: unit testy pokrývají retry/skip/cancel.

### Rollback

- Explicit migration řetězec: `CaptureFailedError` je nová třída, neovlivňuje existing záznamy v DB.

---

## 4. PR-03 — Map save validace invariantů

### Cíl

Mapa se do DB nedostane v semanticky rozbitém stavu. Všechny invarianty se kontrolují při save, ne až při playback.

### Addresses findings

FIND-020, FIND-037, FIND-038, FIND-039, FIND-040, FIND-042, FIND-043, FIND-075

### Změny v kódu

1. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — `validate_plan_invariants`**
   - Nová public funkce:

     ```python
     def validate_plan_invariants(plan: MapPlan) -> None:
         """Raise ValueError pokud plán nesplňuje požadované invarianty."""
         # 1. start_waypoint_id musí být v plan.checkpoints[].waypoint_id (pokud je set)
         # 2. žádné duplikátní name
         # 3. žádné duplikátní waypoint_id
         # 4. aspoň 1 checkpoint (jakýkoli kind)
     ```

   - Pokrýt raise messages v CZ.

2. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — `_extract_fiducial_id` tolerance**
   - Pokud `payload["fiducial"]` je `int` (ne dict), interpret jako `{"id": fiducial}` + log warning.
   - Addresses FIND-038.

3. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — `_normalize_sources` str tolerance**
   - Na začátku: `if isinstance(value, str): value = [value]` + log debug "Legacy scalar capture_source coerced to list".
   - Addresses FIND-040.

4. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — `_as_optional_int` prázdný string**
   - `if isinstance(value, str) and not value.strip(): return fallback`.
   - Addresses FIND-039.

5. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — `parse_checkpoint_results` tolerantní timestampy**
   - `started_at` a `finished_at` přes `_as_optional_str` s fallback `""`, ne `_required_str`.
   - Addresses FIND-042.

6. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — `schema_version` warning na newer**
   - V `parse_checkpoint_plan` po `_normalize_schema_version`:

     ```python
     if schema_version > MAP_METADATA_SCHEMA_VERSION:
         _log.warning("Map schema %d is newer than supported %d", schema_version, MAP_METADATA_SCHEMA_VERSION)
     ```

   - Addresses FIND-043.

7. **[spot_operator/services/map_storage.py](spot_operator/services/map_storage.py) — volat validace v `save_map_to_db`**
   - Po `plan = parse_checkpoint_plan(...)` volat `validate_plan_invariants(plan)`.
   - Addresses FIND-037.

8. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — `read_observed_fiducial_ids` fail je error**
   - Pokud `read_observed_fiducial_ids` raise, místo silent warning+fallback: raise `RuntimeError("Nelze ověřit fiducial v mapě: {exc}")`.
   - Operátor uvidí dialog ve SaveMapPage (existující flow).
   - Addresses FIND-075.

9. **[spot_operator/db/repositories/maps_repo.py](spot_operator/db/repositories/maps_repo.py) — `list_all_validated`**
   - Nová metoda: `list_all_validated(session, *, include_invalid: bool = False)` — default vyfiltruje `archive_is_valid=False`.
   - `MapSelectPage` (oblast 12) použije tuto metodu, aby user neviděl rozbité mapy default.
   - Addresses FIND-020.

### Testy

- `tests/unit/test_map_contracts.py` — rozšířit
  - `test_validate_plan_invariants_rejects_start_wp_not_in_checkpoints()`.
  - `test_validate_plan_invariants_rejects_duplicate_names()`.
  - `test_validate_plan_invariants_rejects_duplicate_waypoint_ids()`.
  - `test_validate_plan_invariants_rejects_empty_checkpoints()`.
  - `test_extract_fiducial_id_accepts_scalar_legacy()` — `{"fiducial": 5}` → 5.
  - `test_normalize_sources_accepts_string()`.
  - `test_as_optional_int_empty_string_returns_fallback()`.
  - `test_parse_checkpoint_results_tolerates_missing_timestamps()`.
  - `test_schema_version_newer_logs_warning()`.

- `tests/unit/test_map_storage_validation.py` — nový
  - Mock `parse_checkpoint_plan` return s corrupted plan → `save_map_to_db` raise.

### Rizika

- Starší mapy v DB mohou selhat validaci při příštím playback → operátor nevidí "Mapa neplatná". Mitigace: na UI vrstvě (MapSelectPage) ukázat "⚠ Tato mapa byla označena jako neplatná ({reason})" i při `list_all_validated(include_invalid=True)` (admin mode).

### Rollback

- Žádná DB migrace. Čistě kódová změna.

---

## 5. PR-04 — Two-phase save + retry

### Cíl

Operátor nikdy neztratí nahrávku kvůli transientní chybě při save. Save je retry-able.

### Addresses findings

FIND-022, FIND-025, FIND-048, FIND-058, FIND-083, FIND-140

### Změny v kódu

1. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — rozdělit `stop_and_archive_to_db`**
   - Nový public method:

     ```python
     def stop_and_export(self) -> RecordingSnapshot:
         """Zastaví recording, stáhne mapu do temp, vrátí immutable snapshot pro save."""
     ```

   - Nová dataclass:

     ```python
     @dataclass(frozen=True)
     class RecordingSnapshot:
         temp_dir: Path
         checkpoints: tuple[RecordedCheckpoint, ...]
         start_waypoint_id: str | None
         observed_fiducial_id: int | None
         default_capture_sources: tuple[str, ...]
         # (není is_recording — recording už je stopnuté)
     ```

   - Nový public method:

     ```python
     def save_snapshot_to_db(
         snapshot: RecordingSnapshot,
         *,
         map_name: str,
         note: str,
         operator_label: str | None,
         end_fiducial_id: int | None,
     ) -> int:
         """Uloží snapshot do DB. **Idempotentní retry-safe** pokud se jméno ještě neobjevilo."""
     ```

   - `stop_and_archive_to_db` zachovat jako wrapper nad oběma (backward compat, ale deprecated).
   - Addresses FIND-140.

2. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — temp dir cleanup na úspěch**
   - Snapshot drží `temp_dir`. Po úspěšném `save_snapshot_to_db` → smazat (v finally nebo explicit).
   - Při failure — **ponechat** temp (pro retry).
   - Dokumentovat v docstring: "Při retry potřeba stejný snapshot objekt."
   - Addresses FIND-083.

3. **[spot_operator/ui/wizards/pages/save_map_page.py](spot_operator/ui/wizards/pages/save_map_page.py) — state snapshot**
   - V `initializePage`: zavolat `state.recording_service.stop_and_export()` a uložit `self._snapshot = ...` + `state.recording_snapshot = snapshot`.
   - V `_start_save`: použít `save_snapshot_to_db(self._snapshot, ...)`.
   - Na `_on_save_failed`: enable `btn_save` (už tam je), ale **NE disable service** — snapshot je stále valid.
   - Dodat pre-flight `maps_repo.exists_by_name` check → pokud uživatel čekal chvíli a název byl mezitím obsazený jinou instancí, zobrazit update "Název už je obsazen, zvol jiný."
   - Addresses FIND-140, FIND-022.

4. **[spot_operator/services/map_storage.py](spot_operator/services/map_storage.py) — wrap `validate_map_dir` s CZ**
   - V `save_map_to_db`:

     ```python
     try:
         validation = validate_map_dir(...)
     except (FileNotFoundError, ValueError) as exc:
         raise RuntimeError(f"Mapa je neúplná nebo poškozená: {exc}") from exc
     ```

   - Addresses FIND-058.

5. **[spot_operator/services/map_storage.py](spot_operator/services/map_storage.py) — retry-safe IntegrityError handling**
   - `save_map_to_db`:

     ```python
     try:
         m = maps_repo.create(...)
         s.commit()
     except IntegrityError as exc:
         s.rollback()
         raise MapNameAlreadyExistsError(name) from exc
     ```

   - Nová exception `MapNameAlreadyExistsError(RuntimeError)`.
   - Addresses FIND-025, FIND-048.

6. **[spot_operator/db/repositories/maps_repo.py](spot_operator/db/repositories/maps_repo.py) — self-contained `create`**
   - V `create`: try `flush` → při IntegrityError raise `MapNameAlreadyExistsError`. Validace neu unikne.
   - Addresses FIND-025.

### Testy

- `tests/unit/test_recording_snapshot_flow.py` — nový
  - Mock GraphNavRecorder + save failures → ověřit že snapshot je reusable.
  - `test_save_snapshot_idempotent_retry_after_transient_failure()`.

- `tests/unit/test_save_map_page.py` — nový (headless Qt)
  - Mock recording_service → `stop_and_export` vrátí snapshot → `save_snapshot_to_db` poprvé raise → retry uspěje.

- `tests/unit/test_map_storage_errors.py`
  - `test_save_raises_MapNameAlreadyExistsError_on_duplicate()`.
  - `test_save_wraps_validate_map_dir_error_with_cz_message()`.

### Rizika

- **Backward compat:** `stop_and_archive_to_db` zůstane, ale deprecated. Testy musí zůstat zelené.
- Temp dir lifecycle — pokud operátor zavře wizard uprostřed retry sekvence, temp zůstane → `cleanup_temp_root` periodický ho vyčistí.

### Rollback

- Nové metody bez DB migrace.

---

## 6. PR-05 — Playback reliability

### Cíl

Playback je předvídatelný, auditovaný a fail-fast. Žádný silent navigate do špatného místa.

### Addresses findings

FIND-086, FIND-088, FIND-089, FIND-090, FIND-091, FIND-092, FIND-093, FIND-094, FIND-095, FIND-097, FIND-099, FIND-100, FIND-101, FIND-102, FIND-103, FIND-104, FIND-067, FIND-068, FIND-087

### Změny v kódu

1. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — run se vytvoří předpre-flight**
   - V `run_all_checkpoints`: `runs_repo.create(...)` volat **jako první** (před `_extract_checkpoints`, před `_is_localized_on_current_graph`).
   - Run začíná s `status=RunStatus.running`. Pokud pre-flight selže, `runs_repo.finish(status=RunStatus.failed, abort_reason=...)`.
   - Addresses FIND-086.

2. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — single localization state call**
   - Extract pre-flight do `_pre_flight_check(meta) -> tuple[str, list[CheckpointRef]]`:
     - Jeden `client.get_localization_state()` call.
     - Z něj odvodit `localized_wp` + ověřit match.
   - Addresses FIND-087.

3. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `_consecutive_nav_fails` po recovery reset**
   - Per-iterace: `consecutive_nav_fails = 0` při success (stejně). Ale po úspěšném re-localize + retry zachovat counter. Explicit test.
   - Addresses FIND-088.

4. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — retry whitelist rozšíření**
   - `_should_retry_outcome` whitelist `{LOST, NOT_LOCALIZED, TIMEOUT, STUCK}`. `STUCK` s explicit `time.sleep(3)` před retry (pro klouzavou překážku).
   - NO_ROUTE **ne** retry (není recoverable přes re-localize).
   - Addresses FIND-089.

5. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — exception type detection místo substring**
   - `_is_robot_lost_error` zkusit:
     1. `isinstance(result.exception, RobotLostError)` (pokud `result.exception` existuje).
     2. Fallback na substring match (zachovat pro compat).
   - Import `from bosdyn.client.robot_state import RobotLostError` (nebo odkud přesně pochází — verify v autonomy).
   - Addresses FIND-090.

6. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `_warn_if_drift` emit signál**
   - Přidat Qt signal `drift_detected = Signal(str, str, str)  # cp_name, actual_wp, target_wp`.
   - Po emitu `_log.warning` zachovat.
   - Addresses FIND-097.

7. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `_localize_with_fallback` verify po NEAREST**
   - Po `self._navigator.localize(strategy=LocalizationStrategy.FIDUCIAL_NEAREST)` volat `_is_localized_on_current_graph()` + ověřit `localized_wp == meta.start_waypoint_id` (pokud je známý).
   - Addresses FIND-094.

8. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — odstranit dead branch**
   - Druhý `if localized_wp != meta.start_waypoint_id:` (řádek 649) pryč.
   - Addresses FIND-095.

9. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — odstranit dead filter**
   - `[CheckpointRef(...) for cp in plan.checkpoints if cp.waypoint_id]` → odstranit `if cp.waypoint_id` (je to mrtvé díky FIND-102).
   - Addresses FIND-102.

10. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — error classify**
    - V `run_all_checkpoints` for loop `except` klauzule:

      ```python
      except (KeyboardInterrupt, MemoryError, SystemExit):
          raise  # never swallow these
      except sqlalchemy.exc.OperationalError as exc:
          _log.error("DB transient error at %s: %s — retrying next CP", cp.name, exc)
          continue
      except sqlalchemy.exc.ProgrammingError:
          _log.exception("DB schema mismatch — aborting run")
          abort_reason = "DB schema error"
          break
      except Exception as exc:
          # existing behavior
          ...
      ```

    - Addresses FIND-099.

11. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `request_return_home` skutečně spustí return**
    - Změnit: vlákno spouštět Qt signal pro caller (UI), ne volat `request_abort()`.
    - Nebo zcela odstranit metodu + používat jen explicit `return_home` volané z UI thread handleru.
    - Ověřit v `PlaybackRunPage._on_stop_return` → používá jen `request_abort` + explicit return home thread. OK, takže `request_return_home` je dead — **odstranit**.
    - Addresses FIND-093.

12. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `return_home` pre-check**
    - Na začátku `return_home`:

      ```python
      if not self._is_localized_on_current_graph():
          raise RuntimeError("Robot není lokalizován — návrat nelze spustit.")
      ```

    - Addresses FIND-100.

13. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `_classify_final_status` priorita success**
    - Pokud `success == total`, vracet `completed` i při abort_reason. (Uživatel dokončil po aborcích.)
    - Addresses FIND-101.

14. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — detailnější "no checkpoints" zpráva**
    - `_extract_checkpoints` vrátí nejen list, ale taky diagnostiku: "payload je None" vs "všechny CP filtrovány".
    - Při prázdném seznamu raise s konkrétní příčinou.
    - Addresses FIND-103.

15. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `set_global_avoidance` dialog**
    - Emit new signal `avoidance_failed = Signal(str)` pokud selže. UI (PlaybackRunPage) poslouchá → zobrazit dialog "Avoidance nenastaven, pokračovat?".
    - Fallback: pokud UI není (test scenario), default je abort (raise).
    - Addresses FIND-104.

16. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — O(N²) payload updates**
    - Promote `checkpoint_results` do samostatné tabulky `run_checkpoint_results` s FK na `spot_runs.id`.
    - Migrace: `20260425_1000_0003_checkpoint_results_table.py`. Přesun dat z JSONB do tabulky (data migration volající `INSERT ... SELECT jsonb_array_elements(...)`)
    - Addresses FIND-092.
    - **Alternativa (lazy):** nechat JSONB, ale batch pomocí `jsonb_insert` SQL místo full rewrite.
    - **Rozhodnutí:** lazy alternative pro tento PR (rychlejší), úplný refactor odložit do PR-15 tech debt (promote to table).

17. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `localize_at_start` ambiguity**
    - Po `resp = client.set_localization(...)`:

      ```python
      ambiguity = getattr(resp, "ambiguity_result", None)
      if ambiguity and getattr(ambiguity, "ambiguous_ratio", 0) > 0.5:
          _log.warning("Localization ambiguous (ratio=%.2f) — robot could be in wrong spot.", ...)
      ```

    - (Konkrétní API záleží na bosdyn version — ověřit.)
    - Addresses FIND-067.

18. **[spot_operator/robot/localize_strict.py](spot_operator/robot/localize_strict.py) — exception classify**
    - Místo catch `Exception` ve `localize_at_start`, zkusit rozlišit bosdyn konkrétní exception types (FiducialNotFoundError apod. — verify names). Zachytit do vlastních CZ message.
    - Addresses FIND-068.

19. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `set_global_avoidance` selhání NOT log, ale exception**
    - Pokud volání selže, raise. UI (přes signal `avoidance_failed`) dá dialog; test mode defaultní je abort.
    - Zachovat warning log ale + raise.
    - Addresses FIND-104.

### Testy

- `tests/unit/test_playback_service.py` — nový (mock GraphNavNavigator)
  - `test_run_created_before_preflight()`.
  - `test_preflight_mis_localize_finishes_run_as_failed()`.
  - `test_retry_after_re_localize()`.
  - `test_robotlost_aborts_immediately()`.
  - `test_three_consecutive_failures_abort()`.
  - `test_stuck_retries_with_delay()`.
  - `test_classify_final_status_completed_beats_abort()`.
  - `test_db_operational_error_continues_run()`.
  - `test_db_programming_error_aborts()`.
  - `test_memory_error_propagates()`.
  - `test_request_return_home_method_removed()` (xfail/skip if kept as alias).
  - `test_return_home_rejects_when_not_localized()`.
  - `test_drift_detected_emits_signal()`.
  - `test_fiducial_nearest_verifies_waypoint_match()`.

- `tests/unit/test_localize_strict.py` — nový (mock bosdyn client)
  - `test_ambiguity_logs_warning()`.
  - `test_fiducial_not_visible_raises_specific_error()`.

### Rizika

- Kaskáda fixů mění behavior. Migrace dat **není** plánovaná, ale running runs při deploy mohou mít rozbitý status (stará app napsala do JSON). Mitigace: nová app tolerantní (viz PR-03 FIND-042).
- Nová `run` vytvořená v DB při pre-flight failu → audit má N runs z neúspěšných pokusů. Feature, ne bug.

### Rollback

- Commity per finding; revert-safe.

---

## 7. PR-06 — OCR worker robustness

### Cíl

OCR worker failuje explicitně (operátor vidí) při permanent chybách. Transient errors s exponential backoff. Žádné zombie stavy.

### Addresses findings

FIND-026, FIND-105, FIND-106, FIND-107, FIND-108, FIND-109, FIND-110, FIND-111, FIND-112, FIND-113, FIND-114, FIND-115, FIND-116, FIND-117, FIND-118

### Změny v kódu

1. **[spot_operator/services/ocr_worker.py](spot_operator/services/ocr_worker.py) — `PermanentOcrError`**
   - Nová exception: `class PermanentOcrError(RuntimeError): pass`.
   - V `run` loop: pokud `_claim_and_process_one` raise `PermanentOcrError`, emit signal `worker_disabled = Signal(str)` + break loop.
   - Pipeline warmup failure s FileNotFoundError / ModuleNotFoundError → wrap do PermanentOcrError.
   - Addresses FIND-106.

2. **[spot_operator/services/ocr_worker.py](spot_operator/services/ocr_worker.py) — heartbeat během OCR**
   - Nová helper v `photos_repo.heartbeat_processing(session, photo_id)` — UPDATE ocr_locked_at = now() WHERE id = :id AND ocr_locked_by = :worker_id.
   - V `_claim_and_process_one`: před `pipeline.process` spawn heartbeat thread (`threading.Thread(target=_heartbeat_loop, args=(photo_id, stop_event))`). Stop event set-ne po completion.
   - Interval 60s.
   - Zvýšit `OCR_ZOMBIE_TIMEOUT_MIN` na 15 (z 5).
   - Addresses FIND-026.

3. **[spot_operator/services/ocr_worker.py](spot_operator/services/ocr_worker.py) — `assert` → raise**
   - `assert photo_id is not None` → `if photo_id is None: raise RuntimeError("photo_id unexpectedly None")`.
   - Addresses FIND-107.

4. **[spot_operator/services/ocr_worker.py](spot_operator/services/ocr_worker.py) — docstring state machine**
   - Přidat do `_claim_and_process_one` 15-řádkový docstring popisující claim → process → commit states + recovery.
   - Addresses FIND-105.

5. **[spot_operator/services/ocr_worker.py](spot_operator/services/ocr_worker.py) — `threading.Event.wait` místo sleep loop**
   - Nahradit `for _ in range(int(wait_s * 2)): if self._stop: return; time.sleep(0.5)` za:

     ```python
     if self._stop_event.wait(timeout=wait_s):
         return
     ```

   - `self._stop_event = threading.Event()` v `__init__`, `request_stop` volá `.set()`.
   - Addresses FIND-117.

6. **[spot_operator/services/ocr_worker.py](spot_operator/services/ocr_worker.py) — periodic sweep**
   - QTimer na 10 min, volá `sweep_zombies_now` (mimo hot loop).
   - Addresses FIND-116.

7. **[spot_operator/ocr/pipeline.py](spot_operator/ocr/pipeline.py) — imdecode fail raise**
   - Pokud `cv2.imdecode` vrací None: `raise RuntimeError(f"Failed to decode image (size={len(image_bytes)} bytes)")`.
   - OCR worker v `_claim_and_process_one` to catch a `mark_failed`.
   - Addresses FIND-108.

8. **[spot_operator/ocr/detector.py](spot_operator/ocr/detector.py) — thread-safe lazy load**
   - Přidat `self._lock = threading.Lock()` v `__init__`.
   - `_ensure_loaded`:

     ```python
     if self._model is not None:
         return self._model
     with self._lock:
         if self._model is not None:
             return self._model
         # ... load
         self._model = YOLO(...)
         return self._model
     ```

   - Addresses FIND-109.

9. **[spot_operator/ocr/reader.py](spot_operator/ocr/reader.py) — refactor nested try/except**
   - Nová helper funkce `_try_run_with_confidence(reader, img) -> Any`:

     ```python
     try:
         return reader.run(img, return_confidence=True)
     except TypeError:
         return reader.run(img)
     ```

   - `read(crop_bgr)` pak: `gray = cvtColor(...)`, try `_try_run_with_confidence(reader, gray)`; except Exception → RGB fallback s retry wrapper.
   - Zjednodušit na 2 úrovně místo 3.
   - Propagate `KeyboardInterrupt` a `MemoryError`.
   - Addresses FIND-110.

10. **[spot_operator/ocr/reader.py](spot_operator/ocr/reader.py) — `_normalize_plate` length check**
    - Import `PLATE_TEXT_REGEX` from `spot_operator.constants`.
    - V `_normalize_plate`:

      ```python
      result = "".join(...)
      if result and not re.match(PLATE_TEXT_REGEX, result):
          _log.warning("Normalized plate %r doesn't match PLATE_TEXT_REGEX; returning empty", result)
          return ""
      return result
      ```

    - Addresses FIND-112.

11. **[spot_operator/ocr/fallback.py](spot_operator/ocr/fallback.py) — PyInstaller detection**
    - Na začátku `reprocess_bytes`:

      ```python
      if getattr(sys, 'frozen', False):
          _log.error("Nomeroff fallback not supported in frozen build")
          return []
      ```

    - Addresses FIND-113.

12. **[spot_operator/ocr/fallback.py](spot_operator/ocr/fallback.py) — unlink logging**
    - V `finally`:

      ```python
      try:
          if temp_path.exists():
              temp_path.unlink()
      except OSError as exc:
          _log.warning("Failed to delete temp OCR file %s: %s", temp_path, exc)
      ```

    - Addresses FIND-114.

13. **[spot_operator/ocr/fallback.py](spot_operator/ocr/fallback.py) — timeout konfigurovatelný**
    - `_SUBPROCESS_TIMEOUT_SEC = 60` (z 30).
    - Později: přes env var `OCR_FALLBACK_TIMEOUT_SEC`.
    - Addresses FIND-115.

14. **[spot_operator/ocr/dtos.py](spot_operator/ocr/dtos.py) — `to_db_row` defensive**
    - `"plate_text": self.plate or None` — pokud `plate == ""`, raise `ValueError("Detection.plate must not be empty")`.
    - (Pipeline už filtruje, ale defensive.)
    - Addresses FIND-118.

15. **[tests/unit/test_ocr_reader.py](tests/unit/test_ocr_reader.py) — nový test pro `_unpack_result`**
    - Pokrýt všech 6 formátů: tuple, dict, list, string, PlatePrediction, with/without confidence.
    - Addresses FIND-111.

### Testy

- `tests/unit/test_ocr_worker.py` — nový (pure mock)
  - `test_permanent_error_stops_loop()`.
  - `test_heartbeat_updates_lock_timestamp()`.
  - `test_transient_error_backoff()`.
  - `test_stop_event_interrupts_backoff()`.
  - `test_imdecode_failure_marks_failed()`.

- `tests/unit/test_ocr_detector_threading.py` — nový
  - Dva souběžné `_ensure_loaded` volání, ověřit jediný YOLO instance (via mock).

- `tests/unit/test_ocr_fallback.py` — nový
  - `test_frozen_mode_returns_empty()`.
  - `test_temp_file_cleanup_on_exception()`.
  - Mock subprocess.run → timeout → vrátí `[]`.

### Rizika

- Heartbeat thread — pokud crash v pipeline, heartbeat nemusí vědět a zaparkuje. Mitigace: heartbeat respektuje process-level stop_event.
- Nová `PermanentOcrError` — UI musí poslouchat `worker_disabled` signál a reagovat (status bar).

### Rollback

- Čistě kódová změna.

---

## 8. PR-07 — DB hardening

### Cíl

DB vrstva je korektní po commit/rollback, nemá TOCTOU races, cascade delete je bezpečný, schema odpovídá modelům.

### Addresses findings

FIND-011, FIND-012, FIND-013, FIND-014, FIND-015, FIND-016, FIND-017, FIND-018, FIND-019, FIND-021, FIND-023, FIND-024, FIND-027, FIND-028, FIND-029, FIND-030, FIND-031, FIND-032, FIND-033, FIND-034

### Změny v kódu

1. **[alembic/versions/20260425_1100_0003_schema_cleanup.py](alembic/versions/) — nová migrace**
   - Řešíme inkonzistenci migrace 0002: sloupce jsou nullable=False ale bez server_default.
   - Pro každý sloupec: `op.alter_column("spot_runs", "checkpoint_results_json", server_default=sa.text("'[]'::jsonb"))`.
   - Stejně pro `metadata_version`, `archive_is_valid`, `return_home_status`.
   - Addresses FIND-011, FIND-019.

2. **[alembic/versions/20260425_1100_0003_schema_cleanup.py](alembic/versions/) — NULLS NOT DISTINCT**
   - Pokud PG >= 15:

     ```python
     op.execute("""
         ALTER TABLE plate_detections
         DROP CONSTRAINT IF EXISTS ux_det_photo_engine_plate;
         ALTER TABLE plate_detections
         ADD CONSTRAINT ux_det_photo_engine_plate
         UNIQUE NULLS NOT DISTINCT (photo_id, engine_name, plate_text);
     """)
     ```

   - Fallback pro PG<15: `CREATE UNIQUE INDEX` s `COALESCE(plate_text, '')` expression.
   - Addresses FIND-017.

3. **[spot_operator/db/repositories/photos_repo.py](spot_operator/db/repositories/photos_repo.py) — `insert_many` s NULL handling**
   - Alternativa: nevkládat detection s `plate_text=NULL`. Reader (pipeline) už filtruje, ale defensive check v `insert_many`:

     ```python
     rows = [r for r in rows if r.get("plate_text")]
     ```

   - Addresses FIND-028.

4. **[spot_operator/db/models.py](spot_operator/db/models.py) — `Map.default_capture_sources` default**
   - `default_capture_sources: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)`.
   - Addresses FIND-018.

5. **[spot_operator/db/migrations.py](spot_operator/db/migrations.py) — friendly error wrap**
   - `upgrade_to_head`:

     ```python
     try:
         command.upgrade(cfg, "head")
     except Exception as exc:
         _log.exception("Alembic upgrade failed")
         raise RuntimeError(f"Migrace databáze selhala: {exc}. Zkontroluj připojení k DB a logy.") from exc
     ```

   - Addresses FIND-012.

6. **[spot_operator/db/migrations.py](spot_operator/db/migrations.py) — `current_revision` reuse**
   - Pokud existuje global `_engine` → použít ho místo ephemeral.
   - Addresses FIND-013.

7. **[spot_operator/db/engine.py](spot_operator/db/engine.py) — dokumentace + per-worker cleanup helper**
   - Docstring na module-level popisující `expire_on_commit=False` invariant.
   - Nová helper `thread_local_session_remove()` — jednoduchý wrapper `_session_factory.remove()` pro volání v `finally` worker threadů.
   - Addresses FIND-014, FIND-015.

8. **[spot_operator/services/ocr_worker.py](spot_operator/services/ocr_worker.py) — session cleanup po run**
   - V `run()` finally:

     ```python
     try:
         thread_local_session_remove()
     except Exception:
         pass
     ```

   - Addresses FIND-015.

9. **[spot_operator/db/repositories/runs_repo.py](spot_operator/db/repositories/runs_repo.py) — `mark_progress`/`finish` rowcount**
   - Each UPDATE: `if result.rowcount != 1: raise RuntimeError(f"Run {run_id} not found")`.
   - Addresses FIND-023.

10. **[spot_operator/db/repositories/runs_repo.py](spot_operator/db/repositories/runs_repo.py) — `generate_unique_run_code` retry on IntegrityError**
    - Changes design: místo check-then-insert, integrity retry ve wrapper:

      ```python
      def create_run_with_unique_code(session, *, ..., max_attempts=5) -> SpotRun:
          for attempt in range(max_attempts):
              try:
                  code = generate_run_code(attempt=attempt)
                  return create(session, run_code=code, ...)
              except IntegrityError:
                  session.rollback()
          raise RuntimeError(...)
      ```

    - Playback používá tuto novou funkci.
    - Addresses FIND-024.

11. **[spot_operator/db/repositories/plates_repo.py](spot_operator/db/repositories/plates_repo.py) — `upsert` na ON CONFLICT**
    - Použít `pg_insert(LicensePlate).on_conflict_do_update(...)`.
    - Addresses FIND-027.

12. **[spot_operator/db/repositories/photos_repo.py](spot_operator/db/repositories/photos_repo.py) — `reset_all_to_pending` batch delete**
    - Odstranit loop `for photo_id in photo_ids: delete_for_photo`. Nahradit `detections_repo.delete_for_photo_ids(photo_ids)`:

      ```python
      def delete_for_photo_ids(session, photo_ids: Sequence[int]) -> int:
          if not photo_ids:
              return 0
          result = session.execute(
              sqldelete(PlateDetection).where(PlateDetection.photo_id.in_(photo_ids))
          )
          return result.rowcount or 0
      ```

    - Addresses FIND-029.

13. **[spot_operator/db/repositories/photos_repo.py](spot_operator/db/repositories/photos_repo.py) — `_to_photo_row` no UI fallback**
    - `plates=tuple(d.plate_text for d in photo.detections)` — povoluje None.
    - UI layer (`photos_model.cell`) si vykreslí None jako `"?"`.
    - Addresses FIND-030.

14. **[spot_operator/db/repositories/photos_repo.py](spot_operator/db/repositories/photos_repo.py) — `fetch_last_image_bytes_for_plate` normalize**
    - Import `from spot_operator.db.repositories.plates_repo import normalize_plate_text`.
    - `normalized = normalize_plate_text(plate_text)`.
    - Stejně v `get_last_photo_for_plate`.
    - Addresses FIND-031.

15. **[spot_operator/services/photo_sink.py](spot_operator/services/photo_sink.py) — atomic photo_id**
    - Změnit:

      ```python
      with Session() as s:
          photo = photos_repo.insert(s, ...)
          s.flush()
          photo_id = photo.id
          s.commit()  # commit may fail; photo_id is already generated but data rolled back
      return photo_id
      ```

    - Po změně: použít `s.commit()` v `with` bloku (implicit close), zachytit commit error a re-raise s clear message. Flush je důležitý pro id populace; commit se stane na `__exit__`. Moved `photo_id = photo.id` **před** return, ale dokumentovat že pokud exception, photo_id je invalid.
    - Unit test ověří rollback scenář.
    - Addresses FIND-032.

16. **[spot_operator/db/repositories/runs_repo.py](spot_operator/db/repositories/runs_repo.py) — `RunRow.status: RunStatus`**
    - Změnit typ z `str` na `RunStatus` enum.
    - CRUD layer konvertuje na display string přes `status.value` nebo CZ label dict.
    - Addresses FIND-033.

17. **[spot_operator/db/repositories/photos_repo.py](spot_operator/db/repositories/photos_repo.py) — `claim_next_pending` dokumentace**
    - Docstring: "Volající MUSÍ commitnout session ihned po claim. Jinak se ORM Photo instance dostane do detached state s stale data." + explicitní contract.
    - Volitelně: přemístit commit *do* funkce (session commit uvnitř). Alternativně: vracet jen `photo_id + image_bytes tuple`, ne ORM objekt.
    - Rozhodnutí: vrátit `tuple[int, bytes] | None` (lightweight). Změna callsite v ocr_worker.
    - Addresses FIND-034.

18. **[spot_operator/db/repositories/photos_repo.py](spot_operator/db/repositories/photos_repo.py) — `sweep_zombies` parametrizace**
    - Parameter `timeout_minutes` default z constants `OCR_ZOMBIE_TIMEOUT_MIN`.
    - Nový helper `record_heartbeat(session, photo_id, worker_id)` pro FIND-026.
    - Addresses FIND-021.

19. **[spot_operator/db/repositories/runs_repo.py](spot_operator/db/repositories/runs_repo.py) — batch delete run**
    - Nová metoda:

      ```python
      def delete_cascade_batched(session, run_id: int, photo_batch_size: int = 500) -> None:
          """Smaže run s photos v batch po batch_size, commit per batch."""
      ```

    - Používá `detections_repo.delete_for_photo_ids` + `DELETE photos ... LIMIT N` v SQL (PG specific).
    - CRUD tlačítko Delete Run volá tuto metodu.
    - Addresses FIND-016.

### Testy

- `tests/unit/test_runs_repo.py` — nový / rozšířit
  - `test_mark_progress_raises_on_unknown_run_id()`.
  - `test_create_run_with_unique_code_retries_on_conflict()`.
  - `test_delete_cascade_batched()`.

- `tests/unit/test_photos_repo.py` — nový / rozšířit
  - `test_claim_returns_tuple_not_orm_instance()`.
  - `test_reset_all_to_pending_single_query()`.
  - `test_fetch_last_image_bytes_normalizes_plate()`.
  - `test_heartbeat_updates_locked_at()`.

- `tests/unit/test_plates_repo.py` — rozšířit
  - `test_upsert_on_conflict_updates_status()`.
  - `test_upsert_parallel_no_integrity_error()` (simulace thread, requires SQLite in-memory).

- `tests/unit/test_photo_sink.py` — nový
  - Mock session commit → raise → ověřit, že photo_id není vrácen.

### Rizika

- Migrace 0003 je PG version-dependent (NULLS NOT DISTINCT jen PG15+). Mitigace: check verze v migration + fallback na expression unique index.
- Změna `claim_next_pending` signature → breaking change interní API. OCR worker musí aktualizovat v jednom commitu se změnou.

### Rollback

- Migrace má `downgrade()` — revert vrací starý constraint / nullable.

---

## 9. PR-08 — Qt signal lifecycle + concurrency

### Cíl

Žádné signály do zničených widgetů. Žádné race conditions v table models. Thread cleanup je deterministický.

### Addresses findings

FIND-141, FIND-142, FIND-143, FIND-145, FIND-146, FIND-148, FIND-149, FIND-152, FIND-158, FIND-159, FIND-160, FIND-164, FIND-165, FIND-167, FIND-169

### Změny v kódu

1. **[spot_operator/ui/common/workers.py](spot_operator/ui/common/workers.py) — helper `disconnect_all_signals`**
   - Nová utility:

     ```python
     def disconnect_all_signals(obj: QObject) -> None:
         """Bezpečně odpojí všechny signály obj. Idempotent."""
         for attr_name in dir(obj):
             try:
                 attr = getattr(obj, attr_name, None)
                 if isinstance(attr, QSignalBase):
                     attr.disconnect()
             except (TypeError, RuntimeError):
                 pass
     ```

   - Volat v každém teardown.

2. **[spot_operator/ui/wizards/pages/playback_run_page.py](spot_operator/ui/wizards/pages/playback_run_page.py) — disconnect PlaybackService signály**
   - V `_teardown`:

     ```python
     if self._service is not None:
         try:
             self._service.progress.disconnect(self._append_log)
             self._service.run_started.disconnect(self._on_run_started)
             self._service.checkpoint_reached.disconnect()  # lambda, disconnect all
             self._service.photo_taken.disconnect()
             self._service.run_completed.disconnect()
             self._service.run_failed.disconnect()
         except (TypeError, RuntimeError):
             pass
     ```

   - Stejně v `teleop_record_page._teardown` pro RecordingService (pokud by emitovalo).
   - Addresses FIND-141.

3. **[spot_operator/ui/wizards/pages/playback_run_page.py](spot_operator/ui/wizards/pages/playback_run_page.py) — `_on_run_failed` enable Next**
   - `self._btn_next.setEnabled(True)` vždy (i když `run_id is None`).
   - ResultPage si poradí s `run_id=None` (viz dále v PR-09).
   - Addresses FIND-142.

4. **[spot_operator/ui/wizards/pages/playback_run_page.py](spot_operator/ui/wizards/pages/playback_run_page.py) — bundle is None state**
   - Pokud bundle is None v `initializePage`:
     - Nastavit `self._btn_start.setEnabled(False)`.
     - `self._status_label.setText("❌ Spot není připojen. Zavři wizard a připoj se znovu.")`.
     - Skip `_ensure_live_view`, `_ensure_estop_widget`.
   - Addresses FIND-143.

5. **[spot_operator/ui/wizards/pages/playback_run_page.py](spot_operator/ui/wizards/pages/playback_run_page.py) — `_on_ocr_done` v BG thread**
   - Místo direct DB volání:

     ```python
     worker = FunctionWorker(lambda pid=photo_id: self._load_plates_for_photo(pid))
     worker.finished_ok.connect(lambda plates: self._append_log(f"  🔤 SPZ: {plates}"))
     worker.failed.connect(lambda err: self._append_log(f"  🔤 SPZ: (load failed: {err})"))
     worker.start()
     ```

   - Addresses FIND-145.

6. **[spot_operator/ui/wizards/pages/save_map_page.py](spot_operator/ui/wizards/pages/save_map_page.py) — `_is_name_valid` debounce**
   - QTimer `_name_validate_timer` s `CRUD_SEARCH_DEBOUNCE_MS=200`. Restart na každý `textChanged`.
   - Validate jen po debounce.
   - Addresses FIND-146.

7. **[spot_operator/ui/wizards/pages/teleop_record_page.py](spot_operator/ui/wizards/pages/teleop_record_page.py) — `_poller` teardown**
   - V `_teardown`: `self._poller = None` (po zastavení image pipeline).
   - Addresses FIND-148.

8. **[spot_operator/ui/wizards/pages/playback_run_page.py](spot_operator/ui/wizards/pages/playback_run_page.py) + [teleop_record_page.py](spot_operator/ui/wizards/pages/teleop_record_page.py) — live view placeholder**
   - `_ensure_live_view` / `_ensure_image_pipeline` při failure: `self._live_placeholder.setText("⚠ Live view nedostupný")`.
   - Addresses FIND-149.

9. **[spot_operator/ui/wizards/pages/playback_run_page.py](spot_operator/ui/wizards/pages/playback_run_page.py) — E-Stop callback reset**
   - V `_teardown`: `self.wizard().set_estop_callback(None, None)`.
   - `base_wizard.set_estop_callback`: přijímá None, updatuje `_estop_callback = None`.
   - Addresses FIND-152.

10. **[spot_operator/ui/common/workers.py](spot_operator/ui/common/workers.py) — `cleanup_worker` race fix**
    - Místo `worker.deleteLater()` hned po `stop_and_wait`:

      ```python
      worker.finished.connect(worker.deleteLater)
      ```

      nejdřív, pak `stop_and_wait`.
    - Addresses FIND-158.

11. **[spot_operator/ui/common/workers.py](spot_operator/ui/common/workers.py) — `DbQueryWorker` cancel**
    - `requestInterruption` samo DB neruší. Přidat docstring "DbQueryWorker nelze přerušit uprostřed DB query — max timeout je `statement_timeout` v PG."
    - (Implementace SQL cancel je mimo scope, jen dokumentace.)
    - Addresses FIND-159.

12. **[spot_operator/ui/main_window.py](spot_operator/ui/main_window.py) — `_periodic_temp_cleanup` race**
    - Mutex: `self._cleanup_running = False`. Ignorovat pokud už běží, a aktualizovat v timer handleru atomicky.
    - Addresses FIND-160.

13. **[spot_operator/ui/common/table_models/paged_table_model.py](spot_operator/ui/common/table_models/paged_table_model.py) — worker cleanup**
    - V `fetchMore` worker connect:

      ```python
      worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
      worker.finished.connect(worker.deleteLater)
      ```

    - Addresses FIND-164.

14. **[spot_operator/ui/common/table_models/paged_table_model.py](spot_operator/ui/common/table_models/paged_table_model.py) — `_fetching` flag reset**
    - Každý `_on_page` a `_on_fail`: `if req_id == self._request_id: self._fetching = False`. Taky na `reset()` reset flag.
    - Addresses FIND-165.

15. **[spot_operator/ui/crud/photo_detail_dialog.py](spot_operator/ui/crud/photo_detail_dialog.py) — Re-OCR tlačítko debounce**
    - Na klik: `self._btn_reocr.setEnabled(False)`. Enable zpět v `_on_reocr_done` / `_on_reocr_failed`.
    - Addresses FIND-167.

16. **Všechny CRUD tab** ([photos_tab.py](spot_operator/ui/crud/photos_tab.py), [runs_tab.py](spot_operator/ui/crud/runs_tab.py), [spz_tab.py](spot_operator/ui/crud/spz_tab.py)) — close cleanup
    - Přidat metodu `closeEvent` / `hideEvent` (podle containeru) volající `self._model.stop_all_workers()`.
    - `PagedTableModel.stop_all_workers()` nová method: iterate `self._workers`, call `cleanup_worker`.
    - Addresses FIND-169.

### Testy

- `tests/unit/test_workers.py` — nový
  - `test_cleanup_worker_handles_already_finished()`.
  - `test_disconnect_all_signals()`.
  - Parallel fetch + reset, ověřit že starý výsledek je zahozen.

- `tests/unit/test_paged_table_model.py` — nový
  - `test_worker_removed_from_list_on_finish()`.
  - `test_reset_during_fetch_drops_stale_result()`.
  - `test_stop_all_workers_on_close()`.

### Rizika

- `disconnect` bez specifikovaného slotu disconnectuje **všechny** sloty. Pro signály do externích callerů (jiný modul) to může být příliš agresivní. Mitigace: v teardown ukládat handler references a disconnect specifické.

### Rollback

- Kódová změna, žádná DB.

---

## 10. PR-09 — Wizard UX polish + type consistency

### Cíl

Wizardy sdílejí typed state API. Silné typy eliminují `# type: ignore`. UX je konzistentní.

### Addresses findings

FIND-130, FIND-131, FIND-132, FIND-133, FIND-135, FIND-136, FIND-137, FIND-139, FIND-150, FIND-151, FIND-153, FIND-045, FIND-046, FIND-041, FIND-043

### Změny v kódu

1. **[spot_operator/ui/wizards/state.py](spot_operator/ui/wizards/state.py) — WalkWizardState**
   - Nová dataclass `WalkWizardState` (i kdyby jen prázdná):

     ```python
     @dataclass(slots=True)
     class WalkWizardState:
         spot_ip: str | None = None
         available_sources: list[str] = field(default_factory=list)
         fiducial_id: int | None = None
         lifecycle: str = WIZARD_LIFECYCLE_PREPARING
     ```

   - Addresses FIND-130.

2. **[spot_operator/ui/wizards/walk_wizard.py](spot_operator/ui/wizards/walk_wizard.py) — použít WalkWizardState**
   - `self.set_flow_state(WalkWizardState())` v `__init__`.
   - `_populate_props_from_bundle` ukládá do state místo Qt property.
   - Addresses FIND-130.

3. **[spot_operator/ui/wizards/state.py](spot_operator/ui/wizards/state.py) — `fiducial_id` → `detected_fiducial_id`**
   - Rename v `PlaybackWizardState`. Aktualizace všech call-sitů.
   - Addresses FIND-132.

4. **[spot_operator/ui/wizards/base_wizard.py](spot_operator/ui/wizards/base_wizard.py) — `flow_state` typed**
   - Přidat generic typing:

     ```python
     from typing import Generic, TypeVar
     TState = TypeVar("TState")

     class SpotWizard(QWizard, Generic[TState]):
         def flow_state(self) -> TState | None: ...
     ```

   - Wizard classes dědí `SpotWizard[RecordingWizardState]`, `SpotWizard[PlaybackWizardState]`, `SpotWizard[WalkWizardState]`.
   - Pages pak `self.wizard().flow_state()` je typovaný.
   - Odstranit `# type: ignore[attr-defined]` napříč pages.
   - Addresses FIND-153.

5. **[spot_operator/ui/wizards/*.py](spot_operator/ui/wizards/) — `recording_state()` / `playback_state()` raise místo assert**
   - `if not isinstance(state, RecordingWizardState): raise RuntimeError(...)`.
   - Addresses FIND-131.

6. **[spot_operator/ui/wizards/base_wizard.py](spot_operator/ui/wizards/base_wizard.py) — `_should_confirm_close` lifecycle**
   - Místo `return self._bundle is not None`:

     ```python
     state = self.flow_state()
     lifecycle = getattr(state, "lifecycle", None)
     return lifecycle in {WIZARD_LIFECYCLE_RUNNING, WIZARD_LIFECYCLE_ABORTING, WIZARD_LIFECYCLE_RETURNING}
     ```

   - Addresses FIND-133.

7. **[spot_operator/ui/wizards/base_wizard.py](spot_operator/ui/wizards/base_wizard.py) — `safe_abort` + `closeEvent` failure handling**
   - Při selhání `safe_abort`: emit signal `cleanup_failed`, dialog "Úklid selhal — pokus o ukončení přesto?", ignore if rejected.
   - Addresses FIND-135.

8. **[spot_operator/robot/session_factory.py](spot_operator/robot/session_factory.py) — helper `bundle.get_info()`**
   - Nová method `SpotBundle.get_info() -> BundleInfo`:

     ```python
     @dataclass(frozen=True, slots=True)
     class BundleInfo:
         hostname: str | None
         available_sources: list[str]

     def get_info(self) -> BundleInfo:
         hostname = getattr(self.session, "hostname", None)
         # ... list_sources, catch Exception
     ```

   - Všechny 3 wizardy `_populate_props_from_bundle` → `info = bundle.get_info(); state.spot_ip = info.hostname; ...`.
   - Addresses FIND-136.

9. **[spot_operator/ui/wizards/base_wizard.py](spot_operator/ui/wizards/base_wizard.py) — F1 `Qt.WindowShortcut`**
   - `self._f1_shortcut.setContext(Qt.WindowShortcut)`.
   - Addresses FIND-137.

10. **[spot_operator/ui/wizards/base_wizard.py](spot_operator/ui/wizards/base_wizard.py) — close message centralize**
    - Přesunout strings do konstant v `spot_operator/ui/wizards/messages.py` (nový modul):

      ```python
      CLOSE_WARNING_RECORDING = "Nahrávání probíhá. Po zavření se nahrávka zruší..."
      CLOSE_WARNING_PLAYBACK = "Autonomní jízda probíhá..."
      CLOSE_WARNING_WALK = "Chůze se Spotem — po zavření..."
      ```

    - Addresses FIND-139.

11. **[spot_operator/ui/wizards/pages/teleop_record_page.py](spot_operator/ui/wizards/pages/teleop_record_page.py) — dead `recording_finished` signal**
    - Odstranit: `recording_finished = Signal()` (řádek 49) + emit (řádek 566).
    - Addresses FIND-150.

12. **[spot_operator/ui/wizards/pages/teleop_record_page.py](spot_operator/ui/wizards/pages/teleop_record_page.py) — HealthMonitor cache**
    - V `_ensure_image_pipeline`: `self._health_monitor = HealthMonitor(bundle.session)`.
    - `_poll_battery` reuse `self._health_monitor`.
    - Addresses FIND-151.

13. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — `CheckpointRef` → frozen tuple**
    - Změnit:

      ```python
      @dataclass(frozen=True, slots=True)
      class CheckpointRef:
          ...
          capture_sources: tuple[str, ...]
      ```

    - Aktualizovat callsites.
    - Addresses FIND-045.

14. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — `build_checkpoint_result` enum kontrola**
    - Přijímat `NavigationOutcome` nebo string; normalizovat na value:

      ```python
      def build_checkpoint_result(*, nav_outcome: NavigationOutcome | str, ...):
          nav_outcome_str = nav_outcome.value if isinstance(nav_outcome, NavigationOutcome) else nav_outcome.lower()
          # whitelist check
      ```

    - Addresses FIND-046.

15. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — Protocol pro checkpoint payload**
    - Nová `Protocol`:

      ```python
      class CheckpointPayloadSource(Protocol):
          name: str
          waypoint_id: str
          kind: str
          capture_sources: Sequence[str]
          capture_status: str
          saved_sources: Sequence[str]
          failed_sources: Sequence[str]
          note: str
          created_at: str
      ```

    - `build_checkpoint_plan_payload(..., checkpoints: Iterable[CheckpointPayloadSource]) -> dict`.
    - Odstranit `getattr(..., default)` — atribut musí existovat.
    - Addresses FIND-041.

### Testy

- `tests/unit/test_wizard_state.py` — nový
  - Generic typing test (pytest + mypy/pyright static).
  - `WalkWizardState` holds fiducial_id correctly.

- `tests/unit/test_checkpoint_ref.py` — nový
  - Frozen semantic, hashable.

- `tests/unit/test_build_checkpoint_result.py` — rozšířit
  - Accept enum + string, reject unknown nav_outcome.

### Rizika

- `Generic[TState]` v Qt QWizard může mít edge cases s Qt metaclass (pyside 6). Ověřit.
- Rename field `fiducial_id` → `detected_fiducial_id` dotkne se ~5 souborů. Ruční migrace.

### Rollback

- Kódová změna, žádná DB.

---

## 11. PR-10 — Credentials & WiFi

### Cíl

Hesla nejsou v plain-textu na disku (pokud je možné). Wi-Fi check je robustní přes locale. Config validace dává srozumitelné chyby.

### Addresses findings

FIND-001, FIND-002, FIND-003, FIND-004, FIND-119, FIND-120, FIND-121, FIND-122, FIND-123, FIND-124, FIND-125, FIND-126, FIND-127, FIND-128, FIND-129

### Změny v kódu

1. **[.env.example](.env.example) + [spot_operator/config.py](spot_operator/config.py) — DB keyring support**
   - Nový env var `DATABASE_URL_TEMPLATE` s placeholder `{password}`.
   - Nový env var `DATABASE_PASSWORD_KEYRING_KEY`.
   - Pokud `DATABASE_URL_TEMPLATE` set: `password = keyring.get_password(..., keyring_key)`, composite URL.
   - Backward compat: `DATABASE_URL` stále funguje.
   - Addresses FIND-001.
   - **Odložit:** actual migration existujícího `.env` je manuální task uživatele.

2. **[spot_operator/config.py](spot_operator/config.py) — helper `_require_float`, `_require_int` s rozsahy**
   - Nové helpers:

     ```python
     def _require_float(key: str, default: str, *, min_val: float | None = None, max_val: float | None = None) -> float:
         raw = os.environ.get(key, default)
         try:
             value = float(raw)
         except ValueError as exc:
             raise RuntimeError(f"Proměnná {key}={raw!r} není číslo: {exc}") from exc
         if min_val is not None and value < min_val:
             raise RuntimeError(f"Proměnná {key}={value} musí být >= {min_val}")
         # ...
         return value
     ```

   - Použít v `AppConfig.load_from_env`.
   - Addresses FIND-003.

3. **[spot_operator/config.py](spot_operator/config.py) — LOG_LEVEL validace**
   - `if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}: raise RuntimeError(...)`.
   - Addresses FIND-002.

4. **[spot_operator/config.py](spot_operator/config.py) — friendly error + `.env.example` check**
   - `_require`:

     ```python
     if not value:
         example_exists = (ROOT / ".env.example").is_file()
         hint = "Zkopíruj .env.example na .env a doplň ji." if example_exists else f"Nastav env proměnnou '{key}'."
         raise RuntimeError(f"Chybí povinná proměnná '{key}'. {hint}")
     ```

   - Addresses FIND-004.

5. **[spot_operator/services/credentials_service.py](spot_operator/services/credentials_service.py) — DB-first order**
   - `save_credentials`:

     ```python
     with Session() as s:
         existing = credentials_repo.get_by_label(s, label)
         if existing:
             old_keyring_ref = existing.keyring_ref
             existing.hostname = hostname
             existing.username = username
             existing.keyring_ref = keyring_ref
             s.commit()
         else:
             row = credentials_repo.create(s, ...)
             s.commit()
     # DB committed first
     try:
         keyring.set_password(service_name, keyring_ref, password)
     except KeyringError as exc:
         # keyring setup failed — rollback DB? No, we had the metadata...
         raise RuntimeError(f"Heslo nebylo uloženo do Windows Credential Locker: {exc}") from exc

     # Cleanup old keyring ref if changed
     if existing and old_keyring_ref != keyring_ref:
         try:
             keyring.delete_password(service_name, old_keyring_ref)
         except KeyringError:
             pass
     ```

   - Addresses FIND-119, FIND-120.

6. **[spot_operator/services/credentials_service.py](spot_operator/services/credentials_service.py) — `load_password` raise**
   - Rozdělit: `load_password_strict(...)` raise, `load_password(...)` vrátí None s logem (backward compat).
   - UI použije strict variant a zobrazí dialog na error.
   - Addresses FIND-121.

7. **[spot_operator/services/credentials_service.py](spot_operator/services/credentials_service.py) — `delete_credentials` vrací tuple**
   - `delete_credentials(...) -> tuple[bool, bool]` — (db_deleted, keyring_deleted).
   - UI zobrazí warning pokud keyring delete failed.
   - Addresses FIND-122.

8. **[spot_operator/services/credentials_service.py](spot_operator/services/credentials_service.py) — `_build_keyring_ref` URL-encode**
   - Použít `urllib.parse.quote(label, safe='')` + `quote(username, safe='')`:

     ```python
     return f"{quote(label, safe='')}:{quote(username, safe='')}"
     ```

   - Addresses FIND-123.

9. **[spot_operator/services/credentials_service.py](spot_operator/services/credentials_service.py) — `service_name` central**
   - Module-level `_SERVICE_NAME: str | None = None`.
   - `initialize_service(service_name: str) -> None` volané z main.py po config load.
   - Všechny public funkce: bez `service_name` parametru, čte z module-level.
   - Addresses FIND-129.

10. **[spot_operator/services/spot_wifi.py](spot_operator/services/spot_wifi.py) — locale-independent ping**
    - Nahradit text parsing za "počet úspěšných pingů přes N × run 1-ping":

      ```python
      def _ping(ip: str, *, count: int, timeout_s: float) -> int:
          responses = 0
          for _ in range(count):
              args = [...]  # 1 ping, timeout
              proc = subprocess.run(args, timeout=timeout_s + 1, capture_output=True, text=True)
              if proc.returncode == 0:
                  responses += 1
          return responses
      ```

    - Jedním returncode per ping = lokálně-independent.
    - Addresses FIND-124.

11. **[spot_operator/services/spot_wifi.py](spot_operator/services/spot_wifi.py) — timeout cap**
    - `timeout=min(timeout_s * count + 5, 30)`.
    - Addresses FIND-125.

12. **[spot_operator/services/spot_wifi.py](spot_operator/services/spot_wifi.py) — TCP connect log**
    - `except Exception as exc: _log.debug("TCP %s:%d failed: %s", ip, port, exc); return False`.
    - Addresses FIND-126.

13. **[spot_operator/services/spot_wifi.py](spot_operator/services/spot_wifi.py) — non-Windows**
    - `open_windows_wifi_menu` raise `NotImplementedError` na non-Windows.
    - UI tlačítko skrýt na non-Windows (podmíněně).
    - Addresses FIND-127.

14. **[spot_operator/services/spot_wifi.py](spot_operator/services/spot_wifi.py) — `WifiCheckResult.ok` lenient**
    - `return self.tcp_reachable` (TCP je autoritativní).
    - Ping je jen info (zobrazit v detail, ne součást `.ok`).
    - Addresses FIND-128.

### Testy

- `tests/unit/test_config.py` — nový
  - `test_log_level_validated()`.
  - `test_env_float_range_check()`.
  - `test_missing_db_url_friendly_error()`.

- `tests/unit/test_credentials_service.py` — nový (mock keyring)
  - `test_save_db_first_then_keyring()`.
  - `test_save_deletes_old_keyring_ref_on_username_change()`.
  - `test_delete_returns_tuple()`.
  - `test_keyring_ref_url_encoded()`.

- `tests/unit/test_spot_wifi.py` — nový
  - `test_ping_count_from_returncode()` (mock subprocess.run with returncode 0/1).
  - `test_ok_based_on_tcp_only()`.

### Rizika

- Keyring URL-encode mění existujícíkeyring refs — DB je neaktualizuje. Mitigace: migrace — script "upgrade_keyring_refs.py" který iteruje `spot_credentials`, čte heslo pod starým ref, ukládá pod novým, maže starý. Ruční sysadmin task.
- **Odloženo: DB keyring** je opt-in. Pokud uživatel nemigruje, stará cesta stále funguje.

### Rollback

- Opt-in feature (DB keyring) — bezpečný.

---

## 12. PR-11 — Bootstrap & main entry

### Cíl

Spuštění aplikace je robustní, fail-fast, s jasnými error dialogy.

### Addresses findings

FIND-005, FIND-006, FIND-007, FIND-008, FIND-009, FIND-010, FIND-155, FIND-157, FIND-161, FIND-163, FIND-171, FIND-172, FIND-173, FIND-174, FIND-175, FIND-196

### Změny v kódu

1. **[main.py](main.py) — Python version check**
   - Na začátku `main()`:

     ```python
     if sys.version_info[:2] != (3, 10):
         _fatal_dialog(f"Vyžaduje Python 3.10, máš {sys.version_info.major}.{sys.version_info.minor}.")
         return 3
     ```

   - Addresses FIND-196.

2. **[main.py](main.py) — OCR worker shutdown 30s**
   - `ocr_worker.wait(30000)`.
   - Addresses FIND-171.

3. **[main.py](main.py) — cleanup consistency**
   - `cleanup_temp_root` failure: `log.error("temp cleanup failed: %s — pokračuji", exc)`. Explicit, ne warning.
   - Addresses FIND-172.

4. **[main.py](main.py) — `_fatal_dialog` cleanup**
   - Správný Qt lifecycle:

     ```python
     def _fatal_dialog(message: str) -> None:
         app = QApplication.instance()
         ephemeral = False
         if app is None:
             app = QApplication(sys.argv)
             ephemeral = True
         box = QMessageBox()
         # ...
         box.exec()
         if ephemeral:
             app.quit()
     ```

   - Addresses FIND-173.

5. **[main.py](main.py) — `inject_paths` uvnitř `main()`**
   - Přesunout volání `inject_paths()` do `main()` funkce. Top-level import zůstat.
   - Addresses FIND-174.

6. **[main.py](main.py) — lock v APPDATA**
   - `lock_path = Path(os.getenv("LOCALAPPDATA", "C:\\Temp")) / "spot_operator" / f"spot_operator_{safe_user}.lock"`.
   - Create parent directory.
   - Addresses FIND-175.

7. **[spot_operator/bootstrap.py](spot_operator/bootstrap.py) — rozšířit verify**
   - Přidat kontrolu dalších kritických souborů: `autonomy/app/robot/graphnav_navigation.py`, `autonomy/app/image_pipeline.py`, `autonomy/app/models.py`.
   - Addresses FIND-007.

8. **[spot_operator/constants.py](spot_operator/constants.py) — `CAMERA_FRONT_COMPOSITE` v VALID_CAPTURE_SOURCES**
   - Přidat do tuple (nebo odstranit konstantu — rozhodnutí: přidat, je reálně používán v live view).
   - Addresses FIND-005.

9. **[spot_operator/constants.py](spot_operator/constants.py) — TELEOP_SPEED sync**
   - `from app.constants import TELEOP_SPEED_PROFILES as _AUTONOMY_PROFILES`:

     ```python
     TELEOP_SPEED_PROFILES = _AUTONOMY_PROFILES
     ```

   - Nebo assertion při importu `assert TELEOP_SPEED_PROFILES == _AUTONOMY_PROFILES, "drift with autonomy"`.
   - Addresses FIND-006.

10. **[spot_operator/constants.py](spot_operator/constants.py) — `CRUD_WORKER_STOP_TIMEOUT_MS=10000`**
    - Zvýšit na 10s.
    - Addresses FIND-010.

11. **[spot_operator/logging_config.py](spot_operator/logging_config.py) — Qt handler idempotent guard**
    - Module-level `_qt_handler_installed: bool = False`.
    - `_install_qt_handler` skip pokud True.
    - Addresses FIND-008.

12. **[spot_operator/config.py](spot_operator/config.py) — `override=False` dokumentace**
    - Docstring na `load_from_env`: "env vars mají prioritu před `.env` (dotenv override=False)."
    - Addresses FIND-009.

13. **[spot_operator/ui/main_window.py](spot_operator/ui/main_window.py) — bundle liveness via `bundle.is_alive()`**
    - Nová `SpotBundle.is_alive() -> bool` method (RPC ping nebo session flag).
    - `_on_wizard_closed` volá `bundle.is_alive()` místo `getattr hack`.
    - Addresses FIND-155.

14. **[spot_operator/ui/main_window.py](spot_operator/ui/main_window.py) — closeEvent confirm**
    - Pokud bundle is alive: confirm dialog podobný `_disconnect_spot`.
    - Addresses FIND-157.

15. **[spot_operator/ui/main_window.py](spot_operator/ui/main_window.py) — DB ping log dedup**
    - Pattern z `_handle_loop_error` (FIND-117): last_error_key + counter + dedup.
    - Addresses FIND-161.

16. **[spot_operator/ui/common/estop_floating.py](spot_operator/ui/common/estop_floating.py) — removeEventFilter**
    - Override `__del__` nebo `closeEvent`:

      ```python
      def closeEvent(self, event):
          parent = self.parentWidget()
          if parent is not None:
              parent.removeEventFilter(self)
          super().closeEvent(event)
      ```

    - Addresses FIND-163.

### Testy

- `tests/unit/test_bootstrap.py` — rozšířit
  - `test_verify_presence_checks_all_critical_files()`.
  - `test_inject_paths_idempotent()`.

- `tests/unit/test_main_entry.py` — nový
  - `test_python_version_check_fails_on_312()` (mock sys.version_info).

- `tests/unit/test_main_window.py` — rozšířit
  - `test_db_ping_dedup_logging()`.
  - `test_close_event_confirms_when_connected()`.

### Rizika

- Version check breaks Python 3.11/3.12 testing envs. Mitigace: upravit v testech přes monkeypatch.
- Lock path migrace z `temp/` do `APPDATA` → první spuštění po upgrade může mít duplicate lock detection. Mitigace: při startu check obou lokací, odstranit starou.

### Rollback

- Kódová změna. `APPDATA` lock path lze vrátit přes env var.

---

## 13. PR-12 — Map storage + archiver

### Cíl

Map storage je čistě read-only v load path. Export je memory-efficient. Archiver robustní.

### Addresses findings

FIND-047, FIND-049, FIND-050, FIND-051, FIND-052, FIND-053, FIND-054, FIND-055, FIND-056, FIND-057, FIND-059, FIND-060

### Změny v kódu

1. **[spot_operator/services/map_storage.py](spot_operator/services/map_storage.py) — `_validate_loaded_map` bez DB side-effect**
   - Změnit signature: `_validate_loaded_map(map_id, map_dir, meta) -> MapMetadata`.
   - Odstranit `maps_repo.update_validation(...)` uvnitř.
   - Nová separátní funkce `revalidate_map_in_db(map_id) -> None` — explicit call z admin UI / CLI.
   - `load_map_to_temp` vrací immutable meta (bez DB modify).
   - Addresses FIND-047.

2. **[spot_operator/services/zip_exporter.py](spot_operator/services/zip_exporter.py) — streaming export**
   - Místo `buf = io.BytesIO()`:

     ```python
     def build_run_zip(run_id: int, output_path: Path) -> str:
         """Stream-exportuje run do output_path, vrátí suggested filename."""
     ```

   - `ZipFile(output_path, "w", ...)`.
   - Pro každou fotku: `photos_repo.fetch_image_bytes(photo_id)` (separate query místo load-all).
   - Použít `list_for_run_light` + `selectinload(detections)` pro metadata.
   - Stará `build_run_zip(run_id) -> tuple[bytes, str]` deprecated, ale zachovat.
   - Addresses FIND-049.

3. **[spot_operator/services/map_storage.py](spot_operator/services/map_storage.py) — `cleanup_temp_root` lock-aware**
   - V `main.py`: `cleanup_temp_root` volat **až po** `_single_instance_lock`.
   - Mazání restrikovat: jen adresáře vytvořené před lock acquire time (metadata mtime).
   - Addresses FIND-050.

4. **[spot_operator/services/map_storage.py](spot_operator/services/map_storage.py) — `safe_rmtree`**
   - Nový helper:

     ```python
     def safe_rmtree(path: Path, *, retries: int = 3, delay_s: float = 0.5) -> bool:
         for attempt in range(retries):
             try:
                 shutil.rmtree(path)
                 return True
             except OSError:
                 time.sleep(delay_s * (attempt + 1))
         _log.warning("rmtree failed after %d retries: %s", retries, path)
         return False
     ```

   - `map_extracted` context manager + `cleanup` v playback_service → používat.
   - Addresses FIND-051.

5. **[spot_operator/services/map_archiver.py](spot_operator/services/map_archiver.py) — zip whitelist**
   - V `zip_map_dir`:

     ```python
     EXCLUDE_PATTERNS = {".tmp", ".swp", ".lock"}
     files = [p for p in map_dir.rglob("*")
              if p.is_file() and not any(p.name.endswith(ext) or p.name.startswith('.') for ext in EXCLUDE_PATTERNS)]
     ```

   - Addresses FIND-052.

6. **[spot_operator/services/map_archiver.py](spot_operator/services/map_archiver.py) — extract velkých pro >100MB**
   - Pokud `len(data) > 100 * 1024 * 1024`: write to temp file, open ZipFile from path.

     ```python
     if len(data) > 100 * 1024 * 1024:
         with tempfile.NamedTemporaryFile(delete=False) as f:
             f.write(data); f.flush()
             # use f.name as archive path
     ```

   - Addresses FIND-053.

7. **[spot_operator/services/map_archiver.py](spot_operator/services/map_archiver.py) — bosdyn import top**
   - `from bosdyn.api.graph_nav import map_pb2` na úrovni module (s lazy pattern pro testy: `try: ... except ImportError: map_pb2 = None`).
   - Runtime check: pokud `map_pb2 is None` raise "bosdyn not available".
   - Addresses FIND-054.

8. **[spot_operator/services/map_archiver.py](spot_operator/services/map_archiver.py) — odstranit `count_waypoints_in_map_dir`**
   - Dead code (FIND-055). Pokud je to nepoužité, odstranit.
   - Addresses FIND-055.

9. **[spot_operator/services/map_storage.py](spot_operator/services/map_storage.py) — `MapMetadata.default_capture_sources: tuple`**
   - Změnit typ na `tuple[str, ...]`. `_to_metadata` konverze `tuple(...)`.
   - Addresses FIND-056.

10. **[spot_operator/db/repositories/maps_repo.py](spot_operator/db/repositories/maps_repo.py) — remove `list_all`**
    - Přímo přesměrovat na `map_storage.list_all_metadata` (používá `defer`).
    - Nebo `list_all` → deprecated, warning log + delegate.
    - Addresses FIND-057.

11. **[spot_operator/services/zip_exporter.py](spot_operator/services/zip_exporter.py) — `_safe_name` s id suffix**
    - Už má photo_id v filename → unique. Jen zajistit, že `{base}.json` je unique přes `__{photo_id}` suffix.

      ```python
      base = f"{safe_cp}__{safe_src}__{photo.id}"
      # json too: f"{base}.json"
      ```

    - Addresses FIND-059.

12. **[spot_operator/services/zip_exporter.py](spot_operator/services/zip_exporter.py) — odstranit defensive `or []`**
    - `run.checkpoint_results_json` je teď invariantně list per DB schema → `list(run.checkpoint_results_json)` bez `or []`.
    - Addresses FIND-060.

### Testy

- `tests/unit/test_map_archiver.py` — rozšířit
  - `test_zip_excludes_tmp_files()`.
  - `test_large_extract_uses_temp_file()` (mock > 100MB via fixture).
  - `test_safe_rmtree_retries_on_permission_error()`.

- `tests/unit/test_zip_exporter.py` — nový
  - `test_stream_export_to_file()`.
  - `test_unique_photo_filenames_even_on_long_names()`.

### Rizika

- Streaming export s file handle — crash mid-write zanechá partial ZIP. Mitigace: write to temp, atomic rename at end.

### Rollback

- Kódová změna.

---

## 14. PR-13 — Autonomy contract hardening

### Cíl

Kontrakt mezi `spot_operator` a `autonomy` je explicit přes Protocols. Mismatch se projeví staticky.

### Addresses findings

FIND-065, FIND-069, FIND-176, FIND-177, FIND-178, FIND-179, FIND-182

### Změny v kódu

1. **[spot_operator/robot/contracts.py](spot_operator/robot/contracts.py) — nový modul**
   - Protocol classes:

     ```python
     class NavigationResultProtocol(Protocol):
         outcome: NavigationOutcome  # enum from app.models
         message: str
         ok: bool
         is_localization_loss: bool

     class ImagePollerProtocol(Protocol):
         def capture(self, source: str) -> np.ndarray | None: ...
         def list_sources(self) -> list[str]: ...

     class SessionProtocol(Protocol):
         hostname: str
         robot: Any
         graph_nav_client: Any
         def disconnect(self) -> None: ...
     ```

   - Addresses FIND-177, FIND-178.

2. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — use contracts**
   - `self._navigator: NavigationResultProtocol` atd.
   - Addresses FIND-177.

3. **[spot_operator/services/playback_service.py](spot_operator/services/playback_service.py) — NavigationOutcome unknown warning**
   - V `_should_retry_outcome`: pokud `result.outcome not in known_values: _log.warning(...)`.
   - Addresses FIND-176.

4. **[spot_operator/robot/power_state.py](spot_operator/robot/power_state.py) — `is_motors_powered -> Optional[bool]`**
   - Změnit:

     ```python
     def is_motors_powered(bundle: Any) -> bool | None:
         # None = unknown (RPC failed), True/False = known state
     ```

   - Aktualizovat callsites (FiducialPage atd.).
   - Addresses FIND-065.

5. **[spot_operator/robot/session_factory.py](spot_operator/robot/session_factory.py) — odstranit dead flags**
   - `connect_partial(..., with_lease=True, with_estop=True)` — odstranit parametry (dead flexibility).
   - Jen `connect(hostname, username, password) -> SpotBundle`.
   - Addresses FIND-069.

6. **[spot_operator/robot/session_factory.py](spot_operator/robot/session_factory.py) — `get_info` API (z PR-09)**
   - `SessionProtocol.get_hostname() -> str | None` method definovat.
   - Pokud autonomy nemá, spot_operator wrapper.
   - Addresses FIND-182.

7. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — `read_observed_fiducial_ids` wrapper**
   - Nový `spot_operator/robot/graphnav_fiducial.py` wrapper:

     ```python
     def read_observed_fiducial_ids(map_dir: Path) -> list[int]:
         """Wrapper přes autonomy internal API. Izoluje importy."""
         try:
             from app.robot.graphnav_recording import read_observed_fiducial_ids as _impl
         except ImportError as exc:
             raise RuntimeError("autonomy read_observed_fiducial_ids not available") from exc
         return list(_impl(map_dir))
     ```

   - `recording_service` importuje z lokálního wrapperu.
   - Addresses FIND-179.

### Testy

- `tests/unit/test_protocols.py` — nový
  - `isinstance`-style struct checks (může použít `typing_extensions.runtime_checkable`).

- `tests/integration/test_autonomy_smoke.py` — rozšířit
  - Smoke test, že Protocol matches actual autonomy types.

### Rizika

- Protocol nejsou runtime validated bez `@runtime_checkable`. Ale static type check je enough.

### Rollback

- Pouze nové kód (wrapper, Protocols). Starý kód zůstává funkční.

---

## 15. PR-14 — Tests

### Cíl

End-to-end pokrytí flows, které nejsou pokryty v dřívějších PR. Prio A+B ze §5 code_review.

### Addresses findings

FIND-183, FIND-184, FIND-185, FIND-186, FIND-187 (mimo robot), FIND-188, FIND-189

### Změny v kódu

1. **[tests/unit/test_recording_flow_e2e.py](tests/unit/)** — nový
   - Full recording flow s mock GraphNavRecorder, ImagePoller, WaypointNamer.
   - 5+ test cases (start_waypoint semantika, demotion, fiducial fallback).
   - Addresses FIND-183.

2. **[tests/unit/test_playback_flow_e2e.py](tests/unit/)** — nový
   - Full playback s mock GraphNavNavigator.
   - 10+ test cases pro různé NavigationOutcome kombinace.
   - Addresses FIND-184.

3. **[tests/unit/test_map_contracts_roundtrip.py](tests/unit/)** — nový
   - build → parse → build equal test.
   - Edge cases: prázdný, velký, Unicode names.
   - Addresses FIND-185.

4. **[tests/unit/test_ocr_worker_flow.py](tests/unit/)** — nový (navazuje na PR-06)
   - Claim → process → commit.
   - Scenáře: success, DB error, pipeline raise, permanent error.
   - Addresses FIND-186.

5. **[tests/unit/test_estop_safety.py](tests/unit/)** — nový
   - `EstopFloating` bez on_release → TypeError (už PR-01).
   - `trigger_estop` + bundle None → emit signal.
   - Addresses FIND-189.

6. **[tests/unit/test_validate_sources_known.py](tests/unit/)** — rozšířit existující (test_map_contracts.py)
   - Empty `available_sources`, None input.
   - Addresses FIND-188.

### Rizika

- Test suite runtime roste. Mitigace: pytest marker pro rychlé/pomalé testy.

---

## 16. PR-15 — Dokumentace + tech debt cleanup

### Cíl

Dokumentace odpovídá kódu. Dead code pryč. Magic strings → enumy.

### Addresses findings

FIND-030, FIND-079, FIND-080, FIND-081, FIND-082 (už PR-02), FIND-084, FIND-138, FIND-140 (už PR-04), FIND-145 (už PR-08), FIND-147, FIND-155 (už PR-11), FIND-166, FIND-168, FIND-170, FIND-190, FIND-191, FIND-192, FIND-193, FIND-194, FIND-195

### Změny v kódu

1. **[spot_operator/services/contracts.py](spot_operator/services/contracts.py) — `CaptureNote` enum**
   - Nová enum:

     ```python
     class CaptureNote(str, Enum):
         OK = ""
         CAPTURE_FAILED = "capture_failed"
         CAPTURE_PARTIAL = "capture_partial"
     ```

   - Použít napříč. Addresses FIND-079.

2. **[spot_operator/services/photo_sink.py](spot_operator/services/photo_sink.py) — shape validace**
   - `encode_bgr_to_jpeg`:

     ```python
     if image_bgr.ndim == 2:
         rgb = image_bgr
         pil_mode = 'L'
     elif image_bgr.ndim == 3 and image_bgr.shape[2] == 3:
         rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
         pil_mode = 'RGB'
     else:
         raise ValueError(f"Unsupported image shape: {image_bgr.shape}")
     ```

   - Addresses FIND-080, FIND-081.

3. **[spot_operator/services/recording_service.py](spot_operator/services/recording_service.py) — `photo_count` cache**
   - V `capture_and_record_checkpoint`: `self._photo_count_cache += len(photos)`. Invalidate v abort.
   - Property vrací cache.
   - Addresses FIND-084.

4. **[spot_operator/ui/wizards/pages/fiducial_page.py](spot_operator/ui/wizards/pages/fiducial_page.py) — refactor split**
   - Extract common logic do `FiducialPageBase(QWizardPage)`.
   - 3 subclass: `RecordingFiducialPage`, `PlaybackFiducialPage`, `WalkFiducialPage`.
   - Vstupní argument `required_id` → subclass-specific (PlaybackFiducialPage pouze).
   - Velká změna, ale mechanical.
   - Addresses FIND-138.

5. **[spot_operator/ui/wizards/pages/save_map_page.py](spot_operator/ui/wizards/pages/save_map_page.py) — fiducial warn save**
   - Pokud `_fiducial_status` shows failure: na `_start_save` confirm dialog "Fiducial ověřený, uložit i tak?".
   - Addresses FIND-147.

6. **[spot_operator/ui/crud/*.py](spot_operator/ui/crud/) — batch operations status**
   - Místo info_dialog: `status_label.setText(f"Resetováno {count} fotek")`. Logger stále loguje.
   - Addresses FIND-166.

7. **[spot_operator/ui/crud/photos_tab.py](spot_operator/ui/crud/photos_tab.py) — auto-refresh**
   - Connect `OcrWorker.photo_processed` (přes main_window passování) → throttled `_model.reset()` (QTimer 1s).
   - Addresses FIND-168.

8. **[spot_operator/ui/common/table_models/](spot_operator/ui/common/table_models/) — local timestamp**
   - Helper `format_local_datetime(dt: datetime | None) -> str`:

     ```python
     return dt.astimezone().strftime("%d. %m. %Y %H:%M") if dt else ""
     ```

   - Použít v `photos_model.cell`, `runs_model.cell`, `plates_model.cell`.
   - Addresses FIND-170.

9. **[spot_operator/db/repositories/photos_repo.py](spot_operator/db/repositories/photos_repo.py) — `_to_photo_row` bez UI fallback**
   - Už součást PR-07, ale pokud jsme nestihli, doplnit zde.
   - Addresses FIND-030.

10. **[instructions.md](instructions.md) — Recording protocol section**
    - Nová sekce "Recording protocol":
      > Operátor **MUSÍ** stisknout Waypoint (C) jako první krok u startovního fiducialu. Toto pojmenuje startovní bod mapy. Až následně může fotit (V/N/B).

    - Addresses FIND-190, FIND-191.

11. **[instructions.md](instructions.md) — glossary update**
    - "Checkpoint" definice: "Waypoint s **alespoň jedním successfully saved photo**."
    - "Waypoint demoted checkpoint" = pouze pokud capture failed explicit user choice.
    - Addresses FIND-190.

12. **[instructions.md](instructions.md) — UI flow**
    - Přidat krok-po-kroku recording/playback protokol pro operátora.
    - Addresses FIND-192.

13. **[.env.example](.env.example) — `OCR_YOLO_MODEL` cesta absolute tolerance**
    - Dokumentace: "absolutní cesta přepisuje `ROOT`". Pokud user zadá `C:\models\...`, config detectuje `is_absolute()` a použije as-is.

      ```python
      p = Path(ocr_yolo_model_rel)
      ocr_yolo_model_path = p if p.is_absolute() else ROOT / p
      ```

    - Addresses FIND-193.

14. **[spot_operator/__init__.py](spot_operator/__init__.py) — version sync**
    - `__version__` kontrola proti `instructions.md:2` (version: 1.3.0). Buď update `__version__` nebo dokumentace.
    - Addresses FIND-194.

15. **[README.md](README.md) — keyboard shortcuts audit**
    - Cross-check proti `teleop_record_page.py._hint` a aktualizovat.
    - Addresses FIND-195.

16. **Dead code removal**
    - `spot_operator/services/map_archiver.count_waypoints_in_map_dir` (už PR-12).
    - `spot_operator/services/playback_service._extract_checkpoints` dead filter (už PR-05).
    - `playback_service._localize_with_fallback` duplicate if (už PR-05).
    - `teleop_record_page.recording_finished` signal (už PR-09).

### Testy

- `tests/unit/test_format_timestamp.py` — nový
  - Local formatter test s fixní tz.

- `tests/unit/test_photo_sink_shape.py` — nový
  - Grayscale / 3-channel / 4-channel handling.

### Rizika

- `FiducialPage` refactor (subclass) — velká změna, mnoho callsitů. Mitigace: postupně, jeden subclass naráz.

### Rollback

- Dokumentace + dead code bez issue.

---

## 17. Graf závislostí

```
PR-01 (Safety) ──┬───→ PR-03 (Map invariants)
                 ├───→ PR-05 (Playback)
                 └───→ PR-11 (Bootstrap)

PR-02 (Recording) ───→ PR-04 (Two-phase save)

PR-03 (Contracts) ──┬──→ PR-04
                    ├──→ PR-05
                    └──→ PR-09 (Protocol)

PR-05 (Playback) ───→ PR-07 (DB hardening) [checkpoint results]

PR-06 (OCR) ───→ PR-07 [sweep heartbeat]

PR-07 (DB) ──┬──→ PR-08 (Qt lifecycle)
             └──→ PR-09 (Wizard types)

PR-08 (Qt) ──→ PR-15 (Tech debt UI)

PR-09 (Types) ──→ PR-13 (Autonomy contracts)

PR-10 (Credentials) — nezávislé

PR-11 (Bootstrap) — nezávislé (ale po PR-01)

PR-12 (Map storage) — nezávislé

PR-13 (Autonomy) ──→ PR-14 (Tests)

PR-14 (Tests) — závisí na všech předchozích

PR-15 (Tech debt + docs) — poslední
```

**Kritická cesta (ASAP merge order):**

1. PR-01 (safety)
2. PR-02 (root cause UX)
3. PR-03 (invariants)
4. PR-04 (save retry)
5. PR-05 (playback)
6. PR-06 (OCR worker)
7. Paralelně: PR-07, PR-10, PR-11, PR-12
8. PR-08 (Qt lifecycle)
9. PR-09 (types)
10. PR-13 (autonomy)
11. PR-14 (tests)
12. PR-15 (tech debt + docs)

---

## 18. Test coverage goal

Po dokončení všech PR cíl:

- **Line coverage ≥ 70 %** v `spot_operator/services/`, `spot_operator/db/`, `spot_operator/ui/wizards/`.
- **Branch coverage ≥ 50 %**.
- 100 % pokrytí v `contracts.py`, `session_factory.py` (error paths).

**Měření:** `pytest --cov=spot_operator --cov-report=html`. Coverage report součást PR.

---

## 19. Postup implementace

1. **Review tento plán** s uživatelem, získat schválení.
2. **Založit tracking issue** (Markdown nebo Linear) s checklistem PR-01 až PR-15.
3. **Implementovat PR po PR** dle kritické cesty (§17). Pokud PR je moc velký, rozdělit ještě.
4. **Po každém PR:**
   - Unit testy projdou lokálně (`pytest -xvs`).
   - Type check (`mypy --strict spot_operator/` — může být incremental).
   - Lokální smoke run `python main.py --diag` (bez robota).
   - Update CHANGELOG.md.
5. **Po merge posledního PR:**
   - Full test run.
   - Dokumentace final check.
   - **[robot-test]** Live ověření s reálným robotem (user provede po dodání):
     - Recording → playback happy path (ověřit FIND-072 fix).
     - Capture failure retry dialog (simulovat přes firewall block).
     - E-Stop safety cycle.
     - Lease keepalive >60s idle.
     - Disconnect timeout.

---

## 20. Co plán NEŘEŠÍ

- **Performance tuning** (OCR latence, DB indexing) — odloženo.
- **Reporting / analytics** dashboard — mimo scope.
- **Multi-robot orchestration** — single Spot assumption.
- **Internationalization** (jen CZ texty) — uživatel preferuje.
- **Web UI / REST API** — desktop app stays.
- **Automated testing with real bosdyn SDK** — bez robota nemožné.

---

## 21. Riziko celého plánu

| Riziko | Pravděpodobnost | Dopad | Mitigace |
|---|---|---|---|
| PR-02 breaks UX, operátoři se nedovedou adaptovat | Střední | Vysoký | Subtitle/status_label hint + CHANGELOG note |
| PR-05 PR-07 kompatibilita s existujícími runs v DB | Střední | Střední | Parse tolerance (PR-03 FIND-042) |
| Protocols v PR-09 nefungují v Qt metaclass | Nízká | Střední | Fallback na `Any` + runtime isinstance |
| Migrace 0003 (NULLS NOT DISTINCT) neaplikovatelná na starší PG | Nízká | Nízký | Version check v migraci |
| Testy bez robota nepokryjí bugs, které se projeví jen live | Vysoká | Vysoký | Explicit [robot-test] flag; uživatel ověří po dodání |

---

## 22. Odhad rozsahu

**Pesimistický odhad** per PR:

| PR | Velikost | Kompexity |
|---|---|---|
| PR-01 | 200 lines diff | Malá |
| PR-02 | 400 lines | Střední (UX) |
| PR-03 | 300 lines | Malá |
| PR-04 | 500 lines | Střední |
| PR-05 | 600 lines | Velká |
| PR-06 | 500 lines | Střední |
| PR-07 | 700 lines | Velká (DB migrace) |
| PR-08 | 400 lines | Střední |
| PR-09 | 500 lines | Velká (generics) |
| PR-10 | 400 lines | Malá |
| PR-11 | 300 lines | Malá |
| PR-12 | 400 lines | Střední |
| PR-13 | 200 lines | Malá |
| PR-14 | 1000+ lines (tests) | Velká |
| PR-15 | 500 lines | Střední (refactor) |
| **Celkem** | ~7000 lines diff | |

S 196 findings rozprostřenými přes 15 PR a test coverage, jde o 2-4 týdny fokusované práce (pokud se neřeší nic jiného).

---

## 23. Závěr

Plán adresuje **všech 196 findings** z `code_review.md`. Po dokončení by aplikace měla:

- Být **safer** (E-Stop, lease, power sekvence correct).
- Mít **root cause fix** pro "robot jede náhodně" a "chybějící data".
- Mít **retry-able save** flow.
- Mít **robustní OCR worker** s explicit permanent-error handling.
- Mít **typed wizard state** bez `# type: ignore`.
- Mít **test coverage >70 %** v kritických vrstvách.
- Mít **aktualizovanou dokumentaci** odpovídající kódu.

**Bez robota** je implementováno/testovatelno všech 196 findings kromě 4 odložených (FIND-064, FIND-070, FIND-180, FIND-187) — ty jsou explicit označené **[robot-test]** a ověří se po dodání.

Po schválení tohoto plánu bych začal PR-01 (safety stoppers) jako první, protože je samostatný a má nejvyšší ROI (safety + malá velikost + rychlý review).
