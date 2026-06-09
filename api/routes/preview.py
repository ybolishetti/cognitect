"""
Preview API route — renders floor plan to PNG.

GET /plan/{plan_id}/preview
  Query params:
    width  (int, default 900) — image width in pixels
    height (int, default 700) — image height in pixels
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from engine.previewer import PlanPreviewer
from api.routes.plan import _PLANS  # shared in-memory session store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plan", tags=["preview"])

_previewer = PlanPreviewer()


@router.get("/{plan_id}/preview")
async def preview_plan(plan_id: str, width: int = 900, height: int = 700):
    """
    Render the current plan state as a PNG image.
    Returns an empty canvas with a prompt message if the plan has no rooms.
    """
    if plan_id not in _PLANS:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")

    manager = _PLANS[plan_id]
    state = manager.state

    coordinate_matrix = state.coordinate_matrix or {}

    # If no coordinate_matrix yet but rooms exist, run solver first
    if not coordinate_matrix and state.rooms:
        try:
            coordinate_matrix = manager.solve()
        except Exception as exc:
            logger.warning("Solver failed during preview: %s", exc)
            coordinate_matrix = {}

    room_metadata = {
        room_id: {"name": spec.name, "room_type": spec.room_type}
        for room_id, spec in state.rooms.items()
    }

    png_bytes = _previewer.render(
        coordinate_matrix=coordinate_matrix,
        room_metadata=room_metadata,
        width_px=width,
        height_px=height,
        title=f"Plan {plan_id}",
    )

    return Response(content=png_bytes, media_type="image/png")
