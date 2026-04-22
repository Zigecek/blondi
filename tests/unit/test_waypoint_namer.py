"""Unit testy WaypointNameGenerator (v autonomy/app/robot/waypoint_namer.py)."""

from __future__ import annotations


def test_waypoint_sequence():
    from app.robot.waypoint_namer import WaypointNameGenerator

    gen = WaypointNameGenerator()
    assert gen.next_waypoint() == "WP_001"
    assert gen.next_waypoint() == "WP_002"
    assert gen.next_checkpoint() == "CP_001"
    assert gen.next_waypoint() == "WP_003"
    assert gen.next_checkpoint() == "CP_002"
    assert gen.waypoint_count == 3
    assert gen.checkpoint_count == 2


def test_reset_zeroes_both_counters():
    from app.robot.waypoint_namer import WaypointNameGenerator

    gen = WaypointNameGenerator()
    gen.next_waypoint()
    gen.next_checkpoint()
    gen.reset()
    assert gen.next_waypoint() == "WP_001"
    assert gen.next_checkpoint() == "CP_001"


def test_custom_prefix():
    from app.robot.waypoint_namer import WaypointNameGenerator

    gen = WaypointNameGenerator(waypoint_prefix="NAV", checkpoint_prefix="PHOTO")
    assert gen.next_waypoint() == "NAV_001"
    assert gen.next_checkpoint() == "PHOTO_001"
