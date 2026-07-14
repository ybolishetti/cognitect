"""Best-of-N pipeline -- the composable loop from FloorPlanSpec to top-K Layouts.

Reads generators (LayoutGenerator ABC), Layer A (hard gate), Layer C (hard
gate), Layer B (advisory scorer), and user-constraint scoring. Returns
Layouts each stamped with a full LayoutAuditManifest.

Architecture rule: this module NEVER touches the LLM directly and NEVER
touches geometry directly. It composes contracts.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from engine.generators import GeneratorFactory, GeneratorFailure, LayoutGenerator
from engine.layout import FloorPlanSpec, Layout, LayoutAuditManifest, VerifierResult
from engine.pipeline.scoring import compute_layout_score
from engine.pipeline.spec_hash import spec_hash
from engine.verifiers import verify_layer_a, verify_layer_c
from engine.verifiers.layer_b import verify_layer_b

logger = logging.getLogger(__name__)


class PipelineFailure(Exception):
    """Raised when best-of-N produces 0 verifiable candidates.

    Distinct from GeneratorFailure: the generator successfully emitted N
    candidates, but every one failed Layer A or Layer C. The audit trail
    (which candidate failed which check) is on `.verifier_results` so the
    caller can diagnose.
    """

    def __init__(
        self,
        message: str,
        spec_id: str,
        total_candidates: int,
        survived_layer_a: int,
        survived_layer_c: int,
        verifier_results: dict[int, list[VerifierResult]],
    ):
        super().__init__(message)
        self.spec_id = spec_id
        self.total_candidates = total_candidates
        self.survived_layer_a = survived_layer_a
        self.survived_layer_c = survived_layer_c
        self.verifier_results = verifier_results


@dataclass
class BestOfNResult:
    """Return value of run_best_of_n.

    `layouts` is the top-K Layouts sorted descending by combined score.
    Every Layout's `.audit` is populated; every Layout's `.metadata["generator"]`
    is preserved (populated by the generator earlier in the pipeline).

    `all_verifier_results` is the FULL run of Layer A/C/B against EVERY
    candidate (including those that failed hard gates), keyed by candidate
    index. Useful for debugging why a generator's output was rejected --
    DRAFT 8's /plan/generate route surfaces this on ?debug=1.
    """

    layouts: list[Layout]  # Top-K, sorted desc by combined_score
    total_candidates: int
    survived_layer_a: int
    survived_layer_c: int
    all_verifier_results: dict[int, list[VerifierResult]]
    elapsed_ms: float
    generator_name: str
    generator_version: str


def _extract_generator_metadata(layout: Layout) -> dict:
    """Pull layout.metadata["generator"] (populated by the generator per the
    DRAFT 3/4 contract). Falls back to {"name": "unknown", "version": "unknown"}
    if missing -- with a warning, because it means a generator didn't honor
    the contract.
    """
    meta = layout.metadata.get("generator")
    if not meta or "name" not in meta:
        logger.warning(
            "Layout %s is missing metadata['generator']['name'] -- generator "
            "did not honor the LayoutGenerator contract",
            getattr(layout, "plan_id", "<unknown>"),
        )
        return {"name": "unknown", "version": "unknown"}
    return {"name": meta["name"], "version": meta.get("version", "unknown")}


def run_best_of_n(
    spec: FloorPlanSpec,
    generator: Optional[LayoutGenerator] = None,
    top_k: int = 1,
    include_layer_b: bool = True,
) -> BestOfNResult:
    """Generate N candidates, filter through hard gates, score, return top-K.

    Args:
      spec:              the FloorPlanSpec (also controls N via spec.n_candidates)
      generator:         optional override; defaults to GeneratorFactory.from_env()
      top_k:             how many top candidates to return (clamped to [1, len(survivors)])
      include_layer_b:   if False, skip Layer B (still runs A + C hard gates).
                         Useful for cheap smoke tests. Default True.

    Returns BestOfNResult. Raises PipelineFailure if 0 candidates survive both
    hard gates -- the caller has no valid plan to return. This is different
    from GeneratorFailure (0 candidates produced): here we DID produce
    candidates, they all failed verification.
    """
    t0 = time.perf_counter()
    generator = generator or GeneratorFactory.from_env()

    # 1. Generate
    try:
        candidates = generator.generate(spec)  # may raise GeneratorFailure -- let it propagate up
    except GeneratorFailure:
        raise  # DO NOT swallow -- the caller distinguishes generator-fail from pipeline-fail

    # 2. Hard gates: Layer A then Layer C (both on every candidate, always -- no short circuit)
    all_verifier_results: dict[int, list[VerifierResult]] = {}
    survivors: list[tuple[int, Layout]] = []  # (original_candidate_index, layout)

    for idx, layout in enumerate(candidates):
        ra = verify_layer_a(layout)
        rc = verify_layer_c(layout, site=spec.site_constraints)
        results = [ra, rc]

        if include_layer_b:
            rb = verify_layer_b(layout, spec)  # Layer B is advisory -- never rejects
            results.append(rb)

        all_verifier_results[idx] = results

        if ra.passed and rc.passed:
            survivors.append((idx, layout))

    survived_layer_a_count = sum(1 for r in all_verifier_results.values() if r[0].passed)
    survived_layer_c_count = sum(1 for r in all_verifier_results.values() if r[0].passed and r[1].passed)

    # 3. Fail loud if nothing survived
    if not survivors:
        raise PipelineFailure(
            f"0/{len(candidates)} candidates survived hard gates "
            f"(A: {survived_layer_a_count} passed, C: {survived_layer_c_count} passed both)",
            spec_id=spec.spec_id,
            total_candidates=len(candidates),
            survived_layer_a=survived_layer_a_count,
            survived_layer_c=survived_layer_c_count,
            verifier_results=all_verifier_results,
        )

    # 4. Score survivors: user constraints + Layer B advisory (if enabled)
    scored: list[tuple[float, int, Layout]] = []
    for idx, layout in survivors:
        verifier_results = all_verifier_results[idx]
        layer_b_score = None
        if include_layer_b:
            # Layer B is always the last result (see order above)
            layer_b_score = verifier_results[-1].score
        combined_score, _user_score = compute_layout_score(spec, layout, layer_b_score)
        scored.append((combined_score, idx, layout))

    # 5. Sort desc by combined score, stable on tie via original candidate index
    scored.sort(key=lambda t: (-t[0], t[1]))

    # 6. Take top-K and stamp audit manifests
    top_k_effective = max(1, min(top_k, len(scored)))
    generator_meta = _extract_generator_metadata(candidates[0])
    h = spec_hash(spec)
    now = datetime.now(timezone.utc)

    top_layouts: list[Layout] = []
    for rank, (combined_score, idx, layout) in enumerate(scored[:top_k_effective]):
        layer_b_score = all_verifier_results[idx][-1].score if include_layer_b else None
        _, user_score = compute_layout_score(spec, layout, layer_b_score)
        layout.audit = LayoutAuditManifest(
            generator=generator_meta["name"],
            generator_version=generator_meta.get("version", "unknown"),
            spec_hash=h,
            verifier_results=all_verifier_results[idx],
            generated_at=now,
            selection_rank=rank,
            total_candidates=len(candidates),
            survived_layer_a=survived_layer_a_count,
            survived_layer_c=survived_layer_c_count,
            user_score=user_score,
        )
        top_layouts.append(layout)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    return BestOfNResult(
        layouts=top_layouts,
        total_candidates=len(candidates),
        survived_layer_a=survived_layer_a_count,
        survived_layer_c=survived_layer_c_count,
        all_verifier_results=all_verifier_results,
        elapsed_ms=elapsed_ms,
        generator_name=generator_meta["name"],
        generator_version=generator_meta.get("version", "unknown"),
    )
