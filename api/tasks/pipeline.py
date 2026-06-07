"""
Celery task definitions for the Cognitect pipeline.

Tasks run the full NL→CAD pipeline asynchronously.
"""

from __future__ import annotations

import logging
import os

from celery import Celery

logger = logging.getLogger(__name__)

# ── Celery app ────────────────────────────────────────────────────────────────
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "cognitect",
    broker=redis_url,
    backend=redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=3600,
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="cognitect.pipeline.run", bind=True, max_retries=2)
def run_pipeline_task(self, plan_id: str, nl_input: str) -> dict:
    """
    Async pipeline task: NL → Intent Parse → Constraint Solve → (CAD → Export when ready).

    Args:
        plan_id: ID of the floor plan to operate on.
        nl_input: Natural-language instruction.

    Returns:
        dict with keys: plan_id, op, coordinate_matrix, status
    """
    from api.routes.plans import _plans, _apply_op
    from engine.intent_parser.parser import IntentParser
    from engine.constraint_solver.solver import ConstraintSolver

    logger.info("Pipeline task started: plan=%s nl='%s'", plan_id, nl_input[:50])

    plan = _plans.get(plan_id)
    if plan is None:
        raise ValueError(f"Plan '{plan_id}' not found in task context")

    try:
        parser = IntentParser()
        op = parser.parse(nl_input, plan)
    except Exception as exc:
        logger.error("Intent parse failed in task: %s", exc)
        raise self.retry(exc=exc, countdown=2)

    _apply_op(plan, op)

    solver = ConstraintSolver()
    matrix = solver.solve(plan)
    plan.coordinate_matrix = matrix
    plan.version += 1
    _plans[plan_id] = plan

    logger.info("Pipeline task complete: plan=%s, %d rooms", plan_id, len(matrix))
    return {
        "plan_id": plan_id,
        "op": op.model_dump(),
        "coordinate_matrix": matrix,
        "status": "complete",
    }
