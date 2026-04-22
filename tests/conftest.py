"""Pytest — bootstrap sys.path, fixture pro postgresql DB."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Bootstrap — musí být PŘED importem spot_operator.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spot_operator.bootstrap import inject_paths  # noqa: E402

inject_paths()

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture
def ocr_test_images_dir(project_root: Path) -> Path:
    return project_root / "ocr" / "test"
