"""
Intent Parser — NL → FloorPlanOp via Claude API.

SLA: <2s (uses claude-3-5-haiku-20241022 for speed and cost efficiency).
Architecture rule: this module ONLY produces structured ops. It never touches geometry.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import anthropic
from dotenv import load_dotenv

from .schemas import FloorPlanOp, FloorPlanOpBatch, FloorPlanState

load_dotenv()

logger = logging.getLogger(__name__)

# ── Prompt ──────────────────────────────────────────────────────────────────

FLOOR_PLAN_OP_SCHEMA = """
## FloorPlanOp schema

{
  "op_type": one of ["add_room","remove_room","resize_room","move_room","add_connection","set_constraint"],
  "target_room_id": string or null,
  "room_spec": RoomSpec or null,
  "constraint_spec": ConstraintSpec or null,
  "connection_spec": ConnectionSpec or null,
  "metadata": {"confidence": 0.0-1.0, "raw_nl": "..."}
}

## RoomSpec schema
{
  "name": string,
  "room_type": one of ["bedroom","bathroom","kitchen","living","dining","hallway","office","garage","other"],
  "area_sqft": float or null,
  "min_area_sqft": float or null,
  "max_area_sqft": float or null,
  "aspect_ratio": float or null,
  "adjacency_requirements": [string, ...]
}

## ConstraintSpec schema
{
  "constraint_type": one of ["min_area","max_area","adjacency","separation","aspect_ratio","orientation"],
  "room_id": string,
  "value": float or string,
  "strength": one of ["required","strong","medium","weak"]
}

## ConnectionSpec schema
{
  "room_a_id": string,
  "room_b_id": string,
  "connection_type": one of ["door","archway","wall_opening"],
  "width_ft": float or null
}
"""

SYSTEM_PROMPT_BATCH = f"""You are the intent extraction engine for Cognitect, an architectural CAD system.

Your job: given a natural-language instruction about a floor plan, decompose it into ONE OR MORE
atomic FloorPlanOp operations and return them as a JSON object with an "ops" array.

Return format:
{{
  "ops": [ <FloorPlanOp>, <FloorPlanOp>, ... ],
  "batch_description": "<one sentence describing what was requested>",
  "metadata": {{ "confidence": 0.0-1.0 }}
}}

{FLOOR_PLAN_OP_SCHEMA}

## Rules

1. Return ONLY the JSON object. No markdown, no explanation.
2. Decompose complex requests into multiple ops. Examples:
   - "Add 3 bedrooms" → 3 separate add_room ops (bedroom_1, bedroom_2, bedroom_3)
   - "Add a master suite" → add_room(master_bedroom, 200sqft) + add_room(master_bathroom, 80sqft, adjacent to master_bedroom)
   - "Open concept kitchen/living/dining" → 3 add_room ops with adjacency_requirements set on each
   - "Make the kitchen bigger to fit the dining room" → resize_room(kitchen) + set_constraint(adjacency, kitchen↔dining)
3. For "X next to Y" / "X adjacent to Y" → encode adjacency_requirements in the RoomSpec of X
4. For resize requests that affect another room: emit the resize op AND a set_constraint adjacency op
5. For named architectural bundles, expand them:
   - "master suite" → master bedroom (200sqft) + en-suite bathroom (80sqft, adjacent)
   - "open concept" → living (300sqft) + kitchen (200sqft, adjacent) + dining (150sqft, adjacent to both)
   - "mudroom entry" → hallway/entry (80sqft)
   - "two-car garage" → garage (440sqft)
6. Ops must be ordered so dependencies come first: add rooms before connecting them
7. When resizing a room "to account for" another room, interpret this as: the rooms need adjacency, and
   the resized room's new area should create a visually proportionate pair (e.g. if dining is 150sqft,
   kitchen adjacent to it should be ~180-250sqft)
8. op_type must exactly match what the user asked for:
   - "Add a room" → add_room
   - "Remove / delete a room" → remove_room
   - "Make bigger / resize / change area" → resize_room or set_constraint
   - "Move a room" → move_room
   - "Add a door / connection" → add_connection
   - "Require adjacency / set a rule" → set_constraint
