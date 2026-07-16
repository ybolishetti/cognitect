"""Layout → PlanState materialization.

Lossy conversion: Layout has more geometry than PlanState needs. We preserve
room names/types/areas and derive a coordinate_matrix from each room's vertex
bounding box (min/max x/y). Structural grid and raw wall geometry are dropped
— the legacy editor doesn't model them.

This is a one-way trip. Once a Layout is materialized, edits go through the
legacy /instruct + kiwisolver path. Re-generating from the same spec produces
fresh Layouts but does not update materialized plans.
"""

from __future__ import annotations

import secrets
from typing import Any

# RoomType (engine/layout/schemas.py) has two values RoomSpec.room_type
# (engine/intent_parser/schemas.py) doesn't accept. Map them to "other" so
# FloorPlanState.model_validate (run on every plan load) doesn't reject the
# materialized state.
_UNSUPPORTED_ROOM_TYPES = {"closet", "utility"}

# ConnectionSpec.connection_type only accepts these three values. "window"
# openings don't map to a passage between rooms, so they're skipped.
_OPENING_TO_CONNECTION_TYPE = {
    "door": "door",
    "archway": "archway",
    "wall_opening": "wall_opening",
}


def layout_to_plan_state(layout_json: dict) -> dict:
    """Convert Architecture C Layout JSON → legacy FloorPlanState dict.

    Args:
        layout_json: dict-form Layout (from Layout.model_dump(mode="json"))

    Returns:
        dict shaped like FloorPlanState — safe to hand to plan_store.save_plan
        or PlanManager via plan_store._deserialize. The plan name lives in the
        plans.name column, not inside FloorPlanState, so it's not embedded here.
    """
    rooms_out: dict[str, dict[str, Any]] = {}
    for room in layout_json.get("rooms", []):
        rid = room["id"]
        room_type = room["room_type"]
        if room_type in _UNSUPPORTED_ROOM_TYPES:
            room_type = "other"
        rooms_out[rid] = {
            "name": room["name"],
            "room_type": room_type,
            "area_sqft": float(room["area_sqft"]),
            "aspect_ratio": None,  # Layout doesn't carry aspect_ratio explicitly
            "scale_factor": None,
        }

    coordinate_matrix: dict[str, dict[str, float]] = {}
    for room in layout_json.get("rooms", []):
        xs = [v[0] for v in room["vertices"]]
        ys = [v[1] for v in room["vertices"]]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        coordinate_matrix[room["id"]] = {
            "x": round(min_x, 2),
            "y": round(min_y, 2),
            "width": round(max_x - min_x, 2),
            "height": round(max_y - min_y, 2),
        }

    walls_by_id = {wall["id"]: wall for wall in layout_json.get("walls", [])}
    connections: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for opening in layout_json.get("openings", []):
        connection_type = _OPENING_TO_CONNECTION_TYPE.get(opening["opening_type"])
        if connection_type is None:
            continue  # "window" — doesn't represent a passage between rooms
        wall = walls_by_id.get(opening["wall_id"])
        if wall is None:
            continue
        bounds = wall.get("bounds_rooms", [])
        if len(bounds) != 2:
            continue
        pair = tuple(sorted(bounds))
        if pair in seen:
            continue
        seen.add(pair)
        connections.append({
            "room_a_id": pair[0],
            "room_b_id": pair[1],
            "connection_type": connection_type,
            "width_ft": opening.get("width_ft"),
        })

    return {
        "plan_id": _new_plan_id(),
        "rooms": rooms_out,
        "connections": connections,
        "coordinate_matrix": coordinate_matrix,
        "version": 1,
    }


def _new_plan_id() -> str:
    """Generate a legacy-style plan_id. 8-char lowercase hex.

    Overwritten by plan_store.create_plan_from_materialized with the real
    plans.id UUID — this is just a placeholder to satisfy FloorPlanState's
    required plan_id field before that happens.
    """
    return secrets.token_hex(4)
