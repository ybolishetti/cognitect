"""Tests for engine/pipeline/best_of_n -- the best-of-N orchestrator.

Architecture C, DRAFT 6. Decoupled from real Layer A/C/B behavior via local
_fake_layer_a / _fake_layer_c / _fake_layer_b doubles (monkeypatched into
engine.pipeline.best_of_n's module namespace), except for the two tests that
specifically exercise real verifier behavior (jurisdiction short-circuit,
Layer A exception propagation).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

import engine.pipeline.best_of_n as best_of_n_module
from engine.generators import GeneratorFactory, GeneratorFailure, LayoutGenerator
from engine.layout import (
    FloorPlanSpec,
    Layout,
    Room,
    RoomRequirement,
    SiteConstraints,
    VerifierResult,
    Wall,
)
from engine.pipeline import PipelineFailure, run_best_of_n
from engine.pipeline.scoring import compute_layout_score
from engine.pipeline.spec_hash import spec_hash as compute_spec_hash

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _make_layout(
    plan_id: str,
    room_id: str = "room_1",
    wall_prefix: str = "wall_1",
    x: float = 0.0,
    y: float = 0.0,
    width: float = 10.0,
    height: float = 10.0,
    room_type: str = "living",
    room_name: str = "Living",
    candidate_index: int = 0,
    generator_name: str = "fake_gen",
    generator_version: str = "v1",
) -> Layout:
    x1, y1 = x + width, y + height
    walls = [
        Wall(id=f"{wall_prefix}_s", start=(x, y), end=(x1, y), bounds_rooms=[room_id]),
        Wall(id=f"{wall_prefix}_e", start=(x1, y), end=(x1, y1), bounds_rooms=[room_id]),
        Wall(id=f"{wall_prefix}_n", start=(x1, y1), end=(x, y1), bounds_rooms=[room_id]),
        Wall(id=f"{wall_prefix}_w", start=(x, y1), end=(x, y), bounds_rooms=[room_id]),
    ]
    room = Room(
        id=room_id,
        name=room_name,
        room_type=room_type,
        vertices=[(x, y), (x1, y), (x1, y1), (x, y1), (x, y)],
        area_sqft=width * height,
        boundary_wall_ids=[w.id for w in walls],
    )
    layout = Layout(
        plan_id=plan_id,
        rooms=[room],
        walls=walls,
        extent_x_ft=max(1000.0, x1),
        extent_y_ft=max(1000.0, y1),
    )
    layout.metadata["generator"] = {
        "name": generator_name,
        "version": generator_version,
        "candidate_index": candidate_index,
    }
    return layout


def _make_spec(
    n_candidates: int = 3,
    spec_id: str = "spec_test_bon",
    site_constraints: SiteConstraints | None = None,
    room_requirements: list[RoomRequirement] | None = None,
) -> FloorPlanSpec:
    kwargs = dict(
        spec_id=spec_id,
        original_nl="a test plan",
        room_requirements=room_requirements or [RoomRequirement(name="Living", room_type="living")],
        n_candidates=n_candidates,
    )
    if site_constraints is not None:
        kwargs["site_constraints"] = site_constraints
    return FloorPlanSpec(**kwargs)


class _FakeGenerator(LayoutGenerator):
    def __init__(self, layouts: list[Layout] | None = None, raises: Exception | None = None):
        self._layouts = layouts if layouts is not None else []
        self._raises = raises

    @property
    def name(self) -> str:
        return "fake_gen"

    def generate(self, spec: FloorPlanSpec) -> list[Layout]:
        if self._raises is not None:
            raise self._raises
        return self._layouts


def _verifier_result(verifier_name: str, passed: bool, score: float | None = None) -> VerifierResult:
    return VerifierResult(
        verifier_name=verifier_name,
        passed=passed,
        checks_run=["fake_check"],
        failures=[] if passed else [{"check": "fake_check", "detail": "fake failure", "entity_ids": []}],
        warnings=[],
        score=score,
        elapsed_ms=0.1,
    )


def _fake_layer_a(fail_plan_ids: frozenset[str] = frozenset()):
    def _fn(layout: Layout) -> VerifierResult:
        return _verifier_result("layer_a_geometry", layout.plan_id not in fail_plan_ids)

    return _fn


def _fake_layer_c(fail_plan_ids: frozenset[str] = frozenset()):
    def _fn(layout: Layout, site: SiteConstraints | None = None) -> VerifierResult:
        return _verifier_result("layer_c_code", layout.plan_id not in fail_plan_ids)

    return _fn


def _fake_layer_b(score_map: dict[str, float] | None = None, default_score: float = 1.0):
    score_map = score_map or {}

    def _fn(layout: Layout, spec: FloorPlanSpec | None = None) -> VerifierResult:
        score = score_map.get(layout.plan_id, default_score)
        return _verifier_result("layer_b_structural", True, score=score)

    return _fn


def _patch_fakes(monkeypatch, fail_layer_a=frozenset(), fail_layer_c=frozenset(), layer_b_scores=None):
    monkeypatch.setattr(best_of_n_module, "verify_layer_a", _fake_layer_a(fail_layer_a))
    monkeypatch.setattr(best_of_n_module, "verify_layer_c", _fake_layer_c(fail_layer_c))
    monkeypatch.setattr(best_of_n_module, "verify_layer_b", _fake_layer_b(layer_b_scores))


def _candidates(n=3):
    return [
        _make_layout(f"plan_c{i}", room_id=f"room_c{i}", wall_prefix=f"wall_c{i}", x=i * 30, candidate_index=i)
        for i in range(n)
    ]


# ── Core behavior ────────────────────────────────────────────────────────────


def test_pipeline_returns_generator_output_when_all_pass(monkeypatch):
    _patch_fakes(monkeypatch)
    layouts = _candidates(3)
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    assert len(result.layouts) == 3


def test_pipeline_default_top_k_is_1(monkeypatch):
    _patch_fakes(monkeypatch)
    layouts = _candidates(3)
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts))
    assert len(result.layouts) == 1


def test_pipeline_clamps_top_k_to_survivor_count(monkeypatch):
    _patch_fakes(monkeypatch)
    layouts = _candidates(3)
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=10)
    assert len(result.layouts) == 3


def test_pipeline_layer_a_rejection_drops_candidate(monkeypatch):
    layouts = _candidates(3)
    _patch_fakes(monkeypatch, fail_layer_a=frozenset({layouts[0].plan_id}))
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    assert result.survived_layer_a == 2
    assert result.survived_layer_c == 2
    assert len(result.layouts) == 2


def test_pipeline_layer_c_rejection_drops_candidate(monkeypatch):
    layouts = _candidates(3)
    _patch_fakes(monkeypatch, fail_layer_c=frozenset({layouts[0].plan_id}))
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    assert result.survived_layer_a == 3
    assert result.survived_layer_c == 2


def test_pipeline_all_candidates_fail_raises_pipeline_failure(monkeypatch):
    layouts = _candidates(3)
    fail_ids = frozenset(l.plan_id for l in layouts)
    _patch_fakes(monkeypatch, fail_layer_a=fail_ids)
    spec = _make_spec(n_candidates=3)
    with pytest.raises(PipelineFailure) as excinfo:
        run_best_of_n(spec, generator=_FakeGenerator(layouts))
    err = excinfo.value
    assert err.total_candidates == 3
    assert err.survived_layer_a == 0
    assert err.survived_layer_c == 0
    assert len(err.verifier_results) == 3


def test_generator_failure_propagates():
    failure = GeneratorFailure("boom", spec_id="spec_x", generator_name="fake_gen", reason_code="test_fail")
    spec = _make_spec()
    with pytest.raises(GeneratorFailure):
        run_best_of_n(spec, generator=_FakeGenerator(raises=failure))


def test_pipeline_result_shape(monkeypatch):
    _patch_fakes(monkeypatch)
    layouts = _candidates(3)
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3, include_layer_b=True)
    assert result.total_candidates == 3
    assert result.survived_layer_a == 3
    assert result.survived_layer_c == 3
    assert len(result.all_verifier_results) == 3
    for vrs in result.all_verifier_results.values():
        assert len(vrs) == 3
    assert result.elapsed_ms > 0
    assert result.generator_name == "fake_gen"
    assert result.generator_version == "v1"


def test_pipeline_all_verifier_results_includes_rejected_candidates(monkeypatch):
    layouts = _candidates(3)
    _patch_fakes(monkeypatch, fail_layer_a=frozenset({layouts[0].plan_id}))
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    assert len(result.all_verifier_results) == 3


def test_include_layer_b_false_skips_layer_b_call(monkeypatch):
    layouts = _candidates(3)
    monkeypatch.setattr(best_of_n_module, "verify_layer_a", _fake_layer_a())
    monkeypatch.setattr(best_of_n_module, "verify_layer_c", _fake_layer_c())

    def _boom(layout, spec=None):
        raise AssertionError("verify_layer_b must not be called when include_layer_b=False")

    monkeypatch.setattr(best_of_n_module, "verify_layer_b", _boom)
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3, include_layer_b=False)
    for vrs in result.all_verifier_results.values():
        assert len(vrs) == 2


# ── Audit manifest ───────────────────────────────────────────────────────────


def test_audit_manifest_populated_on_returned_layouts(monkeypatch):
    _patch_fakes(monkeypatch)
    layouts = _candidates(3)
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    for rank, layout in enumerate(result.layouts):
        assert layout.audit is not None
        assert layout.audit.generator == "fake_gen"
        assert len(layout.audit.spec_hash) == 64
        assert len(layout.audit.verifier_results) == 3
        assert layout.audit.selection_rank == rank


def test_audit_manifest_generated_at_is_recent(monkeypatch):
    _patch_fakes(monkeypatch)
    layouts = _candidates(3)
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    now = datetime.now(timezone.utc)
    for layout in result.layouts:
        delta = abs((now - layout.audit.generated_at).total_seconds())
        assert delta < 5.0


def test_audit_manifest_survived_counts_match_result(monkeypatch):
    layouts = _candidates(3)
    _patch_fakes(monkeypatch, fail_layer_a=frozenset({layouts[0].plan_id}))
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    for layout in result.layouts:
        assert layout.audit.survived_layer_a == result.survived_layer_a
        assert layout.audit.survived_layer_c == result.survived_layer_c


def test_audit_manifest_selection_rank_is_zero_indexed_and_ordered(monkeypatch):
    _patch_fakes(monkeypatch)
    layouts = _candidates(3)
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    ranks = [layout.audit.selection_rank for layout in result.layouts]
    assert ranks == [0, 1, 2]


def test_audit_manifest_user_score_populated(monkeypatch):
    _patch_fakes(monkeypatch)
    layouts = _candidates(3)
    spec = _make_spec(n_candidates=3)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    for layout in result.layouts:
        assert isinstance(layout.audit.user_score, float)
        assert 0.0 <= layout.audit.user_score <= 1.0
        _, expected_user_score = compute_layout_score(spec, layout, layer_b_score=None)
        assert layout.audit.user_score == expected_user_score


# ── Ordering ─────────────────────────────────────────────────────────────────


def test_top_k_ordering_is_by_combined_score_desc(monkeypatch):
    _patch_fakes(monkeypatch)
    areas = {0: 190.0, 1: 100.0, 2: 150.0}
    layouts = [
        _make_layout(
            f"plan_c{i}",
            room_id=f"room_c{i}",
            wall_prefix=f"wall_c{i}",
            x=i * 30,
            width=10.0,
            height=areas[i] / 10.0,
            candidate_index=i,
        )
        for i in range(3)
    ]
    req = RoomRequirement(name="Living", room_type="living", preferred_area_sqft=100.0)
    spec = _make_spec(n_candidates=3, room_requirements=[req])
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=3)
    ordering = [layout.metadata["generator"]["candidate_index"] for layout in result.layouts]
    assert ordering == [1, 2, 0]


def test_top_k_ordering_stable_on_tie(monkeypatch):
    _patch_fakes(monkeypatch)
    layouts = _candidates(2)
    spec = _make_spec(n_candidates=2)
    result = run_best_of_n(spec, generator=_FakeGenerator(layouts), top_k=2)
    ordering = [layout.metadata["generator"]["candidate_index"] for layout in result.layouts]
    assert ordering == [0, 1]


# ── Generator metadata / factory ─────────────────────────────────────────────


def test_missing_generator_metadata_falls_back_gracefully(monkeypatch, caplog):
    _patch_fakes(monkeypatch)
    layout = _make_layout("plan_c0", room_id="room_c0", wall_prefix="wall_c0", candidate_index=0)
    layout.metadata.pop("generator")
    spec = _make_spec(n_candidates=1)
    with caplog.at_level(logging.WARNING):
        result = run_best_of_n(spec, generator=_FakeGenerator([layout]), top_k=1)
    assert result.generator_name == "unknown"
    assert result.generator_version == "unknown"
    assert any("generator" in record.message.lower() for record in caplog.records)


def test_default_generator_from_env(monkeypatch):
    _patch_fakes(monkeypatch)
    layout = _make_layout("plan_c0", room_id="room_c0", wall_prefix="wall_c0", candidate_index=0)
    fake_gen = _FakeGenerator([layout])
    monkeypatch.setattr(GeneratorFactory, "from_env", staticmethod(lambda: fake_gen))
    spec = _make_spec(n_candidates=1)
    result = run_best_of_n(spec)
    assert result.generator_name == "fake_gen"


# ── Real Layer A / Layer C behavior (not faked) ─────────────────────────────


def test_layer_c_receives_site_constraints_from_spec():
    layout = _make_layout("plan_c0", room_id="room_c0", wall_prefix="wall_c0", candidate_index=0)
    site = SiteConstraints(jurisdiction="California-2022")
    spec = _make_spec(n_candidates=1, site_constraints=site)
    with pytest.raises(PipelineFailure) as excinfo:
        run_best_of_n(spec, generator=_FakeGenerator([layout]), include_layer_b=False)
    err = excinfo.value
    assert err.survived_layer_c == 0
    layer_c_result = err.verifier_results[0][1]
    assert any("California-2022" in f["detail"] for f in layer_c_result.failures)


def test_pipeline_never_swallows_layer_a_exceptions(monkeypatch):
    layout = _make_layout("plan_c0", room_id="room_c0", wall_prefix="wall_c0", candidate_index=0)

    def _boom(layout):
        raise RuntimeError("layer a bug")

    monkeypatch.setattr(best_of_n_module, "verify_layer_a", _boom)
    spec = _make_spec(n_candidates=1)
    with pytest.raises(RuntimeError, match="layer a bug"):
        run_best_of_n(spec, generator=_FakeGenerator([layout]))


# ── Timing / scoring integration ─────────────────────────────────────────────


def test_pipeline_elapsed_ms_is_positive(monkeypatch):
    _patch_fakes(monkeypatch)
    layout = _make_layout("plan_c0", room_id="room_c0", wall_prefix="wall_c0", candidate_index=0)
    spec = _make_spec(n_candidates=1)
    result = run_best_of_n(spec, generator=_FakeGenerator([layout]))
    assert result.elapsed_ms > 0


def test_pipeline_layer_b_score_flows_into_combined_score(monkeypatch):
    layout_low = _make_layout("plan_low", room_id="room_low", wall_prefix="wall_low", candidate_index=0)
    layout_high = _make_layout("plan_high", room_id="room_high", wall_prefix="wall_high", x=30, candidate_index=1)
    monkeypatch.setattr(best_of_n_module, "verify_layer_a", _fake_layer_a())
    monkeypatch.setattr(best_of_n_module, "verify_layer_c", _fake_layer_c())
    monkeypatch.setattr(
        best_of_n_module, "verify_layer_b", _fake_layer_b({"plan_low": 0.0, "plan_high": 1.0})
    )
    spec = _make_spec(n_candidates=2)
    result = run_best_of_n(spec, generator=_FakeGenerator([layout_low, layout_high]), top_k=2)
    scores = {layout.plan_id: layout.audit.user_score for layout in result.layouts}
    # user_score is frozen (layer_b excluded) and must be identical for both.
    assert scores["plan_low"] == scores["plan_high"] == 1.0
    # But Layer B must still drive the combined score used for ranking.
    ordering = [layout.plan_id for layout in result.layouts]
    assert ordering == ["plan_high", "plan_low"]


def test_pipeline_survivor_ordering_deterministic_across_runs(monkeypatch):
    _patch_fakes(monkeypatch)
    spec = _make_spec(n_candidates=3)
    result_1 = run_best_of_n(spec, generator=_FakeGenerator(_candidates(3)), top_k=3)
    result_2 = run_best_of_n(spec, generator=_FakeGenerator(_candidates(3)), top_k=3)
    ids_1 = [layout.plan_id for layout in result_1.layouts]
    ids_2 = [layout.plan_id for layout in result_2.layouts]
    assert ids_1 == ids_2
    for layout_1, layout_2 in zip(result_1.layouts, result_2.layouts):
        assert layout_1.audit.spec_hash == layout_2.audit.spec_hash
        assert layout_1.audit.user_score == layout_2.audit.user_score
        assert layout_1.audit.selection_rank == layout_2.audit.selection_rank


def test_spec_hash_stable_across_runs(monkeypatch):
    _patch_fakes(monkeypatch)
    spec = _make_spec(n_candidates=1)
    layout_1 = _make_layout("plan_c0", room_id="room_c0", wall_prefix="wall_c0", candidate_index=0)
    layout_2 = _make_layout("plan_c0b", room_id="room_c0b", wall_prefix="wall_c0b", candidate_index=0)
    result_1 = run_best_of_n(spec, generator=_FakeGenerator([layout_1]))
    result_2 = run_best_of_n(spec, generator=_FakeGenerator([layout_2]))
    expected = compute_spec_hash(spec)
    assert result_1.layouts[0].audit.spec_hash == expected
    assert result_2.layouts[0].audit.spec_hash == expected
