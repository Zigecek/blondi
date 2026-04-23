"""Wi-Fi diagnostika — ping + TCP connect k Spotovi.

Nepracuje s přepínáním Wi-Fi (to by chtělo admin práva přes netsh wlan). Jen
ověří, že se k zadané IP dostaneme. Volá se z wizard kroku "Kontrola připojení".

SSID detekce byla v 1.1.1 odstraněna — `netsh wlan show interfaces` vrací SSID
náhodné Wi-Fi karty (obvykle první v pořadí), což nemusí být ta, přes kterou
ping jde. Při multi-Wi-Fi setupu to bylo matoucí. Ping + TCP stačí jako důkaz.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from dataclasses import dataclass

from spot_operator.constants import WIFI_PING_COUNT, WIFI_PING_TIMEOUT_SEC, WIFI_TCP_PORT
from spot_operator.logging_config import get_logger

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
        return self.tcp_reachable and self.ping_responses > 0


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
    """Vrátí počet úspěšných ping odpovědí (0..count)."""
    if sys.platform.startswith("win"):
        args = ["ping", "-n", str(count), "-w", str(int(timeout_s * 1000)), ip]
    else:  # pragma: no cover - projekt je primárně Windows
        args = ["ping", "-c", str(count), "-W", str(int(timeout_s)), ip]

    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout_s * count + 5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0

    # Windows ping výstup: "Received = N"
    # Linux ping výstup: "N received"
    text = proc.stdout.lower()
    if "received =" in text:
        try:
            part = text.split("received =", 1)[1].strip().split(",", 1)[0].strip()
            return int(part)
        except Exception:
            pass
    if " received" in text:
        for line in text.splitlines():
            if " received" in line:
                try:
                    return int(line.split("received", 1)[0].strip().split()[-1])
                except Exception:
                    pass
    # Fallback — if exit code is 0, assume count responses
    return count if proc.returncode == 0 else 0


def _tcp_connect(ip: str, *, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            return True
    except Exception:
        return False


def _format_detail(responses: int, attempts: int, tcp_ok: bool) -> str:
    parts = [f"ping {responses}/{attempts}"]
    parts.append("TCP OK" if tcp_ok else "TCP nedostupný")
    return ", ".join(parts)


def open_windows_wifi_menu() -> None:
    """Otevře Windows dialog s Wi-Fi sítěmi (pro pohodlí operátora)."""
    if not sys.platform.startswith("win"):
        return
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", "ms-availablenetworks:"],
            creationflags=0,
        )
    except Exception as exc:
        _log.warning("Failed to open Windows Wi-Fi menu: %s", exc)


__all__ = ["WifiCheckResult", "check_connection", "open_windows_wifi_menu"]
