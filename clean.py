#!/usr/bin/env python3
"""Clean — smaže cache a build artefakty pro distribuci projektu.

Spuštění:
    python clean.py               # dry-run: jen ukáže co by se smazalo
    python clean.py --execute     # opravdu smaže (s confirm prompt)
    python clean.py -y            # bez confirm promptu
    python clean.py --help        # nápověda

Co se maže:
    - __pycache__ rekurzivně v celém stromu
    - .pytest_cache, .mypy_cache, .ruff_cache, .tox, htmlcov
    - *.pyc, *.pyo, *.pyd rekurzivně
    - build/, dist/, logs/, temp/ (root-only)
    - .venv/, venv/ (virtualenv — není portable, třeba re-setup)
    - build.log, build_v5.log, .coverage (root-only)

Co NESMAŽE (nechá tam):
    - .git/ — historie commitů
    - .claude/ — uživatelské Claude Code nastavení
    - zdrojový kód (.py, .md, .txt, .ini, .cfg, .bat)
    - modely (.pt, .pb, .onnx, .pth, .h5)
    - requirements.txt, alembic migrace
    - .env / .env.example (POZOR — .env obsahuje DB heslo,
      před zabalením zvaž, jestli ho chceš posílat)
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

# Windows console default cp1252 nezvládá české znaky → přepnout na UTF-8.
# Python 3.7+ umožňuje reconfigure stdout/stderr (platí pro current process).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parent

# Adresáře, které hledáme rekurzivně v celém stromu.
RECURSIVE_DIR_NAMES: frozenset[str] = frozenset({
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "htmlcov",
    ".ipynb_checkpoints",
})

# Souborové suffixy smazané rekurzivně.
RECURSIVE_FILE_SUFFIXES: frozenset[str] = frozenset({
    ".pyc", ".pyo", ".pyd",
})

# Adresáře smazané pouze v rootu projektu.
ROOT_ONLY_DIRS: tuple[str, ...] = (
    "build",
    "dist",
    ".venv",
    "venv",
    "logs",
    "temp",
)

# Soubory smazané pouze v rootu projektu.
ROOT_ONLY_FILES: tuple[str, ...] = (
    "build.log",
    "build_v5.log",
    ".coverage",
    ".DS_Store",
)

# Do těchto adresářů se při rekurzi vůbec nelezeme.
SKIP_DESCENT_INTO: frozenset[str] = frozenset({
    ".git",
    ".claude",
    ".venv",
    "venv",
    "build",  # smazán jako celek, nelezeme dovnitř
    "dist",
})


def _human_bytes(n: int) -> str:
    """Přelož byty na lidsky čitelný řetězec."""
    f_n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f_n < 1024.0:
            return f"{f_n:.1f} {unit}"
        f_n /= 1024.0
    return f"{f_n:.1f} TB"


def _dir_size(path: Path) -> int:
    """Spočítá velikost adresáře v bajtech (best-effort)."""
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _on_rm_error(func, path, exc_info) -> None:  # type: ignore[no-untyped-def]
    """Shutil.rmtree error handler — Windows read-only soubory (např. .git
    objects) potřebujeme chmod před retry."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as exc:
        print(f"  ! nelze smazat {path}: {exc}", file=sys.stderr)