9. For add_room: always populate room_spec. Leave target_room_id null.
10. For remove_room / resize_room / move_room: populate target_room_id using an existing room_id
    from the current state. NEVER invent room IDs that don't exist.
11. For add_connection: populate connection_spec with both room IDs from existing state.
12. Room IDs are slugified names: "master_bedroom", "living_room", "kitchen", etc.
    When adding a new room, derive the ID from the name. When referencing existing rooms,
    use the exact ID from the provided state.
13. For ambiguous instructions, choose the most likely interpretation and set
    metadata.confidence < 0.7.
14. "adjacency" means sharing a wall; encode it in adjacency_requirements of the RoomSpec
    OR as a set_constraint with constraint_type="adjacency".
15. area_sqft is a soft target. Use min_area_sqft / max_area_sqft for hard bounds.
16. If the user says "about X sqft" → area_sqft=X, strength="medium"
    If the user says "at least X sqft" → min_area_sqft=X, strength="strong"
    If the user says "exactly X sqft" → area_sqft=X, strength="required"
"""

USER_PROMPT_TEMPLATE_BATCH = """Current floor plan state:
{state_json}

User instruction:
{nl_input}

Decompose this into one or more FloorPlanOps and return the JSON batch:"""


# ── Custom exceptions ────────────────────────────────────────────────────────

class IntentParseError(Exception):
    """Base class for intent parser errors."""


class APIError(IntentParseError):
    """Claude API call failed."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class SchemaValidationError(IntentParseError):
    """Claude returned JSON but it doesn't match FloorPlanOp schema."""

    def __init__(self, message: str, raw_response: str = ""):
        super().__init__(message)
        self.raw_response = raw_response


class SLAViolationError(IntentParseError):
    """Response took longer than the 2s SLA target."""

    def __init__(self, elapsed_s: float):
        super().__init__(f"Intent parse took {elapsed_s:.2f}s (SLA: 2s)")
        self.elapsed_s = elapsed_s


# ── Parser ───────────────────────────────────────────────────────────────────

