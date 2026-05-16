"""Demo režim — mocky pro Spot SDK + screenshot capture + DB seed.

Aktivuje se přes ``BLONDI_DEMO=1`` env proměnnou. Cílem je proklikat
celou aplikaci bez fyzického robota — pro screenshoty do prezentace.

Moduly:
- ``mock_spot``: ``MockSpotBundle`` se stejným rozhraním jako reálný ``SpotBundle``.
- ``mock_fiducial``: fake fiducial observation.
- ``mock_recording_service`` / ``mock_playback_service``: rychlé fake completion
  s realistickým event timingem.
- ``live_view_stub``: composed QPixmap z ``_demo_assets/left.png + right.png``.
- ``seed``: idempotent seed bohaté demo DB (5 map, 10 runů, 50 fotek, 30 SPZ).
- ``screenshot_capture``: globální F12 hotkey s name detection podle aktuální
  obrazovky.
"""

from __future__ import annotations
