"""PromptedGenerator — LLM-driven Layout generation via Claude Sonnet 4.5.

Contract: implements LayoutGenerator. Returns 1..n_candidates Layouts.
Raises GeneratorFailure if zero candidates can be produced.

Model choice: claude-sonnet-4-5 (heavier reasoning than the haiku-4-5 used
by IntentParser — this task requires whole-plan spatial reasoning, not
per-edit intent extraction).

Cost note: each call is one full Layout emission. Budget: 8 candidates
per generate() at ~$0.03-0.05 each — this is the primary API cost driver
of the runtime path once wired in.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import anthropic
from dotenv import load_dotenv

from engine.generators.base import (
    GeneratorFailure,
    GeneratorMetadata,
    LayoutGenerator,
)
from engine.generators.prompts import (
    LAYOUT_JSON_SCHEMA,
    PROMPTED_SYSTEM_PROMPT,
    PROMPTED_VERSION,
)
from engine.layout import FloorPlanSpec, Layout

load_dotenv()

logger = logging.getLogger(__name__)


class PromptedGenerator(LayoutGenerator):
    """FloorPlanSpec → Layout via Claude Sonnet 4.5 with schema-constrained output."""

    MODEL = "claude-sonnet-4-5"
    MAX_TOKENS = 8192  # a full Layout for a modest house fits well under this
    TIMEOUT_S = 60.0   # per-candidate hard cap

    def __init__(self, api_key: Optional[str] = None, client: Optional[anthropic.Anthropic] = None):
        """
        Args:
            api_key: Overrides COGNITECT_CLAUDE_API_KEY env var (used by tests).
            client:  Injected Anthropic client (used by tests to mock the transport).
                     If provided, api_key is ignored.
        """
        if client is not None:
            self._client = client
        else:
            key = api_key or os.environ.get("COGNITECT_CLAUDE_API_KEY")
            if not key:
                raise GeneratorFailure(
                    "COGNITECT_CLAUDE_API_KEY is not set",
                    spec_id="<no-spec>",
                    generator_name=self.name,
                    reason_code="missing_api_key",
                )
            self._client = anthropic.Anthropic(api_key=key)

    @property
    def name(self) -> str:
        return "prompted"

    def generate(self, spec: FloorPlanSpec) -> list[Layout]:
        candidates: list[Layout] = []
        errors: list[str] = []

        for i in range(spec.n_candidates):
            t0 = time.perf_counter()
            try:
                layout = self._generate_one(spec, candidate_index=i)
            except GeneratorFailure:
                # A single-candidate failure is not a total failure — keep trying.
                errors.append(f"candidate {i}: GeneratorFailure")
                logger.warning("PromptedGenerator candidate %d failed", i, exc_info=True)
                continue
            except Exception as exc:  # noqa: BLE001 — we want to log ALL exceptions per-candidate
                errors.append(f"candidate {i}: {type(exc).__name__}: {exc}")
                logger.warning("PromptedGenerator candidate %d errored", i, exc_info=True)
                continue

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            layout.metadata["generator"] = GeneratorMetadata(
                name=self.name,
                version=PROMPTED_VERSION,
                seed=None,
                extra={
                    "model": self.MODEL,
                    "candidate_index": i,
                    "elapsed_ms": elapsed_ms,
                },
            ).__dict__
            candidates.append(layout)

        if not candidates:
            raise GeneratorFailure(
                f"PromptedGenerator produced 0/{spec.n_candidates} candidates. Errors: {errors}",
                spec_id=spec.spec_id,
                generator_name=self.name,
                reason_code="all_candidates_failed",
            )

        return candidates

    # ── internal ─────────────────────────────────────────────────────────

    def _generate_one(self, spec: FloorPlanSpec, candidate_index: int) -> Layout:
        """Ask Claude for ONE Layout. Parse and validate."""
        user_message = self._build_user_message(spec, candidate_index)

        try:
            response = self._client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                system=PROMPTED_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                tools=[{
                    "name": "emit_layout",
                    "description": (
                        "Emit a complete, validated Layout for the given "
                        "FloorPlanSpec. This is the only way to return a "
                        "layout — do not respond with prose."
                    ),
                    "input_schema": LAYOUT_JSON_SCHEMA,
                }],
                tool_choice={"type": "tool", "name": "emit_layout"},
                timeout=self.TIMEOUT_S,
            )
        except anthropic.RateLimitError as exc:
            # NOTE: RateLimitError subclasses APIStatusError in the SDK, so
            # this branch MUST come first or it's unreachable.
            raise GeneratorFailure(
                f"Anthropic rate limit hit: {exc}",
                spec_id=spec.spec_id,
                generator_name=self.name,
                reason_code="rate_limit",
            ) from exc
        except anthropic.APIStatusError as exc:
            raise GeneratorFailure(
                f"Anthropic API error: {exc}",
                spec_id=spec.spec_id,
                generator_name=self.name,
                reason_code="api_status_error",
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise GeneratorFailure(
                f"Anthropic connection error: {exc}",
                spec_id=spec.spec_id,
                generator_name=self.name,
                reason_code="api_connection_error",
            ) from exc

        # Extract the single tool_use block.
        tool_uses = [b for b in response.content if b.type == "tool_use" and b.name == "emit_layout"]
        if not tool_uses:
            raise GeneratorFailure(
                "Model returned no emit_layout tool_use block",
                spec_id=spec.spec_id,
                generator_name=self.name,
                reason_code="no_tool_use",
            )
        raw = tool_uses[0].input

        # Model may emit the escape hatch {"error": "cannot_generate", "reason": ...}
        # If so, translate to GeneratorFailure — but only if it's clearly the
        # escape hatch and NOT a valid Layout (Layout has no "error" field).
        if isinstance(raw, dict) and set(raw.keys()) == {"error", "reason"}:
            raise GeneratorFailure(
                f"Model refused: {raw.get('reason')}",
                spec_id=spec.spec_id,
                generator_name=self.name,
                reason_code="model_refused",
            )

        try:
            layout = Layout.model_validate(raw)
        except Exception as exc:  # pydantic ValidationError or nested
            raise GeneratorFailure(
                f"Model output failed Layout schema validation: {exc}",
                spec_id=spec.spec_id,
                generator_name=self.name,
                reason_code="schema_validation_failed",
            ) from exc

        return layout

    def _build_user_message(self, spec: FloorPlanSpec, candidate_index: int) -> str:
        """Serialize the FloorPlanSpec to a user message.

        Include the candidate_index in the message so the model gets a
        (weak) diversification signal without us changing temperature. The
        strong diversity signal comes from best-of-N running N independent
        calls; this just prevents the model from returning byte-identical
        outputs on trivial specs.
        """
        spec_json = spec.model_dump_json(indent=2)
        return (
            f"# Candidate {candidate_index + 1} of {spec.n_candidates}\n\n"
            f"## Natural language brief\n\n{spec.original_nl}\n\n"
            f"## Structured spec\n\n```json\n{spec_json}\n```\n\n"
            f"Emit the Layout via the emit_layout tool. This is candidate "
            f"{candidate_index + 1}; if you would generate the same plan "
            f"as a previous candidate, vary the aspect ratios, orientations, "
            f"or interior wall placement while keeping all hard constraints "
            f"satisfied."
        )
