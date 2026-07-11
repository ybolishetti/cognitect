"""Error types for the Architecture C generate flow.

Schema-level violations (SchemaViolation) are distinct from verifier-level
rejections that happen after best-of-N filtering (GenerationFailure).
"""

from __future__ import annotations


class GenerationFailure(Exception):
    """Raised when best-of-N produces zero valid Layouts.

    Do NOT fall back to invalid geometry. Callers must handle this and either
    re-prompt the user, retry with more candidates, or surface the error.
    """

    def __init__(
        self,
        spec_id: str,
        total_candidates: int,
        layer_a_failures: int,
        layer_c_failures: int,
        details: list[dict],
    ):
        self.spec_id = spec_id
        self.total_candidates = total_candidates
        self.layer_a_failures = layer_a_failures
        self.layer_c_failures = layer_c_failures
        self.details = details
        super().__init__(
            f"Generation failed for spec {spec_id}: "
            f"{layer_a_failures}/{total_candidates} failed Layer A, "
            f"{layer_c_failures}/{total_candidates} failed Layer C"
        )


class SchemaViolation(ValueError):
    """Raised when a Layout fails schema-level validation (before verifiers)."""

    pass
