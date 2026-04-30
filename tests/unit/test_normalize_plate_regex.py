"""Testy pro _normalize_plate s regex validací (PR-06 FIND-112)."""

from __future__ import annotations

from blondi.ocr.reader import _normalize_plate


def test_normalize_strips_non_alnum() -> None:
    assert _normalize_plate("AB-123") == "AB123"


def test_normalize_uppercases() -> None:
    assert _normalize_plate("ab 123") == "AB123"


def test_normalize_rejects_too_long() -> None:
    # PLATE_TEXT_REGEX: max 16 znaků. "A"*17 neprojde.
    assert _normalize_plate("A" * 17) == ""


def test_normalize_accepts_16_chars() -> None:
    assert _normalize_plate("A" * 16) == "A" * 16


def test_normalize_empty_input() -> None:
    assert _normalize_plate("") == ""
    assert _normalize_plate(None) == ""  # type: ignore[arg-type]


def test_normalize_whitespace_only() -> None:
    assert _normalize_plate("   ") == ""
