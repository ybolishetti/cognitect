"""Deterministic sha256 of a FloorPlanSpec's canonical JSON.

Two identical FloorPlanSpecs (equal pydantic models) MUST hash to the same
string. Two specs that differ in ANY field (including nested Optional
fields set to None vs unset) MUST hash to different strings.

We use pydantic's model_dump(mode="json") with sorted keys — sorted at
every dict level — to eliminate insertion-order effects. The n_candidates
field is EXCLUDED from the hash because it's a runtime knob, not part of
the plan intent (a request for 8 candidates vs 32 candidates is the same
spec, just cheaper vs more thorough).
"""

from __future__ import annotations

import hashlib
import json

from engine.layout import FloorPlanSpec


def spec_hash(spec: FloorPlanSpec) -> str:
    """Return sha256 hex digest of the spec's canonical JSON (64 hex chars)."""
    data = spec.model_dump(mode="json", exclude={"n_candidates"})
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
