"""Per-Layout provenance record — the audit manifest attached by best-of-N.

This is the moat: every returned Layout ships with a reproducible record of
which generator produced it and which verifiers it passed. See
ARCHITECTURE_C.md §"Audit manifest is the moat".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class VerifierResult(BaseModel):
    """Result of running a single verifier layer against a Layout."""

    verifier_name: Literal["layer_a_geometry", "layer_b_structural", "layer_c_code"]
    passed: bool  # For Layer B: always True (advisory); use `warnings` for issues
    checks_run: list[str] = Field(default_factory=list)
    failures: list[dict] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    score: Optional[float] = Field(None, ge=0.0, le=1.0)  # For Layer B ranking
    elapsed_ms: float = Field(..., ge=0)


class LayoutAuditManifest(BaseModel):
    """Provenance record attached to every Layout returned by best-of-N."""

    generator: str  # e.g. "prompted-claude-sonnet-4-5"
    generator_version: str  # date string or model hash
    spec_hash: str  # sha256 of FloorPlanSpec JSON
    verifier_results: list[VerifierResult]
    generated_at: datetime
    selection_rank: int  # rank in best-of-N (0 = top)
    total_candidates: int  # how many candidates were generated before filtering
    survived_layer_a: int
    survived_layer_c: int
    metadata: dict = Field(default_factory=dict)
