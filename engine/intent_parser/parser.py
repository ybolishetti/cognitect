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

from .schemas import FloorPlanOp, FloorPlanState

load_dotenv()

logger = logging.getLogger(__name__)

# ── Prompt ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the intent extraction engine for Cognitect, an architectural CAD system.

Your job: given a natural-language instruction about a floor plan, extract ONE atomic operation
and return it as valid JSON matching the FloorPlanOp schema below. Nothing else — no explanation,
no markdown fences, no commentary. Just the JSON object.

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

## Rules

1. Return ONLY the JSON object. No markdown, no explanation.
2. op_type must exactly match what the user asked for:
   - "Add a room" → add_room
   - "Remove / delete a room" → remove_room
   - "Make bigger / resize / change area" → resize_room or set_constraint
   - "Move a room" → move_room
   - "Add a door / connection" → add_connection
   - "Require adjacency / set a rule" → set_constraint
3. For add_room: always populate room_spec. Leave target_room_id null.
4. For remove_room / resize_room / move_room: populate target_room_id using an existing room_id
   from the current state. NEVER invent room IDs that don't exist.
5. For add_connection: populate connection_spec with both room IDs from existing state.
6. Room IDs are slugified names: "master_bedroom", "living_room", "kitchen", etc.
   When adding a new room, derive the ID from the name. When referencing existing rooms,
   use the exact ID from the provided state.
7. For ambiguous instructions, choose the most likely interpretation and set
   metadata.confidence < 0.7.
8. "adjacency" means sharing a wall; encode it in adjacency_requirements of the RoomSpec
   OR as a set_constraint with constraint_type="adjacency".
9. area_sqft is a soft target. Use min_area_sqft / max_area_sqft for hard bounds.
10. If the user says "about X sqft" → area_sqft=X, strength="medium"
    If the user says "at least X sqft" → min_area_sqft=X, strength="strong"
    If the user says "exactly X sqft" → area_sqft=X, strength="required"
"""

USER_PROMPT_TEMPLATE = """Current floor plan state:
{state_json}

User instruction:
{nl_input}

Return the FloorPlanOp JSON:"""


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

        Args:
            nl_input: The user's NL instruction, e.g. "Add a master bedroom of 200 sqft"
            plan_state: Current floor plan state (used for room ID resolution)

        Returns:
            Validated FloorPlanOp Pydantic model.

        Raises:
            APIError: If the Claude API call fails.
            SchemaValidationError: If the response doesn't parse into FloorPlanOp.
            SLAViolationError: If the call exceeds 2s (warning-level; still returns result).
        """
        t0 = time.perf_counter()

        state_summary = self._summarize_state(plan_state)
        user_message = USER_PROMPT_TEMPLATE.format(
            state_json=state_summary,
            nl_input=nl_input.strip(),
        )

        try:
            response = self._client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                system=SYSTEM_PROMPT,
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

        return self._parse_response(raw_text, nl_input)

    def _parse_response(self, raw_text: str, nl_input: str) -> FloorPlanOp:
        """Parse and validate the raw text response from Claude."""
        # Strip markdown code fences if Claude misbehaves
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

        # Inject raw_nl into metadata if missing
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

        return op

    def _summarize_state(self, plan_state: FloorPlanState) -> str:
        """
        Produce a compact JSON summary of the current plan state for the prompt.
        Only includes room IDs, types, and areas — not full coordinate matrices.
        """
        summary = {
            "plan_id": plan_state.plan_id,
            "rooms": {
                room_id: {
                    "name": spec.name,
                    "type": spec.room_type,
                    "area_sqft": spec.area_sqft,
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
        }
        return json.dumps(summary, indent=2)
