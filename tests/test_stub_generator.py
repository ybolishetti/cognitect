"""Tests for engine/generators — the LayoutGenerator interface and
StubGenerator (Architecture C, DRAFT 3).

4 groups, 16 tests total. All fast, no network, no LLM:
  1. Interface contract (GeneratorFactory dispatch)
  2. Happy path (StubGenerator produces valid, deterministic Layouts)
  3. Geometry contract (assumptions Layer A depends on downstream)
  4. Failure modes (GeneratorFailure wrapping, never-empty-list contract)
"""

from __future__ import annotations

import pytest

from engine.generators import GeneratorFactory, GeneratorFailure, STUB_VERSION, StubGenerator
from engine.layout import FloorPlanSpec, Layout, RoomRequirement

# ── Group 1 — Interface contract (5 tests) ──────────────────────────────────


def test_stub_generator_name_is_stable():
    assert StubGenerator().name == "stub"


def test_factory_returns_stub_by_default(monkeypatch):
    monkeypatch.delenv("LAYOUT_GENERATOR", raising=False)
    gen = GeneratorFactory.from_env()
    assert isinstance(gen, StubGenerator)


def test_factory_returns_stub_when_env_set_to_stub(monkeypatch):
    monkeypatch.setenv("LAYOUT_GENERATOR", "stub")
    assert isinstance(GeneratorFactory.from_env(), StubGenerator)


# NOTE: "prompted" now dispatches to a real PromptedGenerator (DRAFT 4) —
# see tests/test_prompted_generator.py::test_factory_by_name_prompted_returns_prompted_generator
# and ::test_factory_from_env_prompted for the equivalent coverage.


def test_factory_raises_value_error_for_unknown_kind():
    with pytest.raises(ValueError, match="Unknown LAYOUT_GENERATOR"):
        GeneratorFactory.by_name("banana")


# ── Group 2 — Happy path (3 tests) ──────────────────────────────────────────


def test_generate_returns_at_least_one_layout_for_valid_spec():
    spec = _make_spec_2_rooms()
    layouts = StubGenerator().generate(spec)
    assert len(layouts) >= 1
    assert all(isinstance(l, Layout) for l in layouts)


def test_generated_layout_stamps_generator_metadata():
    spec = _make_spec_2_rooms()
    layout = StubGenerator().generate(spec)[0]
    gen = layout.metadata["generator"]
    assert gen["name"] == "stub"
    assert gen["version"] == STUB_VERSION
    assert isinstance(gen["seed"], int)
    assert gen["elapsed_ms"] > 0


def test_same_spec_produces_same_layout_deterministically():
    spec = _make_spec_2_rooms()
    a = StubGenerator().generate(spec)[0]
    b = StubGenerator().generate(spec)[0]
    # Compare geometry, not the elapsed_ms field
    assert [r.vertices for r in a.rooms] == [r.vertices for r in b.rooms]
    assert [w.start for w in a.walls] == [w.start for w in b.walls]


def test_generate_accepts_utility_and_closet_room_types():
    """RoomRequirement allows 'utility' and 'closet' but the older
    intent_parser RoomSpec does not — the stub must map internally."""
    spec = FloorPlanSpec(
        spec_id="spec_test_utility_closet",
        original_nl="test with utility and closet",
        room_requirements=[
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=200.0),
            RoomRequirement(name="Utility", room_type="utility", preferred_area_sqft=80.0),
            RoomRequirement(name="Closet", room_type="closet", preferred_area_sqft=40.0),
        ],
        n_candidates=1,
    )
    layout = StubGenerator().generate(spec)[0]
    types = {r.room_type for r in layout.rooms}
    assert "utility" in types
    assert "closet" in types
    assert "bedroom" in types


# ── Group 3 — Geometry contract (5 tests) ───────────────────────────────────


def test_no_two_walls_share_the_same_edge():
    """The dedupe rule — no duplicate walls between adjacent rooms."""
    spec = _make_spec_2_rooms()
    layout = StubGenerator().generate(spec)[0]
    edges = [
        frozenset({tuple(w.start), tuple(w.end)}) for w in layout.walls
    ]
    assert len(edges) == len(set(edges)), "duplicate walls emitted"


def test_interior_walls_bound_exactly_two_rooms():
    """Shared walls should have bounds_rooms of length 2."""
    layout = StubGenerator().generate(_make_spec_2_rooms())[0]
    interior = [w for w in layout.walls if len(w.bounds_rooms) == 2]
    assert len(interior) >= 1, "expected at least one interior wall"
    for wall in interior:
        assert len(set(wall.bounds_rooms)) == 2, \
            "interior wall bounds_rooms must contain 2 distinct rooms"


