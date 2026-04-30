"""Jednotné dialogy (chyba, potvrzení) s českými texty."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QMessageBox, QWidget


def error_dialog(parent: Optional[QWidget], title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    box.exec()


def warning_dialog(parent: Optional[QWidget], title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    box.exec()


def info_dialog(parent: Optional[QWidget], title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(title)
    box.setText(message)
    box.exec()


def confirm_dialog(
    parent: Optional[QWidget],
    title: str,
    message: str,
    *,
    destructive: bool = False,
) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning if destructive else QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(message)
    yes = box.addButton("Ano", QMessageBox.AcceptRole)
    box.addButton("Ne", QMessageBox.RejectRole)
    box.setDefaultButton(yes if not destructive else box.buttons()[1])
    box.exec()
    return box.clickedButton() is yes


__all__ = ["error_dialog", "warning_dialog", "info_dialog", "confirm_dialog"]
