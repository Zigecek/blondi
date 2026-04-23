"""Tests pro EstopFloating povinný on_release (PR-01, PR-14).

Headless Qt — vyžaduje QApplication v conftest.
"""

from __future__ import annotations

import pytest


def _qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_estop_requires_on_release_callback() -> None:
    """on_release=None musí raise TypeError (safety contract)."""
    _qapp()
    from PySide6.QtWidgets import QWidget

    from spot_operator.ui.common.estop_floating import EstopFloating

    parent = QWidget()
    with pytest.raises(TypeError, match="on_release"):
        EstopFloating(parent, on_trigger=lambda: None, on_release=None)  # type: ignore[arg-type]


def test_estop_requires_on_trigger_callback() -> None:
    _qapp()
    from PySide6.QtWidgets import QWidget

    from spot_operator.ui.common.estop_floating import EstopFloating

    parent = QWidget()
    with pytest.raises(TypeError, match="on_trigger"):
        EstopFloating(parent, on_trigger=None, on_release=lambda: None)  # type: ignore[arg-type]


def test_estop_trigger_release_cycle() -> None:
    _qapp()
    from PySide6.QtWidgets import QWidget

    from spot_operator.ui.common.estop_floating import EstopFloating

    trigger_called = []
    release_called = []

    parent = QWidget()
    widget = EstopFloating(
        parent,
        on_trigger=lambda: trigger_called.append(True),
        on_release=lambda: release_called.append(True),
    )
    assert not widget.is_triggered
    widget._on_click()
    assert widget.is_triggered
    assert trigger_called == [True]
    widget._on_click()
    assert not widget.is_triggered
    assert release_called == [True]


def test_estop_release_callback_failure_keeps_triggered() -> None:
    """Pokud on_release raise, widget zůstane v triggered stavu — neresetuje
    vizual, protože robot je fyzicky stále v E-Stop."""
    _qapp()
    from PySide6.QtWidgets import QWidget

    from spot_operator.ui.common.estop_floating import EstopFloating

    def bad_release() -> None:
        raise RuntimeError("network lost")

    parent = QWidget()
    widget = EstopFloating(
        parent, on_trigger=lambda: None, on_release=bad_release
    )
    widget._on_click()  # trigger
    assert widget.is_triggered
    widget._on_click()  # try release — selže
    assert widget.is_triggered  # ZŮSTÁVÁ triggered (bezpečnost)
