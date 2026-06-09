# DRAFT — Phase 5: Architect Intelligence

**Goal:** Upgrade Cognitect from a single-op drawing tool to a design-intelligent engine.
Two focused changes to the intent parser layer — nothing else changes.

---

## What to Build

### Change 1 — Multi-Op Parsing (`engine/intent_parser/`)

The parser currently returns a single `FloorPlanOp`. Change it to return a `list[FloorPlanOp]`.

**New return type:** `FloorPlanOpBatch`

```python
# engine/intent_parser/schemas.py — add this model

class FloorPlanOpBatch(BaseModel):
    """One or more ops extracted from a single NL instruction."""
    ops: list[FloorPlanOp]
    batch_description: str  # e.g. "3-bedroom house with open kitchen/living"
    metadata: dict = {}
```

**Update `IntentParser.parse()`** to call a new method `parse_batch()` that returns `FloorPlanOpBatch`.
Keep the old `parse()` signature for backward compatibility — it should call `parse_batch()` and return
`batch.ops[0]` (first op only). All new callers should use `parse_batch()`.

**New system prompt for `parse_batch()`** — replace the single-op system prompt with this:

```
You are the intent extraction engine for Cognitect, an architectural CAD system.

Your job: given a natural-language instruction about a floor plan, decompose it into ONE OR MORE
atomic FloorPlanOp operations and return them as a JSON object with an "ops" array.

Return format:
{
  "ops": [ <FloorPlanOp>, <FloorPlanOp>, ... ],
  "batch_description": "<one sentence describing what was requested>",
  "metadata": { "confidence": 0.0-1.0 }
}

## FloorPlanOp schema (same as before — each op in the array must match this)
[... same FloorPlanOp schema as existing SYSTEM_PROMPT ...]

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
8. All other FloorPlanOp rules from single-op parsing still apply (room IDs, constraint strengths, etc.)
```

**New `USER_PROMPT_TEMPLATE` for batch:**

```python
USER_PROMPT_TEMPLATE_BATCH = """Current floor plan state:
{state_json}

User instruction:
{nl_input}

Decompose this into one or more FloorPlanOps and return the JSON batch:"""
```

---

### Change 2 — Architect-Mode State Summary (`engine/intent_parser/parser.py`)

Update `_summarize_state()` to include richer context so Claude can make spatially-aware decisions.
The current summary only sends room IDs + areas. Expand it to include:

```python
def _summarize_state(self, plan_state: FloorPlanState) -> str:
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
```

---

### Change 3 — `PlanManager.instruct()` uses `parse_batch()`

Update `PlanManager.instruct()` to call `parse_batch()` and apply all ops in the batch sequentially:

```python
def instruct(self, nl_input: str) -> list[FloorPlanOp]:
    """
    Parse a natural-language instruction and apply ALL resulting ops.

    Returns:
        List of FloorPlanOps that were applied (1 or more).
    """
    batch = self._parser.parse_batch(nl_input, self._state)
    applied = []
    for op in batch.ops:
        self._apply_op(op)
        self._history.append(op)
        applied.append(op)
    logger.info(
        "Applied %d op(s) from batch '%s' | rooms=%d | v=%d",
        len(applied), batch.batch_description,
        len(self._state.rooms), self._state.version,
    )
    return applied
```

---

### Change 4 — Update API route `/plan/{id}/instruct`

The `InstructResponse` model needs to handle multiple ops. Update `api/routes/plan.py`:

```python
class InstructResponse(BaseModel):
    plan_id: str
    ops_applied: int          # NEW — was op_type (single)
    op_types: list[str]       # NEW — list of all ops applied
    room_count: int
    version: int
    message: str
```

Update the route handler:

