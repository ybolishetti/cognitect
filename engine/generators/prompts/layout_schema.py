"""JSON Schema for Layout, derived from the Pydantic model.

Used as the tool-input schema in PromptedGenerator's Anthropic tool call.
Deriving from pydantic guarantees the LLM's contract stays in sync with
the Python types. If Layout changes, this changes automatically.
"""

from __future__ import annotations

from engine.layout import Layout


def build_layout_json_schema() -> dict:
    """Return the JSON Schema for a Layout object.

    Pydantic v2 exposes `.model_json_schema()` on any BaseModel. We use
    `mode="validation"` (NOT the default "serialization") because
    "serialization" mode includes `@computed_field`s like `Wall.length_ft`
    in `properties` and `required` — the model would then be forced to
    compute and emit a redundant wall length on every wall for no benefit
    (it's derived from `start`/`end`, and pydantic silently drops unknown
    constructor args for it anyway). "validation" mode reflects exactly
    what a caller must supply to construct the model, which is what we
    want the LLM to emit.
    """
    schema = Layout.model_json_schema(mode="validation")
    # Anthropic tolerates `title` but stripping it reduces token count.
    _strip_titles(schema)
    return schema


def _strip_titles(node: object) -> None:
    if isinstance(node, dict):
        node.pop("title", None)
        for v in node.values():
            _strip_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_titles(v)


LAYOUT_JSON_SCHEMA = build_layout_json_schema()