class IntentParser:
    """
    Parses natural-language floor plan instructions into structured FloorPlanOp objects.

    SLA target: <2s per call.
    Model: claude-haiku-4-5 (fast + cheap; intent extraction doesn't need Sonnet).
    """

    MODEL = "claude-haiku-4-5"
    MAX_TOKENS = 1024
    SLA_SECONDS = 2.0

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.environ.get("COGNITECT_CLAUDE_API_KEY")
        if not key:
            raise ValueError(
                "COGNITECT_CLAUDE_API_KEY not set. "
                "Export it or pass api_key= to IntentParser()."
            )
        self._client = anthropic.Anthropic(api_key=key)

    def parse(self, nl_input: str, plan_state: FloorPlanState) -> FloorPlanOp:
        """
        Parse a natural-language instruction into a FloorPlanOp.

        Backward-compatible shim: returns only the first op from parse_batch().

        Args:
            nl_input: The user's NL instruction, e.g. "Add a master bedroom of 200 sqft"
            plan_state: Current floor plan state (used for room ID resolution)

        Returns:
            Validated FloorPlanOp Pydantic model (first op in the batch).

        Raises:
            APIError: If the Claude API call fails.
            SchemaValidationError: If the response doesn't parse into FloorPlanOp.
            SLAViolationError: If the call exceeds 2s (warning-level; still returns result).
        """
        batch = self.parse_batch(nl_input, plan_state)
        if not batch.ops:
            raise SchemaValidationError("Batch contains no ops")
        return batch.ops[0]

    def parse_batch(self, nl_input: str, plan_state: FloorPlanState) -> FloorPlanOpBatch:
        """
        Parse a natural-language instruction into one or more FloorPlanOps.

        Args:
            nl_input: The user's NL instruction
            plan_state: Current floor plan state (used for room ID resolution)

        Returns:
            Validated FloorPlanOpBatch with 1..N ops.

        Raises:
            APIError: If the Claude API call fails.
            SchemaValidationError: If the response doesn't parse into FloorPlanOpBatch.
        """
        t0 = time.perf_counter()

        state_summary = self._summarize_state(plan_state)
        user_message = USER_PROMPT_TEMPLATE_BATCH.format(
            state_json=state_summary,
            nl_input=nl_input.strip(),
        )

        try:
            response = self._client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                system=SYSTEM_PROMPT_BATCH,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIStatusError as exc:
            raise APIError(str(exc), status_code=exc.status_code) from exc
        except anthropic.APIConnectionError as exc:
            raise APIError(f"Connection error: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise APIError("Rate limit exceeded", status_code=429) from exc

        elapsed = time.perf_counter() - t0
        if elapsed > self.SLA_SECONDS:
            logger.warning("SLA violation: intent parse took %.2fs (target: %.1fs)", elapsed, self.SLA_SECONDS)

        raw_text = response.content[0].text.strip()
        logger.debug("Raw intent response (%.2fs): %s", elapsed, raw_text[:200])

        return self._parse_batch_response(raw_text, nl_input)

    def _parse_batch_response(self, raw_text: str, nl_input: str) -> FloorPlanOpBatch:
        """Parse and validate the raw text response from Claude."""
        text = raw_text
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SchemaValidationError(
                f"Claude returned non-JSON: {exc}", raw_response=raw_text
            ) from exc

        # Backward compat: Claude returned a single FloorPlanOp (no "ops" array)
        if "ops" not in data and "op_type" in data:
            if "metadata" not in data or data["metadata"] is None:
                data["metadata"] = {}
            data["metadata"].setdefault("raw_nl", nl_input)
            try:
                op = FloorPlanOp.model_validate(data)
            except Exception as exc:
                raise SchemaValidationError(
                    f"Response doesn't match FloorPlanOp schema: {exc}",
                    raw_response=raw_text,
                ) from exc
            return FloorPlanOpBatch(
                ops=[op],
                batch_description=op.metadata.get("raw_nl", nl_input),
                metadata={"confidence": op.metadata.get("confidence", 0.0)},
            )

        # Inject raw_nl into each op's metadata if missing
        for op_data in data.get("ops", []):
            if "metadata" not in op_data or op_data["metadata"] is None:
                op_data["metadata"] = {}
            op_data["metadata"].setdefault("raw_nl", nl_input)

        try:
            batch = FloorPlanOpBatch.model_validate(data)
        except Exception as exc:
            raise SchemaValidationError(
                f"Response doesn't match FloorPlanOpBatch schema: {exc}",
                raw_response=raw_text,
            ) from exc

        if not batch.ops:
            raise SchemaValidationError(
                "Batch must contain at least one op", raw_response=raw_text
            )

        return batch

    def _summarize_state(self, plan_state: FloorPlanState) -> str:
        """Produce a JSON summary of the current plan state for the prompt."""
        total_area = sum(
            spec.area_sqft or 0 for spec in plan_state.rooms.values()
        )
        summary = {
            "plan_id": plan_state.plan_id,
            "total_area_sqft": round(total_area, 1),
            "room_count": len(plan_state.rooms),
            "rooms": {
                room_id: {
                    "name": spec.name,
                    "type": spec.room_type,
                    "area_sqft": spec.area_sqft,
                    "adjacency_requirements": spec.adjacency_requirements or [],
                }
                for room_id, spec in plan_state.rooms.items()
            },
            "connections": [
                {
                    "room_a": conn.room_a_id,
                    "room_b": conn.room_b_id,
                    "type": conn.connection_type,
                }
                for conn in plan_state.connections
            ],
            "constraints": [
                {
                    "type": c.constraint_type,
                    "room": c.room_id,
                    "value": c.value,
                    "strength": c.strength,
                }
                for c in plan_state.constraints
            ],
        }
        return json.dumps(summary, indent=2)
