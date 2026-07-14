"""Tests for engine/pipeline/spec_hash -- deterministic FloorPlanSpec hashing.

Architecture C, DRAFT 6.
"""

from __future__ import annotations

from engine.layout import FloorPlanSpec, RoomRequirement
from engine.pipeline.spec_hash import spec_hash


def _make_spec(**overrides) -> FloorPlanSpec:
    defaults = dict(
        spec_id="spec_test_hash",
        original_nl="a 2-room test plan",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=150.0),
        ],
        n_candidates=8,
    )
    defaults.update(overrides)
    return FloorPlanSpec(**defaults)


def test_identical_specs_hash_identically():
    spec_a = _make_spec()
    spec_b = _make_spec()
    assert spec_hash(spec_a) == spec_hash(spec_b)


def test_different_original_nl_hashes_differently():
    spec_a = _make_spec(original_nl="a 2-room test plan")
    spec_b = _make_spec(original_nl="a completely different plan")
    assert spec_hash(spec_a) != spec_hash(spec_b)


def test_different_n_candidates_hashes_the_same():
    spec_a = _make_spec(n_candidates=8)
    spec_b = _make_spec(n_candidates=32)
    assert spec_hash(spec_a) == spec_hash(spec_b)


def test_reordering_construction_kwargs_hashes_the_same():
    # Field order at construction time is a Python/kwargs concern only --
    # pydantic always serializes fields in schema-declared order, and
    # spec_hash additionally sorts all dict keys, so this must match
    # test_identical_specs_hash_identically byte for byte.
    spec_a = FloorPlanSpec(
        spec_id="spec_test_hash",
        original_nl="a 2-room test plan",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=150.0),
        ],
        n_candidates=8,
    )
    spec_b = FloorPlanSpec(
        n_candidates=8,
        room_requirements=[
            RoomRequirement(preferred_area_sqft=200.0, room_type="living", name="Living"),
            RoomRequirement(room_type="bedroom", name="Bedroom", preferred_area_sqft=150.0),
        ],
        original_nl="a 2-room test plan",
        spec_id="spec_test_hash",
    )
    assert spec_hash(spec_a) == spec_hash(spec_b)


def test_hash_length_is_64_hex_chars():
    h = spec_hash(_make_spec())
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
