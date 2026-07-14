"""Architecture C — LayoutGenerator interface.

The pluggable seam between "intent" (FloorPlanSpec) and "geometry"
(Layout). Three implementations are planned:

- StubGenerator (this DRAFT): wraps the existing kiwisolver + shelf-packer.
  Deterministic. Zero API cost. Used by CI and as the fallback path.
- PromptedGenerator (DRAFT 4): claude-sonnet with heavy prompting and
  JSON-schema-constrained output. Runtime default until FineTuned lands.
- FineTunedGenerator (DRAFT 7): NotImplementedError placeholder.

Selection happens at construction time via GeneratorFactory (which reads
the LAYOUT_GENERATOR env var). Nothing downstream — Layer A/B/C, best-of-N,
API routes — knows or cares which implementation is behind the ABC.

Architecture rule: this module NEVER touches the LLM directly, NEVER
touches geometry directly. It defines a contract; implementations fulfil
it.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from engine.layout import FloorPlanSpec, Layout


class GeneratorFailure(Exception):
    """Raised when a generator cannot produce ANY candidate Layout.

    Distinct from "produced N candidates but all failed Layer A/C" — that
    is a best-of-N concern (DRAFT 6). This exception means the generator
    itself refused or errored at the pre-verification stage: bad spec,
    unresolvable constraints, LLM timeout, etc.
    """

    def __init__(self, message: str, spec_id: str, generator_name: str, reason_code: str):
        super().__init__(message)
        self.spec_id = spec_id
        self.generator_name = generator_name
        self.reason_code = reason_code  # e.g. "solver_timeout", "invalid_spec", "llm_refused"


@dataclass(frozen=True)
class GeneratorMetadata:
    """Provenance stamp attached to every generated Layout.

    Written to Layout.metadata["generator"] on emission. The audit
    manifest (DRAFT 6) reads this to populate the top-level audit block.
    Keep this dataclass frozen so it's hashable and cheap to compare across
    candidates.
    """

    name: str                      # e.g. "stub", "prompted-claude-sonnet-4-5"
    version: str                   # ISO date or semver — bump on behavioural change
    seed: int | None = None        # RNG seed for reproducibility (Stub uses this; Prompted may not)
    extra: dict = field(default_factory=dict)  # freeform, per-implementation


class LayoutGenerator(ABC):
    """Produce N candidate Layouts from a FloorPlanSpec.

    Implementations MUST:
      - Return a list of length between 1 and spec.n_candidates.
      - Populate Layout.metadata["generator"] on every returned Layout.
      - Raise GeneratorFailure (not a bare exception) if zero candidates
        can be produced. Do NOT return an empty list — that's a contract
        violation caught by the tests.
      - Be re-entrant / stateless per call. State that spans calls (LLM
        conversation, warm caches) is a per-implementation concern.

    Implementations SHOULD:
      - Log elapsed_ms for each candidate.
      - Set Layout.metadata["generator_extra"] with any diagnostics useful
        for post-mortem (prompt token counts, solver iteration counts).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable identifier (e.g. 'stub', 'prompted'). Used by
        GeneratorFactory dispatch AND written to the audit manifest."""

    @abstractmethod
    def generate(self, spec: FloorPlanSpec) -> list[Layout]:
        """Return 1..spec.n_candidates Layouts. Raise GeneratorFailure on
        total failure. Never return an empty list."""


class GeneratorFactory:
    """Constructs a LayoutGenerator from the LAYOUT_GENERATOR env var.

    Valid values: "stub" (default), "prompted", "finetuned".
    Unknown values raise ValueError at construction time — fail fast.
    """

    @staticmethod
    def from_env() -> LayoutGenerator:
        kind = os.environ.get("LAYOUT_GENERATOR", "stub").lower()
        return GeneratorFactory.by_name(kind)

    @staticmethod
    def by_name(kind: str) -> LayoutGenerator:
        if kind == "stub":
            # Local import to avoid a cycle with the stub module's own
            # imports of engine.generators.base symbols.
            from engine.generators.stub import StubGenerator
            return StubGenerator()
        if kind == "prompted":
            raise NotImplementedError(
                "PromptedGenerator ships in DRAFT_ARCH_C_4. Set "
                "LAYOUT_GENERATOR=stub for now."
            )
        if kind == "finetuned":
            raise NotImplementedError(
                "FineTunedGenerator ships in DRAFT_ARCH_C_7. Set "
                "LAYOUT_GENERATOR=stub for now."
            )
        raise ValueError(
            f"Unknown LAYOUT_GENERATOR value {kind!r}. "
            f"Valid: 'stub', 'prompted', 'finetuned'."
        )
