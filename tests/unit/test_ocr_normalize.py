"""Unit test normalizace OCR textu v fast-plate-ocr reader."""

from __future__ import annotations

from spot_operator.ocr.reader import _normalize_plate


def test_normalize_removes_non_alnum():
    assert _normalize_plate("  2ab 12-34  ") == "2AB1234"


def test_normalize_empty_returns_empty():
    assert _normalize_plate("") == ""


def test_normalize_uppercases():
    assert _normalize_plate("hello") == "HELLO"
