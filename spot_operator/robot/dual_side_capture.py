"""Multi-source focení s tolerantním chováním při částečné chybě."""

from __future__ import annotations

from typing import Any

from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


def capture_sources(image_poller: Any, sources: list[str]) -> dict[str, Any]:
    """Pořídí snímky z každého source v seznamu.

    Nikdy nevyhazuje; pokud kamera neodpoví, vrátí výsledek bez toho source
    a zaloguje warning. Tak dotažené fotky projdou dál, chybějící budou
    viditelně chybět v UI logu.

    Args:
        image_poller: autonomy ImagePoller instance.
        sources: jména image sources (např. ['left_fisheye_image', 'right_fisheye_image']).

    Returns:
        dict[source_name -> np.ndarray (BGR)]. Nezachycené chybí.
    """
    out: dict[str, Any] = {}
    for src in sources:
        try:
            frame = image_poller.capture(src)
            if frame is None:
                _log.warning("capture returned None for source %s", src)
                continue
            out[src] = frame
        except Exception as exc:
            _log.warning("capture failed for source %s: %s", src, exc)
    return out


__all__ = ["capture_sources"]
