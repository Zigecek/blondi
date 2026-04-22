"""Unit test normalizace SPZ textu (čistá funkce, bez DB)."""

from __future__ import annotations

from spot_operator.db.repositories.plates_repo import normalize_plate_text


def test_normalize_removes_spaces_and_dashes():
    assert normalize_plate_text("2AB 1234") == "2AB1234"
    assert normalize_plate_text("1ab-1234") == "1AB1234"
    assert normalize_plate_text("  cz99 aaa  ") == "CZ99AAA"


def test_normalize_strips_special_chars():
    assert normalize_plate_text("AB*1234?") == "AB1234"


def test_normalize_empty():
    assert normalize_plate_text("") == ""
    assert normalize_plate_text("    ") == ""


def test_normalize_uppercase():
    assert normalize_plate_text("abcd1234") == "ABCD1234"
