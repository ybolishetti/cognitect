"""Tests for the StubGenerator egress patch (Architecture C, DRAFT 7).

10 tests, all fast, no network. Covers the two new emission passes added
to engine/generators/stub.py: _emit_exterior_egress_door (IRC §R311.2) and
_emit_bedroom_egress_windows (IRC §R310.1), plus the payoff check that
Layer C actually passes on stub output now.
"""

from __future__ import annotations

from engine.generators import STUB_VERSION, StubGenerator
from engine.layout import FloorPlanSpec, RoomRequirement
from engine.verifiers import verify_layer_c


def _make_spec_2room() -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id="spec_egress_2room",
        original_nl="a 2-room test plan",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=150.0),
        ],
        n_candidates=1,
    )


def _make_spec_3room() -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id="spec_egress_3room",
        original_nl="a 3-room test plan",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Kitchen", room_type="kitchen", preferred_area_sqft=150.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=180.0),
        ],
        n_candidates=1,
    )


def _make_spec_2bedrooms() -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id="spec_egress_2bedroom",
        original_nl="a plan with two bedrooms",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Bedroom1", room_type="bedroom", preferred_area_sqft=150.0),
            RoomRequirement(name="Bedroom2", room_type="bedroom", preferred_area_sqft=140.0),
        ],
        n_candidates=1,
    )


def test_two_room_spec_produces_exterior_door():
    layout = StubGenerator().generate(_make_spec_2room())[0]
    walls_by_id = {w.id: w for w in layout.walls}
    exterior_doors = [
        o for o in layout.openings
        if o.opening_type == "door" and len(walls_by_id[o.wall_id].bounds_rooms) == 1
    ]
    assert len(exterior_doors) >= 1


def test_exterior_door_meets_r311_2_width():
    layout = StubGenerator().generate(_make_spec_2room())[0]
    walls_by_id = {w.id: w for w in layout.walls}
    exterior_doors = [
        o for o in layout.openings
        if o.opening_type == "door" and len(walls_by_id[o.wall_id].bounds_rooms) == 1
    ]
    assert exterior_doors
    assert all(o.width_ft >= 3.0 for o in exterior_doors)


def test_bedroom_spec_produces_bedroom_egress_window():
    layout = StubGenerator().generate(_make_spec_2room())[0]
    walls_by_id = {w.id: w for w in layout.walls}
    bedroom = next(r for r in layout.rooms if r.room_type == "bedroom")
    bedroom_exterior_wall_ids = {
        wid for wid in bedroom.boundary_wall_ids
        if len(walls_by_id[wid].bounds_rooms) == 1
        and walls_by_id[wid].bounds_rooms[0] == bedroom.id
    }
    bedroom_egress_openings = [
        o for o in layout.openings
        if o.wall_id in bedroom_exterior_wall_ids and o.opening_type in ("door", "window")
    ]
    assert bedroom_egress_openings, "expected a door or window on the bedroom's exterior wall"


def test_bedroom_egress_window_on_exterior_wall():
    layout = StubGenerator().generate(_make_spec_2room())[0]
    walls_by_id = {w.id: w for w in layout.walls}
    windows = [o for o in layout.openings if o.opening_type == "window"]
    for window in windows:
        wall = walls_by_id[window.wall_id]
        assert len(wall.bounds_rooms) == 1, "windows must only be on exterior walls"


def test_multiple_bedrooms_get_multiple_windows():
    layout = StubGenerator().generate(_make_spec_2bedrooms())[0]
    walls_by_id = {w.id: w for w in layout.walls}
    bedrooms = [r for r in layout.rooms if r.room_type == "bedroom"]
    assert len(bedrooms) == 2

    for bedroom in bedrooms:
        bedroom_exterior_wall_ids = {
            wid for wid in bedroom.boundary_wall_ids
            if len(walls_by_id[wid].bounds_rooms) == 1
            and walls_by_id[wid].bounds_rooms[0] == bedroom.id
        }
        egress_openings = [
            o for o in layout.openings
            if o.wall_id in bedroom_exterior_wall_ids and o.opening_type in ("door", "window")
        ]
        assert egress_openings, f"bedroom {bedroom.id} has no egress opening"


def test_bedroom_already_on_exterior_door_gets_no_window():
    """Invariant: no bedroom has both a door AND a window on the same
    exterior wall — if the R311.2 exterior door already lands on a
    bedroom's exterior wall, that bedroom is compliant and gets no
    redundant window."""
    layout = StubGenerator().generate(_make_spec_2room())[0]
    walls_by_id = {w.id: w for w in layout.walls}
    openings_by_wall: dict[str, list[str]] = {}
    for o in layout.openings:
        openings_by_wall.setdefault(o.wall_id, []).append(o.opening_type)

    for room in layout.rooms:
        if room.room_type != "bedroom":
            continue
        for wid in room.boundary_wall_ids:
            wall = walls_by_id[wid]
            if len(wall.bounds_rooms) != 1 or wall.bounds_rooms[0] != room.id:
                continue
            types_on_wall = openings_by_wall.get(wid, [])
            assert not ("door" in types_on_wall and "window" in types_on_wall), (
                f"bedroom {room.id} wall {wid} has both a door and a window"
            )


def test_verify_layer_c_passes_on_stub_output_2room():
    layout = StubGenerator().generate(_make_spec_2room())[0]
    result = verify_layer_c(layout)
    assert result.passed is True, result.failures


def test_verify_layer_c_passes_on_stub_output_3room():
    layout = StubGenerator().generate(_make_spec_3room())[0]
    result = verify_layer_c(layout)
    assert result.passed is True, result.failures


def test_stub_still_deterministic_after_egress_patch():
    spec = _make_spec_2room()
    a = StubGenerator().generate(spec)[0]
    b = StubGenerator().generate(spec)[0]

    def _opening_key(o):
        return (o.id, o.opening_type, o.wall_id, o.offset_ft, o.width_ft)

    a_openings = sorted(_opening_key(o) for o in a.openings)
    b_openings = sorted(_opening_key(o) for o in b.openings)
    assert a_openings == b_openings


def test_stub_version_bumped():
    assert STUB_VERSION == "2026-07-14"