```python
@router.post("/{plan_id}/instruct", response_model=InstructResponse)
async def instruct(plan_id: str, request: InstructRequest):
    manager = _get_plan(plan_id)
    try:
        ops = manager.instruct(request.instruction)
    except SchemaValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Claude returned invalid schema: {exc}. Raw: {exc.raw_response[:200]}")
    except IntentParseError as exc:
        raise HTTPException(status_code=502, detail=f"Intent parse failed: {exc}")
    except UnknownRoomError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except PlanManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    op_types = [op.op_type for op in ops]
    return InstructResponse(
        plan_id=plan_id,
        ops_applied=len(ops),
        op_types=op_types,
        room_count=manager.room_count,
        version=manager.state.version,
        message=f"Applied {len(ops)} op(s): {', '.join(op_types)}. Plan now has {manager.room_count} room(s).",
    )
```

---

## Files to Touch

| File | Change |
|---|---|
| `engine/intent_parser/schemas.py` | Add `FloorPlanOpBatch` model |
| `engine/intent_parser/parser.py` | Add `parse_batch()`, update `_summarize_state()`, keep `parse()` as compat shim |
| `engine/plan_manager.py` | Update `instruct()` to call `parse_batch()`, return `list[FloorPlanOp]` |
| `api/routes/plan.py` | Update `InstructResponse` + route handler |
| `tests/test_phase2_e2e.py` | Update assertions that check `op_type` (now `op_types[0]`) |
| `tests/test_plan_manager.py` | Update `instruct()` call sites — now returns list, not single op |

## Do NOT Touch

- `engine/constraint_solver/` — no changes
- `engine/cad_generator/` — no changes
- `engine/exporter/` — no changes
- `engine/intent_parser/schemas.py` beyond adding `FloorPlanOpBatch`
- `frontend/` — the response shape change is backward-compatible for display purposes

---

## Tests to Add

In `tests/test_phase5_batch.py` (new file):

```python
# These are the key cases to cover:

# 1. Single-room instruction still works (1 op returned)
# "Add a living room of 300 sqft" → ops=[add_room], len==1

# 2. Multi-room instruction produces multiple ops
# "Add 3 bedrooms" → ops len >= 3, all op_type==add_room

# 3. Master suite bundle expands correctly  
# "Add a master suite" → ops contains add_room for bedroom AND bathroom

# 4. Resize + adjacency request produces 2 ops
# "Make the kitchen bigger to fit the dining room" (with existing kitchen + dining)
# → resize_room(kitchen) + set_constraint(adjacency) in ops

# 5. Open concept request
# "Add an open concept kitchen and living area"
# → 2+ add_room ops with adjacency_requirements set

# 6. Backward compat: parse() still returns single FloorPlanOp (first of batch)
```

Use `@pytest.mark.live` to gate tests that call the real Claude API.
Mock tests should patch `IntentParser._client.messages.create` with fixture JSON.

---

## Architecture Notes

- `parse_batch()` SLA is still 2s — Haiku handles multi-op decomposition well
- If Claude returns a single-op JSON (no "ops" array), `parse_batch()` should detect this and
  wrap it: `FloorPlanOpBatch(ops=[single_op], batch_description=single_op.metadata.get("raw_nl",""))`
- The `_apply_op()` loop in `instruct()` is sequential — if op N fails, ops 0..N-1 are already
  applied. This is acceptable for Phase 5. Add rollback (copy state before loop, restore on error) if needed.
- `PlanManager.apply_op()` (direct op injection, used in tests) stays unchanged — single op only.

---

## Commit Message

```
feat: Phase 5 — multi-op batch parsing + architect-mode intent extraction

- IntentParser.parse_batch() → FloorPlanOpBatch (1..N ops per instruction)
- "Add 3 bedrooms", "Add a master suite", "Open concept living" all work in one call
- Richer state summary (total_area, constraints, adjacency) fed to Claude
- PlanManager.instruct() applies all ops in batch, returns list[FloorPlanOp]
- API InstructResponse now returns ops_applied + op_types list
- Backward compat: IntentParser.parse() still returns single FloorPlanOp
- 85 existing tests unaffected; new test_phase5_batch.py covers batch cases
```
