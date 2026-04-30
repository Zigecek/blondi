"""Unit test normalizace OCR textu v fast-plate-ocr reader + grayscale vstup."""

from __future__ import annotations

import numpy as np

from blondi.ocr.reader import FastPlateReader, _normalize_plate


def test_normalize_removes_non_alnum():
    assert _normalize_plate("  2ab 12-34  ") == "2AB1234"


def test_normalize_empty_returns_empty():
    assert _normalize_plate("") == ""


def test_normalize_uppercases():
    assert _normalize_plate("hello") == "HELLO"


class _RecordingModel:
    """Fake fast_plate_ocr.LicensePlateRecognizer — zachytí co dostane do run()."""

    def __init__(self):
        self.last_input: np.ndarray | None = None

    def run(self, arr, return_confidence=True):  # noqa: ANN001
        self.last_input = arr
        return ("ABC1234", [0.95])


def test_reader_feeds_grayscale_not_rgb():
    """FastPlateReader.read() MUSÍ předat modelu grayscale (2D) nebo (H,W,1).

    Bez tohoto test-check by se regressní bug ONNX "index 3 Got 3 Expected 1"
    (3-channel RGB místo 1-channel grayscale) vrátil zpět nepozorovaně.
    """
    reader = FastPlateReader()
    fake = _RecordingModel()
    reader._reader = fake  # skip _ensure_loaded model import

    # BGR crop 20×40×3 (uniformní šedá, ale 3 kanály — jako reálný crop z YOLO).
    bgr_crop = np.full((20, 40, 3), 128, dtype=np.uint8)
    text, conf = reader.read(bgr_crop)

    assert fake.last_input is not None, "reader.run() nebyl zavolán"
    arr = fake.last_input
    # Grayscale: buď ndim==2 (H,W) nebo poslední dim == 1 (H,W,1).
    is_grayscale = arr.ndim == 2 or (arr.ndim == 3 and arr.shape[-1] == 1)
    assert is_grayscale, (
        f"fast_plate_ocr musí dostat grayscale; dostal shape={arr.shape} "
        f"(ndim={arr.ndim})"
    )
    assert text == "ABC1234"
    assert conf == 0.95
