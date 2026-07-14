"""Tests for engine/verifiers/layer_c — the code-compliance hard gate
(Architecture C, DRAFT 5).

Covers the verify_layer_c orchestrator and rule registry in isolation
from the real IRC-2021 rules (using fake CodeRule subclasses), so these
tests don't churn every time a rule's behavior changes. Per-rule
behavior lives in tests/test_layer_c_rules_<rule_id>.py, which import the
fixture helpers defined here.
"""

from __future__ import annotations

import pytest

from engine.layout import Layout, Room, SiteConstraints, Wall
from engine.verifiers import (
    IRC_2021_RULES,
    CodeCheckContext,
    CodeRule,
    lookup_rule,
    verify_layer_c,
)

# ── Fixture helpers (shared with tests/test_layer_c_rules_*.py) ─────────────


def _rect_vertices(
    x0: float = 0.0, y0: float = 0.0, x1: float = 10.0, y1: float = 10.0
) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def make_room(
    room_id: str,
    room_type: str,
    wall_ids: list[str],
    x0: float = 0.0,
    y0: float = 0.0,
    x1: float = 10.0,
    y1: float = 10.0,
    name: str | None = None,
    ceiling_height_ft: float = 9.0,
) -> Room:
    return Room(
        id=room_id,
        name=name or room_id,
        room_type=room_type,
        vertices=_rect_vertices(x0, y0, x1, y1),
        area_sqft=(x1 - x0) * (y1 - y0),
        boundary_wall_ids=wall_ids,
        ceiling_height_ft=ceiling_height_ft,
    )


def make_rect_walls(
    room_id: str,
    prefix: str,
    x0: float = 0.0,
    y0: float = 0.0,
    x1: float = 10.0,
    y1: float = 10.0,
) -> list[Wall]:
    """Four exterior walls (bounds_rooms=[room_id]) bounding a rectangle."""
    return [
        Wall(id=f"wall_{prefix}_s", start=(x0, y0), end=(x1, y0), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_e", start=(x1, y0), end=(x1, y1), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_n", start=(x1, y1), end=(x0, y1), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_w", start=(x0, y1), end=(x0, y0), bounds_rooms=[room_id]),
    ]


def make_layout(
    rooms: list[Room],
    walls: list[Wall],
    openings=None,
    extent_x_ft: float = 1000.0,
    extent_y_ft: float = 1000.0,
    plan_id: str = "plan_test1",
) -> Layout:
    return Layout(
        plan_id=plan_id,
        rooms=rooms,
        walls=walls,
        openings=openings or [],
        extent_x_ft=extent_x_ft,
        extent_y_ft=extent_y_ft,
    )


def _simple_bedroom_layout() -> Layout:
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bedroom", [w.id for w in walls])
    return make_layout([room], walls)


class _FakeRule(CodeRule):
    """A CodeRule whose behavior is fully controlled by the test."""

    def __init__(self, rule_id, citation="fake citation", failures=None, raises=False):
        self.rule_id = rule_id
        self.citation = citation
        self._failures = failures or []
        self._raises = raises

    def check(self, layout, ctx):
        if self._raises:
            raise RuntimeError("boom")
        return list(self._failures)


# ── Orchestrator tests ───────────────────────────────────────────────────────


def test_empty_registry_produces_passing_result():
    result = verify_layer_c(_simple_bedroom_layout(), rules=[])
    assert result.passed is True
    assert result.checks_run == []
    assert result.failures == []
    assert result.elapsed_ms >= 0
    assert result.verifier_name == "layer_c_code"


def test_verifier_result_shape_matches_layer_a():
    result = verify_layer_c(_simple_bedroom_layout(), rules=[])
    assert result.warnings == []
    assert result.score is None
    assert result.verifier_name == "layer_c_code"


def test_single_passing_rule():
    result = verify_layer_c(_simple_bedroom_layout(), rules=[_FakeRule("fake_pass")])
    assert result.passed is True
    assert result.checks_run == ["fake_pass"]


def test_single_failing_rule():
    rule = _FakeRule(
        "fake_fail",
        failures=[{"check": "fake_fail", "citation": "c", "detail": "d", "entity_ids": ["room_1"]}],
    )
    result = verify_layer_c(_simple_bedroom_layout(), rules=[rule])
    assert result.passed is False
    assert len(result.failures) == 1
    assert result.checks_run == ["fake_fail"]


def test_multiple_rules_all_run():
    r1 = _FakeRule("r1")
    r2 = _FakeRule(
        "r2", failures=[{"check": "r2", "citation": "c", "detail": "d", "entity_ids": []}]
    )
    r3 = _FakeRule("r3")
    result = verify_layer_c(_simple_bedroom_layout(), rules=[r1, r2, r3])
    assert result.checks_run == ["r1", "r2", "r3"]
    assert len(result.failures) == 1


def test_rule_exception_becomes_failure_not_raise():
    result = verify_layer_c(_simple_bedroom_layout(), rules=[_FakeRule("boom_rule", raises=True)])
    assert result.passed is False
    assert len(result.failures) == 1
    assert "rule bug" in result.failures[0]["detail"]


def test_deterministic_failure_ordering():
    r_b = _FakeRule(
        "check_b",
        failures=[
            {"check": "check_b", "citation": "c", "detail": "zzz", "entity_ids": ["room_9", "room_1"]},
            {"check": "check_b", "citation": "c", "detail": "aaa", "entity_ids": ["room_1"]},
        ],
    )
    r_a = _FakeRule(
        "check_a", failures=[{"check": "check_a", "citation": "c", "detail": "m", "entity_ids": []}]
    )
    result = verify_layer_c(_simple_bedroom_layout(), rules=[r_b, r_a])
    assert [f["check"] for f in result.failures] == ["check_a", "check_b", "check_b"]
    assert result.failures[1]["detail"] == "aaa"
    assert result.failures[2]["detail"] == "zzz"


def test_unsupported_jurisdiction_short_circuits():
    rule = _FakeRule("would_raise_if_called", raises=True)
    site = SiteConstraints(jurisdiction="California-2022")
    result = verify_layer_c(_simple_bedroom_layout(), rules=[rule], site=site)
    assert result.passed is False
    assert len(result.failures) == 1
    assert result.failures[0]["check"] == "unsupported_jurisdiction"


def test_default_rules_is_irc_2021_rules():
    result = verify_layer_c(_simple_bedroom_layout())
    assert result.checks_run == [r.rule_id for r in IRC_2021_RULES]


def test_lookup_rule_finds_by_id_or_raises():
    rule = lookup_rule("irc_r310_1")
    assert rule.rule_id == "irc_r310_1"
    with pytest.raises(KeyError):
        lookup_rule("nonexistent")
