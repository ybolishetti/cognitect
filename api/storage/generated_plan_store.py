"""Persistence for /v2/plans/generate output.

Backed by generated_plans + generated_layout_versions tables (migration 005).
Mirrors plan_store.py's ownership model:
  - Authenticated user (user_id) OR anonymous device (device_id), never both
  - Owner-check enforced in Python (application layer) as well as in RLS
  - Soft delete via archived=true — history preserved for audit
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from engine.layout import Layout
from api.storage import plan_store  # reuse plan_store's module-level client singleton

logger = logging.getLogger(__name__)


class GeneratedPlanStoreError(Exception):
    pass


class GeneratedPlanNotFoundError(GeneratedPlanStoreError):
    pass


class GeneratedPlanAccessDeniedError(GeneratedPlanStoreError):
    pass


def _require_exactly_one(user_id: Optional[str], device_id: Optional[str]) -> None:
    if bool(user_id) == bool(device_id):
        raise GeneratedPlanStoreError(
            "exactly one of (user_id, device_id) must be set"
        )


def create_generated_plan(
    *,
    user_id: Optional[str] = None,
    device_id: Optional[str] = None,
    spec_json: dict,
    spec_hash: str,
    generator_name: str,
    generator_version: str,
    total_candidates: int,
    survived_layer_a: int,
    survived_layer_c: int,
    elapsed_ms: int,
    top_layouts: list[Layout],
) -> str:
    """Insert the header row + per-rank layout rows. Returns the generated_plan_id.

    Called AFTER run_best_of_n returns successfully — do NOT persist a failure
    (PipelineFailure has no generated_plans to store; log it via llm_call_log
    with status='pipeline_failure' instead).
    """
    _require_exactly_one(user_id, device_id)
    plan_id = str(uuid.uuid4())

    plan_store._client.table("generated_plans").insert({
        "id": plan_id,
        "user_id": user_id,
        "device_id": device_id,
        "spec_hash": spec_hash,
        "spec_json": spec_json,
        "generator_name": generator_name,
        "generator_version": generator_version,
        "total_candidates": total_candidates,
        "survived_layer_a": survived_layer_a,
        "survived_layer_c": survived_layer_c,
        "elapsed_ms": elapsed_ms,
    }).execute()

    # Insert one row per top-K layout. NOTE: model_dump(mode="json") serializes
    # datetime + Enum fields correctly for JSON.
    rows = []
    for rank, layout in enumerate(top_layouts):
        layout_json = layout.model_dump(mode="json")
        user_score = None
        if layout.audit and layout.audit.user_score is not None:
            user_score = float(layout.audit.user_score)
        rows.append({
            "generated_plan_id": plan_id,
            "selection_rank": rank,
            "layout_json": layout_json,
            "user_score": user_score,
        })
    if rows:
        plan_store._client.table("generated_layout_versions").insert(rows).execute()

    logger.info(
        "Persisted generated_plan %s: %d candidates, %d survived A, %d survived C, %d layouts stored",
        plan_id, total_candidates, survived_layer_a, survived_layer_c, len(top_layouts),
    )
    return plan_id


def load_generated_plan(
    generated_plan_id: str,
    *,
    user_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> dict:
    """Load the header row + all layouts. Returns dict with keys:
      id, user_id, device_id, spec_json, spec_hash, generator_name,
      generator_version, total_candidates, survived_layer_a, survived_layer_c,
      elapsed_ms, created_at, layouts (list of {id, selection_rank, layout_json, user_score})

    Raises GeneratedPlanNotFoundError if row missing or archived.
    Raises GeneratedPlanAccessDeniedError if caller doesn't own it.
    """
    res = plan_store._client.table("generated_plans").select("*").eq("id", generated_plan_id).limit(1).execute()
    if not res.data:
        raise GeneratedPlanNotFoundError(f"Generated plan {generated_plan_id} not found")
    row = res.data[0]
    if row.get("archived"):
        raise GeneratedPlanNotFoundError(f"Generated plan {generated_plan_id} not found")

    owned_by_user = bool(user_id) and row.get("user_id") == user_id
    owned_by_device = (
        bool(device_id) and row.get("device_id") == device_id and not row.get("user_id")
    )
    if not (owned_by_user or owned_by_device):
        raise GeneratedPlanAccessDeniedError(f"Generated plan {generated_plan_id} not accessible")

    layouts_res = (
        plan_store._client.table("generated_layout_versions")
        .select("id, selection_rank, layout_json, user_score")
        .eq("generated_plan_id", generated_plan_id)
        .order("selection_rank")
        .execute()
    )
    row["layouts"] = layouts_res.data or []
    return row


def list_generated_plans_for_user(*, user_id: str, include_archived: bool = False) -> list[dict]:
    """List all generated_plans for a user, most recent first."""
    query = plan_store._client.table("generated_plans").select("id, spec_hash, generator_name, created_at, archived").eq("user_id", user_id)
    if not include_archived:
        query = query.eq("archived", False)
    res = query.order("created_at", desc=True).execute()
    return res.data or []


def archive_generated_plan(generated_plan_id: str, *, user_id: str) -> None:
    plan_store._client.table("generated_plans").update({"archived": True}).eq("id", generated_plan_id).eq("user_id", user_id).execute()


def find_cached_generation(
    *,
    spec_hash: str,
    user_id: Optional[str] = None,
    device_id: Optional[str] = None,
    max_age_hours: int = 24,
) -> Optional[dict]:
    """Return the most recent generated_plan for (spec_hash, owner) within max_age_hours,
    or None. Used by /generate to short-circuit — same spec twice returns cached layouts.

    The client passes ?force_regenerate=true to bypass this.
    """
    from datetime import datetime, timedelta, timezone
    since_iso = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()

    query = (
        plan_store._client.table("generated_plans")
        .select("id")
        .eq("spec_hash", spec_hash)
        .eq("archived", False)
        .gte("created_at", since_iso)
    )
    if user_id:
        query = query.eq("user_id", user_id)
    else:
        query = query.eq("device_id", device_id).is_("user_id", None)

    res = query.order("created_at", desc=True).limit(1).execute()
    if not res.data:
        return None
    return load_generated_plan(res.data[0]["id"], user_id=user_id, device_id=device_id)