def scan() -> tuple[list[tuple[Path, int]], list[tuple[Path, int]]]:
    """Oskenuje strom a vrátí (adresáře_ke_smazání, soubory_ke_smazání).

    Každá položka je tuple (path, velikost_bajtů).
    """
    dirs: dict[Path, int] = {}
    files: dict[Path, int] = {}

    # Root-only adresáře.
    for name in ROOT_ONLY_DIRS:
        p = ROOT / name
        if p.is_dir():
            dirs[p.resolve()] = _dir_size(p)

    # Root-only soubory.
    for name in ROOT_ONLY_FILES:
        p = ROOT / name
        if p.is_file():
            try:
                files[p.resolve()] = p.stat().st_size
            except OSError:
                files[p.resolve()] = 0

    # Rekurzivní sken.
    for dirpath, dirnames, filenames in os.walk(ROOT):
        current = Path(dirpath)
        # Filtruj potomky — os.walk respektuje mutaci dirnames.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DESCENT_INTO]

        for d in list(dirnames):
            if d in RECURSIVE_DIR_NAMES:
                full = (current / d).resolve()
                if full not in dirs:
                    dirs[full] = _dir_size(full)
                # Nechceme do mazaného adresáře dále sestupovat.
                dirnames.remove(d)

        for f in filenames:
            suffix = Path(f).suffix.lower()
            if suffix in RECURSIVE_FILE_SUFFIXES:
                full = (current / f).resolve()
                if full in files:
                    continue
                try:
                    files[full] = full.stat().st_size
                except OSError:
                    files[full] = 0

    # Odfiltruj cokoliv mimo ROOT (safety).
    safe_dirs = [
        (p, size) for p, size in dirs.items()
        if _is_under_root(p)
    ]
    safe_files = [
        (p, size) for p, size in files.items()
        if _is_under_root(p)
    ]
    safe_dirs.sort(key=lambda item: str(item[0]))
    safe_files.sort(key=lambda item: str(item[0]))
    return safe_dirs, safe_files


def _is_under_root(path: Path) -> bool:
    """Zkontroluje, že ``path`` leží pod ROOT (prevence catastrophic rm)."""
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def delete(dirs: list[tuple[Path, int]], files: list[tuple[Path, int]]) -> tuple[int, int]:
    """Smaže zadané cíle. Vrací (počet_smazaných, počet_chyb)."""
    deleted = 0
    errors = 0
    for path, _size in dirs:
        if not path.is_dir():
            continue
        try:
            shutil.rmtree(path, onerror=_on_rm_error)
            print(f"  - smazán adresář: {path.relative_to(ROOT)}")
            deleted += 1
        except Exception as exc:
            print(f"  ! nelze smazat {path}: {exc}", file=sys.stderr)
            errors += 1
    for path, _size in files:
        if not path.is_file():
            continue
        try:
            path.unlink()
            print(f"  - smazán soubor:  {path.relative_to(ROOT)}")
            deleted += 1
        except Exception as exc:
            print(f"  ! nelze smazat {path}: {exc}", file=sys.stderr)
            errors += 1
    return deleted, errors


def _confirm() -> bool:
    try:
        answer = input("Opravdu smazat všechny vyjmenované položky? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes", "a", "ano"}


def _check_env_warning() -> None:
    env = ROOT / ".env"
    if env.is_file():
        print(
            "\nPOZOR: .env v rootu obsahuje DATABASE_URL (včetně hesla). "
            "Před zabalením / posláním zvaž, jestli ho vymazat ručně nebo "
            "nahradit .env.example.\n",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smaže cache a build artefakty pro distribuci projektu."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Skutečně smazat (bez tohoto flagu jen dry-run výpis).",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Bez confirm promptu (implikuje --execute).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Jen ukázat, co by se smazalo (default, pokud není --execute).",
    )
    args = parser.parse_args()

    if args.yes:
        args.execute = True
    if args.dry_run:
        args.execute = False

    print(f"Clean — root: {ROOT}")
    dirs, files = scan()

    if not dirs and not files:
        print("\nNic na smazání — strom je již čistý.")
        _check_env_warning()
        return 0

    print(f"\nNalezeno k smazání: {len(dirs)} adresářů, {len(files)} souborů")
    total_size = sum(size for _, size in dirs) + sum(size for _, size in files)
    print(f"Celková velikost:    {_human_bytes(total_size)}")
    print()

    if dirs:
        print("Adresáře:")
        for path, size in dirs:
            rel = path.relative_to(ROOT)
            print(f"  [{_human_bytes(size):>10}]  {rel}")
    if files:
        print("\nSoubory:")
        for path, size in files:
            rel = path.relative_to(ROOT)
            print(f"  [{_human_bytes(size):>10}]  {rel}")

    if not args.execute:
        print(
            "\n(DRY-RUN — nic se nemaže. Spusť s --execute nebo -y pro skutečné smazání.)"
        )
        _check_env_warning()
        return 0

    if not args.yes:
        print()
        if not _confirm():
            print("Zrušeno.")
            return 1

    print("\nMažu...")
    deleted, errors = delete(dirs, files)
    print(f"\nHotovo: smazáno {deleted} položek, {errors} chyb.")
    _check_env_warning()
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
