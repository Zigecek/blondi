"""Wi-Fi diagnostika — ping + TCP connect k Spotovi.

Nepracuje s přepínáním Wi-Fi (to by chtělo admin práva přes netsh wlan). Jen
ověří, že se k zadané IP dostaneme. Volá se z wizard kroku "Kontrola připojení".

SSID detekce byla v 1.1.1 odstraněna — `netsh wlan show interfaces` vrací SSID
náhodné Wi-Fi karty (obvykle první v pořadí), což nemusí být ta, přes kterou
ping jde. Při multi-Wi-Fi setupu to bylo matoucí. Ping + TCP stačí jako důkaz.

PR-10 FIND-124: locale-independent ping — N × single-ping subprocess volání
s check na returncode, místo parsování lokalizovaného textu výstupu.
PR-10 FIND-128: ``WifiCheckResult.ok`` teď záleží jen na TCP (Spot může
blokovat ICMP firewallem, ale RPC na 443 je dostupné).
"""

from __future__ import annotations

import socket
import subprocess
import sys
from dataclasses import dataclass

from blondi.constants import WIFI_PING_COUNT, WIFI_PING_TIMEOUT_SEC, WIFI_TCP_PORT
from blondi.logging_config import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WifiCheckResult:
    ip: str
    ping_responses: int
    ping_attempts: int
    tcp_reachable: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        # TCP je autoritativní — ICMP ping může být blokovaný firewallem.
        return self.tcp_reachable


def check_connection(
    ip: str,
    *,
    ping_count: int = WIFI_PING_COUNT,
    ping_timeout_s: float = WIFI_PING_TIMEOUT_SEC,
    tcp_port: int = WIFI_TCP_PORT,
) -> WifiCheckResult:
    """Provede ping + TCP connect a vrátí souhrnný výsledek."""
    responses = _ping(ip, count=ping_count, timeout_s=ping_timeout_s)
    tcp_ok = _tcp_connect(ip, port=tcp_port, timeout_s=ping_timeout_s)
    result = WifiCheckResult(
        ip=ip,
        ping_responses=responses,
        ping_attempts=ping_count,
        tcp_reachable=tcp_ok,
        detail=_format_detail(responses, ping_count, tcp_ok),
    )
    _log.info("Wi-Fi check: %s", result)
    return result


def _ping(ip: str, *, count: int, timeout_s: float) -> int:
    """Vrátí počet úspěšných ping odpovědí (0..count).

    Locale-independent (PR-10 FIND-124): N × single-ping subprocess s
    returncode check. Parsování textu `Received = N` selhávalo na CZ
    Windows ("Přijato = N").
    """
    per_attempt_timeout = max(timeout_s * count + 5.0, 5.0)
    per_attempt_timeout = min(per_attempt_timeout, 30.0)

    responses = 0
    for _attempt in range(count):
        if sys.platform.startswith("win"):
            args = ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), ip]
        else:  # pragma: no cover — projekt je primárně Windows
            args = ["ping", "-c", "1", "-W", str(int(timeout_s)), ip]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=per_attempt_timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            _log.debug("ping %s attempt %d failed: %s", ip, _attempt + 1, exc)
            continue
        if proc.returncode == 0:
            responses += 1
    return responses


def _tcp_connect(ip: str, *, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            return True
    except Exception as exc:
        # PR-10 FIND-126: log debug pro diagnostiku (connection refused
        # vs no route vs timeout).
        _log.debug("TCP connect %s:%d failed: %s", ip, port, exc)
        return False


def _format_detail(responses: int, attempts: int, tcp_ok: bool) -> str:
    parts = [f"ping {responses}/{attempts}"]
    parts.append("TCP OK" if tcp_ok else "TCP nedostupný")
    return ", ".join(parts)


def open_windows_wifi_menu() -> None:
    """Otevře Windows dialog s Wi-Fi sítěmi (pro pohodlí operátora).

    Na non-Windows raise NotImplementedError (PR-10 FIND-127).
    UI má tlačítko skrýt / disable podle platformy.
    """
    if not sys.platform.startswith("win"):
        raise NotImplementedError(
            "open_windows_wifi_menu je podporován pouze na Windows."
        )
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", "ms-availablenetworks:"],
            creationflags=0,
        )
    except Exception as exc:
        _log.warning("Failed to open Windows Wi-Fi menu: %s", exc)


__all__ = ["WifiCheckResult", "check_connection", "open_windows_wifi_menu"]