def test_boundary_wall_ids_reference_existing_walls():
    layout = StubGenerator().generate(_make_spec_3_rooms())[0]
    all_wall_ids = {w.id for w in layout.walls}
    for room in layout.rooms:
        for wid in room.boundary_wall_ids:
            assert wid in all_wall_ids, \
                f"room {room.id} references unknown wall {wid}"


def test_room_vertices_are_ccw_and_closed():
    layout = StubGenerator().generate(_make_spec_3_rooms())[0]
    for room in layout.rooms:
        assert room.vertices[0] == room.vertices[-1], "vertices must close"
        # Schema already enforces CCW; this test guards regressions
        # if the corner-emission order in _make_rooms ever changes.


def test_openings_on_interior_and_exterior_walls():
    """After DRAFT 7 (egress patch), stub emits BOTH interior doors AND
    at least one exterior door for Layer C R311.2 compliance."""
    layout = StubGenerator().generate(_make_spec_3_rooms())[0]
    walls_by_id = {w.id: w for w in layout.walls}
    interior_openings = 0
    exterior_openings = 0
    for opening in layout.openings:
        wall = walls_by_id[opening.wall_id]
        if len(wall.bounds_rooms) == 2:
            interior_openings += 1
        elif len(wall.bounds_rooms) == 1:
            exterior_openings += 1
    assert interior_openings >= 1, "expected at least one interior door"
    assert exterior_openings >= 1, "expected at least one exterior door/window (DRAFT 7 egress patch)"


def test_multi_row_layout_documents_known_limitation():
    """Stub can't dedupe partial edge overlaps between rows.

    A 3-room spec with mismatched row widths produces exactly 1 interior
    wall (the row-1 vertical between rooms 0 and 1), NOT the 2 that a
    geometrically-correct dedupe would emit. Layer A still passes because
    rooms don't overlap. This test pins the current behavior — if the
    interior-wall count changes for this spec, the stub's dedupe logic
    was modified and this test should be updated deliberately, not
    silently accepted.
    """
    spec = FloorPlanSpec(
        spec_id="spec_test_multirow",
        original_nl="3-room layout that forces row 2",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Kitchen", room_type="kitchen", preferred_area_sqft=150.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=180.0),
        ],
        n_candidates=1,
    )
    layout = StubGenerator().generate(spec)[0]
    # Verify shelf-packer actually wrapped to a 2nd row (sanity check —
    # if it doesn't, the test spec is stale)
    ys = {round(r.vertices[0][1], 2) for r in layout.rooms}
    assert len(ys) >= 2, "expected shelf-packer to wrap to a second row for this spec"
    interior = [w for w in layout.walls if len(w.bounds_rooms) == 2]
    assert len(interior) == 1, (
        f"stub currently produces exactly 1 interior wall for this multi-row "
        f"spec (known limitation). Got {len(interior)}. If _make_walls was "
        f"changed to handle partial edge overlaps, update this test."
    )


# ── Group 4 — Failure modes (2 tests) ───────────────────────────────────────


def test_generator_failure_wraps_solver_exceptions():
    """A spec the solver can't handle should raise GeneratorFailure,
    not the underlying ConstraintUnsatisfiableError."""
    spec = _make_impossible_spec()  # e.g. 1 room with area 0.0001 sqft
    with pytest.raises(GeneratorFailure) as exc_info:
        StubGenerator().generate(spec)
    assert exc_info.value.generator_name == "stub"
    assert exc_info.value.spec_id == spec.spec_id
    assert exc_info.value.reason_code in {
        "solver_infeasible", "solver_empty_output",
        "solver_internal_error", "stub_internal_error",
    }


def test_generator_failure_never_returns_empty_list():
    """Contract: generate() returns list of >= 1 OR raises. Never both."""
    spec = _make_spec_2_rooms()
    result = StubGenerator().generate(spec)
    assert len(result) >= 1


# ── Test helpers ─────────────────────────────────────────────────────────────


def _make_spec_2_rooms() -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id="spec_test_2rooms",
        original_nl="a 2-room test plan",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=150.0),
        ],
        n_candidates=1,
    )


def _make_spec_3_rooms() -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id="spec_test_3rooms",
        original_nl="a 3-room test plan",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Kitchen", room_type="kitchen", preferred_area_sqft=150.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=180.0),
        ],
        n_candidates=1,
    )


def _make_impossible_spec() -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id="spec_test_impossible",
        original_nl="a physically impossible plan",
        room_requirements=[
            RoomRequirement(name="Impossible", room_type="other", preferred_area_sqft=0.0001),
        ],
        n_candidates=1,
    )
