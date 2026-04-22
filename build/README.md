# Bundler — tři nezávislé .exe

Tato složka obsahuje PyInstaller spec soubory a runtime hooky, které z projektu
`spot` vytvoří tři **plně samostatné** .exe:

| .exe            | Zdroj                | Typ                 | Použití                                    |
| --------------- | -------------------- | ------------------- | ------------------------------------------ |
| `autonomy.exe`  | `autonomy/main.py`   | PySide6 GUI         | Boston Dynamics ovládání + GraphNav        |
| `ocr.exe`       | `ocr/ocrtest.py`     | CLI (console)       | Batch OCR českých SPZ (YOLO + Nomeroff)    |
| `spot.exe`      | `main.py` (root)     | PySide6 GUI         | Kompletní Spot Operator (DB + autonomy+OCR)|

Všechny tři jsou **neinvazivní** — neupravuje se žádný existující zdrojový kód,
veškeré runtime patche jsou v `build/runtime_hooks/`.

## Rychlý start

Z root projektu (`c:\Users\zige\spot`) **dva kroky**:

```bat
setup_venv.bat                  REM jednorazovy setup venv + vsechny deps (root)
build_all.bat             REM vlastni build
```

Můžeš stavět i jednotlivě:

```bat
build_all.bat autonomy    REM jen autonomy.exe
build_all.bat ocr         REM jen ocr.exe
build_all.bat spot        REM jen spot.exe
```

Výstupy:

```text
dist\autonomy\autonomy.exe      (~180 MB)
dist\ocr\ocr.exe                (~250 MB)
dist\spot\spot.exe              (~450 MB)
```

Každá `dist\<jmeno>\` složka je portable — zkopírujte ji na cílový stroj bez Pythonu.

## Co dělá root `setup_venv.bat`

1. Ověří Python 3.10 (py launcher).
2. Vytvoří `.venv` v rootu (pokud ještě neexistuje).
3. Nainstaluje `requirements.txt` + `nomeroff_net` (ocrtest.py) + `pyinstaller>=6.0` (bundler).

Neřeší `autonomy\.venv` — ten je nezávislý pro dev práci na autonomy samotném
a pro bundler se nepoužívá. Bundler vždy pracuje jen s root `.venv`.

## Předpoklady

- **Python 3.10** vedle Pythonu 3.12 (vyžaduje bosdyn SDK). Je-li potřeba nainstalovat,
  `setup_venv.bat` hlásí instrukci.
- Internet připojení při prvním setupu (pip install).

## Jak to funguje

### Runtime hooky ( `build/runtime_hooks/` )

PyInstaller bootloader spouští runtime hook **před** uživatelským `main()`, takže
lze monkey-patchovat modul-level konstanty bez zásahu do zdrojáků.

- **`rh_autonomy_paths.py`** — přepíše `autonomy/app/config.py:BASE_DIR` z `_MEIPASS`
  na složku vedle `autonomy.exe`, takže `maps/`, `runs/`, `exports/`, `logs/` se
  vytváří portably.
- **`rh_ocr_paths.py`** — zkopíruje YOLO + ONNX modely z `_MEIPASS` vedle
  `ocr.exe` a nastaví CWD, aby `ocrtest.py` našel `./license-plate-finetune-v1m.pt`
  a uživatelskou `./test/` složku.
- **`rh_spot_paths.py`** — injektuje `_MEIPASS/autonomy` a `_MEIPASS/ocr` na
  `sys.path` (analog `spot_operator.bootstrap.inject_paths`), přepíše
  `spot_operator.constants.LOGS_DIR` a `TEMP_ROOT` na exe_dir, změní CWD na
  `_MEIPASS` kvůli `alembic.ini`.

### Runtime data (portable mode)

Data adresáře (`maps/`, `runs/`, `exports/`, `logs/`, `temp/`) se vytváří
vedle .exe při startu (runtime hook přesměruje BASE_DIR).

**`.env` se NEROZBALUJE vedle .exe.** Soubor zůstává uvnitř bundlu
(`_internal/.env`), aplikace ho čte přímo odtud. Pokud chceš runtime override
(jiné credentials, DB URL, model path bez rebuildu), vytvoř `.env` vedle .exe
ručně — má prioritu nad bundled verzí.

**OCR modely**:

- **spot.exe**: model zůstává v bundlu, cesta přes env proměnnou `OCR_YOLO_MODEL`
  (absolute path do `_internal/ocr/`). Nekopíruje se.
- **ocr.exe**: model **se kopíruje** (first-run) vedle `ocr.exe`, protože
  `ocrtest.py` má hardcoded relativní cesty (`./license-plate-finetune-v1m.pt`,
  `./test/`). Neinvazivní patch přes env/sys.modules by vyžadoval víc než
  monkey-patch CWD. Model je modelem (ne credentials), takže rozbalení tady
  neznamená bezpečnostní problém. Uživatel dává vstupní `test/` složku vedle
  ocr.exe.

### Ikony, data, test obrázky

- **autonomy.exe**: žádné ikony (resources složka je prázdná).
- **ocr.exe**: uživatel dá `test/` složku s obrázky SPZ vedle .exe — `ocrtest.py`
  ji iteruje (je hardcoded na `./test`, nebere CLI argumenty).
- **spot.exe**: ikony se zatím neřeší (resources prázdné), `alembic/` je uvnitř bundlu.

## Známé problémy

1. **Antivirus false positive** — PyInstaller .exe občas spustí Windows Defender.
   Pro distribuci třetí straně zvažte code signing (mimo scope tohoto bundleru).
2. **`nomeroff_net` stahuje modely při prvním runtime** — volá do `~/.nomeroff_net/`.
   První spuštění vyžaduje internet nebo pre-deploy cache. Neřešeno.
3. **`.env` s credentials v bundlu** — `autonomy/.env` obsahuje SPOT_USERNAME/PASSWORD.
   Pro veřejnou distribuci zkontrolujte obsah před buildem.
4. **Velikost `spot.exe` (~450 MB)** — důsledek `torch + ultralytics + bosdyn + PySide6`
   v jednom bundlu. UPX komprese lze zapnout nastavením `upx=True` v specu.

## Debugging build

```bat
REM verbose output, necistit stare build artefakty
pyinstaller --log-level=DEBUG --noconfirm ^
  --workpath build\_pyinstaller --distpath dist ^
  build\specs\autonomy.spec > build\_logs\autonomy.log 2>&1
```

Chybějící moduly přidávejte do `hiddenimports` v příslušném `.spec` souboru.
