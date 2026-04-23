# Spot Operator

Desktop aplikace pro operátora parkoviště, která **sjednocuje nahrávání map**,
**autonomní průjezd s focením SPZ** a **CRUD registr SPZ** — všechno proti
jedné PostgreSQL databázi.

Projekt sedí na dvou existujících podprojektech, které zůstávají samostatně
spustitelné:

- `autonomy/` — Boston Dynamics Spot SDK + GraphNav + live view
- `ocr/` — YOLO detektor + OCR pro české SPZ

---

## 1. Co aplikace umí

- **Nahrát novou mapu** parkoviště přes user-friendly wizard: Wi-Fi → login →
  volba strany focení → check fiducialu → teleop WASD + tlačítka na fotky +
  waypointy → uložit celou mapu jako ZIP do PostgreSQL.
- **Spustit autonomní jízdu** podle mapy v DB: vybrat mapu → check
  startovacího fiducialu → START → Spot projede checkpointy, na každém vyfotí
  SPZ (z jedné nebo obou stran). **E-STOP** a **Stop s návratem domů**
  tlačítka vždy dostupná.
- **Automatické OCR** — každá fotka se po uložení do DB vezme OCR workerem,
  detekují se SPZ YOLO + fast-plate-ocr, výsledky se zapíší do DB s confidence
  scórem.
- **Export ZIP** — všechny fotky + metadata vybraného běhu v jednom souboru.
- **CRUD** pro dev/admin: registr povolených SPZ, seznam běhů, galerie fotek,
  re-OCR lepším enginem (Nomeroff fallback v subprocesu). **Celá CRUD složka
  je fyzicky odstranitelná** — v produkci bude tuto agendu řešit jiný systém.

Všechna perzistovaná data (mapy, fotky, detekce, registry) jsou v PostgreSQL.
Mapy jako ZIP v `BYTEA`, fotky jako JPEG v `BYTEA`. Na disku je jen `logs/` a
dočasné `temp/` složky při extrahování map pro playback.

---

## 2. Požadavky

- **Windows 10/11** (testováno na 11)
- **Python 3.10.20 x64** (nutno doinstalovat, Boston Dynamics Spot SDK ho vyžaduje)
<https://github.com/adang1345/PythonWindows/blob/master/3.10.20/python-3.10.20-amd64-full.exe>
- **PostgreSQL 14+** lokálně nebo vzdáleně
- **Boston Dynamics Spot** s aktivní GraphNav licencí
- **AprilTag fiducial** na startu trasy (obvykle u nabíječky)
- Síťové spojení: operátorský PC musí být na Wi-Fi Spota

---

## 3. Proč Python 3.10 a ne 3.12

Boston Dynamics Spot Python SDK oficiálně podporuje Python **3.7–3.10**. Pokud
už máš Python 3.12 v systému, nevadí, **necháme ho tam** a doinstalujeme
Python 3.10 vedle něj. Projekt poběží v `.venv` nad Pythonem 3.10.

---

## 4. Instalace Pythonu 3.10 na Windows vedle 3.12

1. Stáhni oficiální installer: [python.org/downloads/release/python-3100/](https://www.python.org/downloads/release/python-3100/) (Windows installer 64-bit).
2. Při instalaci **zaškrtni "py launcher"** a "Add Python to PATH".
3. Ověř v `cmd`:

```bat
py -3.10 --version
```

Musí vypsat `Python 3.10.x`.

---

## 5. Jak vytvořit a aktivovat venv

```bat
cd c:\Users\zige\spot
setup_venv.bat
```

Skript:

1. Ověří, že je dostupný `py -3.10`.
2. Vytvoří `.venv/` s Pythonem 3.10.
3. Nainstaluje všechny závislosti z `requirements.txt` (PySide6, SQLAlchemy,
   Alembic, bosdyn-*, ultralytics, onnxruntime, fast-plate-ocr, keyring, ...).

První instalace může trvat několik minut (torch + ultralytics jsou velké).

---

## 6. Jak venv opustit

```bat
deactivate
```

---

## 7. Jak nastavit připojení ke Spotovi

Aplikace si pamatuje profily v **Windows Credential Locker** (Keyring).
V kroku "Přihlášení" v každém wizardu:

1. Zadej **IP adresu** Spota (default `192.168.80.3`).
2. Zadej **uživatele** a **heslo**.
3. Pojmenuj profil (`lab-robot`, `spot-demo`, ...).
4. Zaškrtni **Zapamatovat** → heslo uloženo šifrovaně v OS trezoru.

Další spuštění stačí vybrat profil z dropdownu.

---

## 8. Jak spustit aplikaci

```bat
cd c:\Users\zige\spot
launch.bat
```

`launch.bat` udělá setup (pokud `.venv` chybí) a spustí aplikaci. V hlavním okně
jsou 3 velká tlačítka:

- **Spustit jízdu podle mapy** (playback wizard)
- **Nahrát novou mapu** (recording wizard)
- **Správa SPZ a běhů** (CRUD — zobrazí se jen pokud je složka `spot_operator/ui/crud/` nainstalovaná)

---

## 9. Ovládání klávesnicí

Klávesy jsou aktivní jen v kroku **teleop + focení** v nahrávacím wizardu:

| Klávesa | Akce |
|---|---|
| `W` / `S` | dopředu / dozadu |
| `A` / `D` | strafe vlevo / vpravo |
| `Q` / `E` | rotace vlevo / vpravo |
| `Space` | soft stop (zastaví velocity command) |
| `V` | foto z levé kamery (preview → potvrdit / zrušit) |
| `N` | foto z pravé kamery (preview → potvrdit / zrušit) |
| `B` | foto z obou kamer (preview obou → potvrdit / zrušit) |
| `C` | přidat waypoint (bez fotky) |
| **F1** | **E-STOP — hardwarový nouzový stop** |

V playback wizardu je aktivní jen **F1** (E-STOP) — tam je autonomní režim.

---

## 10. Jak nahrát mapu a checkpointy

1. Klikni **Nahrát novou mapu** v hlavním okně.
2. **Krok 1 — Wi-Fi**: připoj PC k Wi-Fi Spota (SSID typicky `spot-BD-XXXXXXXX`), pak klikni Zkontrolovat.
3. **Krok 2 — Přihlášení**: vyber profil nebo zadej údaje a klikni Připojit.
4. **Krok 3 — Strana focení**: vyber Levá / Pravá / Obě strany.
5. **Krok 4 — Fiducial**: postav Spota 1–2 m před fiducial u nabíječky, klikni Zkontrolovat.
6. **Krok 5 — Teleop**: nahrávání se spustilo automaticky. Projeď Spotem parkoviště, u každého auta stiskni `V` / `N` / `B` pro fotku z levé / pravé / obou kamer. **Zobrazí se náhled** — zkontroluj, že SPZ je vidět, a potvrď "✓ Vyfotit a uložit" (nebo zruš a uprav pozici Spota). Klávesa `C` přidá waypoint bez fotky. Vrať se k fiducialu.
7. **Krok 6 — Uložit**: znovu ověř fiducial, zadej jméno mapy (např. `parkoviste_sever_2026`), klikni Uložit mapu.

Mapa se zabalí do ZIPu a uloží se **celá do PostgreSQL**. Můžeš ji pak spustit z libovolného PC proti stejné DB.

---

## 11. Jak spustit autonomní průchod

1. Klikni **Spustit jízdu podle mapy** v hlavním okně.
2. Wi-Fi + přihlášení jako u nahrávání.
3. **Výběr mapy**: z listu vyber mapu, vidíš náhled metadat.
4. **Fiducial check**: postav Spota před fiducial uložený v mapě (ID musí odpovídat — wizard to zkontroluje).
5. **START**: Spot si uploadne mapu, lokalizuje se, projíždí checkpointy a na každém fotí.
6. Během jízdy vidíš live view, progress a log událostí. Můžeš použít:
    - **F1 nebo červený E-STOP button** — hardwarový nouzový stop.
    - **Žluté "STOP s návratem domů"** — přeruší běh a Spot se autonomně vrátí k fiducialu.
7. **Výsledek**: tabulka přečtených SPZ + možnost stáhnout ZIP.

---

## 12. Jak exportovat ZIP

Automaticky po dokončení jízdy na stránce výsledků (**Stáhnout ZIP**), nebo
kdykoli později v **CRUD → Běhy → Exportovat ZIP vybraného běhu**.

ZIP obsahuje:

- `run.json` — metadata běhu + seznam fotek + detekce
- `photos/*.jpg` — samotné JPEGy
- `photos/*.json` — detekce na dané fotce (jeden záznam na engine)

---

## 13. Známá omezení

- **Jeden OCR worker** běží paralelně s aplikací. Při velkém množství fotek se OCR fronty kupí, ale fotky to neblokuje — všechny se zachytí, OCR je doplní v klidu. Aplikace si při startu uklidí zaseklé "processing" záznamy.
- **Wi-Fi ztráta mid-recording** = konec mapy. Wizard to detekuje a nabídne zrušit. Není pause/resume.
- **Return home** vyžaduje stále-validní lokalizaci. Pokud se Spot úplně ztratí, vrátit se sám nedokáže; použij E-STOP a dojdi k němu fyzicky.
- **Nomeroff fallback** běží v subprocesu (kvůli izolaci torch/protobuf) — má cold-start ~3–5 s, takže Re-OCR v CRUD není instantní.
- **Mapy v `BYTEA`** — typicky 1–20 MB. Pro velké areály (> 100 MB) zvaž externí storage.
- **CRUD modul je dev-only** — v prod ho smaž (`spot_operator/ui/crud/`), tlačítko zmizí.

---

## 14. Bezpečnostní poznámky

- Spot se smí ovládat **jen v bezpečném prostoru**, dál od lidí a aut.
- Před pohybem musí být **validní lease a bezpečnostní režim** (aplikace je ověří při login kroku).
- Aplikace **není náhrada za fyzické bezpečnostní postupy**. Operátor musí vidět Spota nebo mít druhou osobu, která ho pozoruje.
- Autonomní průchod **vyžaduje předem nahranou validní GraphNav mapu**. Pokud se prostředí výrazně změnilo (přestavbou, novými překážkami), mapa nemusí být použitelná.
- Při **ztrátě lokalizace** aplikace autonomní jízdu zastaví a zapíše `status=aborted`.
- **E-STOP je nadřazený všemu** — po stisku se motory okamžitě odpojí, je nutné restartovat power on + stand.

---

## 15. Troubleshooting

### Python 3.12 problém

Pokud tě `python main.py` spustí s Pythonem 3.12, venv je špatně:

```bat
.venv\Scripts\python.exe --version
```

Musí vypsat `3.10.x`. Pokud ne, smaž `.venv` a spusť znovu `setup_venv.bat`.

### Venv aktivace

```bat
call .venv\Scripts\activate
```

### Ukončení venv

```bat
deactivate
```

### Robot se nepřipojí

Zkontroluj:

- IP adresu (default `192.168.80.3`)
- síťové spojení (PC musí být na Wi-Fi Spota)
- uživatele + heslo (Spot má výchozí `admin`/heslo dle dokumentace)
- že běží potřebné služby (`recording`, `graph-nav`, `estop`) — aplikace je vypíše v logu
- že máš **aktivní GraphNav licenci** na robotovi (jinak chybí recording i navigation klient)

### Není obraz / live view prázdný

- Aplikace použije default `frontleft_fisheye_image`. Pokud ten source neexistuje, v logu je warning.
- Zkontroluj, že `ImageClient` je dostupný (v logu `[image] ensure_client`).

### Databáze nedostupná

```
FATAL: Databáze není dostupná
```

- Ověř `DATABASE_URL` v `.env`
- Ověř že PostgreSQL běží a uživatel má `CREATE TABLE` oprávnění (kvůli Alembic migraci při startu)
- Test: `psql "<DATABASE_URL>" -c "select 1;"`

### GraphNav nejde spustit

Zkontroluj:

- že mapa v DB odpovídá fyzickému prostředí
- že fiducial má stejné ID jako v mapě (wizard to hlídá)
- že prostředí se výrazně nezměnilo od nahrání

### OCR výsledky jsou prázdné

- Ověř, že YOLO model je na cestě `ocr/license-plate-finetune-v1m.pt`.
- Koukni do `logs/spot_operator.log` — pokud worker nespustil, uvidíš důvod.
- Zkus Re-OCR fallbackem (Nomeroff) v CRUD → Fotky → detail.

### Diagnostika

Spuštění s `--diag`:

```bat
python main.py --diag
```

Vypíše verze klíčových balíčků a cesty — užitečné při hlášení chyb.

---

## Struktura projektu

```
c:\Users\zige\spot\
├── main.py                      # entry point
├── launch.bat / setup_venv.bat / run_app.bat
├── requirements.txt
├── .env.example
├── alembic.ini + alembic/       # DB migrace
├── logs/                         # rotující log
├── temp/                         # dočasné extract map (čistí se při startu)
├── spot_operator/                # hlavní Python balíček
│   ├── bootstrap.py              # sys.path injection
│   ├── config.py, logging_config.py, constants.py
│   ├── db/                       # SQLAlchemy + Alembic + repositories
│   ├── robot/                    # wrappery + session factory
│   ├── ocr/                      # YOLO detector + fast-plate-ocr + Nomeroff subprocess fallback
│   ├── services/                 # map storage, photo sink, OCR worker, zip exporter, ...
│   └── ui/                       # PySide6 — main window, wizards, crud (odstranitelné)
├── autonomy/                     # podprojekt — zůstává samostatně spustitelný
└── ocr/                          # podprojekt — zůstává samostatně spustitelný
```

---

## Pro vývojáře

Aplikace má **dvě vrstvy developer dokumentace** pro ty, kdo ji chtějí upravovat
nebo na ni nasadit AI agenta:

- **[instructions.md](instructions.md)** — **normativní pravidla**:
  architektonická rozhodnutí, explicitní zákazy (co se nesmí měnit v `autonomy/`
  a `ocr/`), pravidla pro každý prompt AI agenta, upgrade path. Glosář pojmů
  (waypoint, checkpoint, fiducial, run, ...).
  **Pokud jsi AI agent pracující na této codebase, čti to jako první.**
- **[instructions-reference.md](instructions-reference.md)** — **implementační
  reference**: kompletní adresářový layout, DB schéma všech tabulek, API
  signatury `autonomy`/`ocr` + additive modulů, 9 inline code samples pro
  klíčové moduly (`OcrWorker`, `map_extracted` context manager, fiducial check,
  return home, ...), implementační pořadí, styl kódu, postup pinutí verzí.
- **[CHANGELOG.md](CHANGELOG.md)** — **historie verzí dokumentace**. Formát
  Keep a Changelog + semver. Při jakékoli změně `instructions*.md` sem přidat
  záznam.

### Rozdělení rolí mezi dokumenty

| Dokument | Pro koho | Obsah |
|---|---|---|
| `README.md` (tento) | operátor / onboarding | instalace, spuštění, **klávesy**, **troubleshooting**, **safety poznámky** |
| `instructions.md` | AI agent / dev (normativ) | glosář, architektonická rozhodnutí, zákazy, pravidla pro prompt |
| `instructions-reference.md` | AI agent / dev (detail) | API, DB schéma, code samples, implementační pořadí |

**Pravidlo:** stejný obsah **nesmí být na dvou místech**. Pokud potřebuješ
referenci, napiš jednořádkový odkaz ("viz [README sekce 9](README.md#9-ovládání-klávesnicí)"),
ne copy-paste.

---

## Licence

Interní projekt. Autonomy a OCR projekty mají své vlastní licence — respektuj je.
