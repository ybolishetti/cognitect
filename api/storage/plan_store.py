"""
Supabase-backed persistent plan store.

Backs the new `/v2/plans` routes (api/routes/plans_v2.py) only. The existing
`/plan` routes (api/routes/plan.py) keep their own in-memory `_PLANS` dict
unchanged for backward compat.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from supabase import create_client, Client

from engine.plan_manager import PlanManager
from engine.intent_parser.schemas import FloorPlanState

logger = logging.getLogger(__name__)

_VERSION_KEEP = 50

_client: Client = create_client(
    os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
)


class PlanStoreError(Exception):
    """Base error for plan_store operations."""


class PlanNotFoundError(PlanStoreError):
    """No plan exists with the given id."""


class PlanAccessDeniedError(PlanStoreError):
    """Plan exists but the caller doesn't own it."""


def _serialize(manager: PlanManager) -> dict:
    """
    Snapshot the persistable fields of a PlanManager's state.

    FloorPlanState (engine/intent_parser/schemas.py) is a plain Pydantic model
    — rooms/constraints/connections/coordinate_matrix/version, no live solver
    objects — so a whole-model dump is safe. PlanManager's own collaborators
    (_solver, _parser, _cad, _exporter, _history, _last_mutated_rooms) are
    session-only and must never be persisted.
    """
    return manager.state.model_dump(mode="json")


def _deserialize(state_json: dict, plan_id: str) -> PlanManager:
    """
    Rehydrate a PlanManager from a persisted state snapshot.

    PlanManager.state is a read-only @property with no setter, and engine/ is
    frozen for this work, so the private _state attribute is set directly
    rather than adding a public setter.
    """
    manager = PlanManager(plan_id=plan_id)
    manager._state = FloorPlanState.model_validate(state_json)
    return manager


def _require_exactly_one(user_id: Optional[str], device_id: Optional[str]) -> None:
    if bool(user_id) == bool(device_id):
        raise ValueError("Exactly one of user_id or device_id is required")


def _fetch_owned_row(plan_id: str, *, user_id: str) -> dict:
    res = (
        _client.table("plans")
        .select("id, user_id, archived")
        .eq("id", plan_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise PlanNotFoundError(f"Plan {plan_id} not found")
    row = res.data[0]
    if row.get("archived"):
        raise PlanNotFoundError(f"Plan {plan_id} not found")
    if row.get("user_id") != user_id:
        raise PlanAccessDeniedError(f"Plan {plan_id} not accessible")
    return row


def ping() -> bool:
    """Cheap connectivity check for the health endpoint."""
    try:
        _client.table("plans").select("id").limit(1).execute()
        return True
    except Exception as exc:
        logger.warning("Supabase ping failed: %s", exc)
        return False


def create_plan(
    *,
    user_id: Optional[str] = None,
    device_id: Optional[str] = None,
    name: str = "Untitled Plan",
) -> tuple[str, PlanManager]:
    """Create a new plan owned by exactly one of user_id or device_id."""
    _require_exactly_one(user_id, device_id)

    plan_id = str(uuid.uuid4())
    manager = PlanManager(plan_id=plan_id)
    row = {
        "id": plan_id,
        "user_id": user_id,
        "device_id": device_id,
        "name": name,
        "state_json": _serialize(manager),
        "version": 1,
        "room_count": 0,
    }
    _client.table("plans").insert(row).execute()
    logger.info("Created plan %s (user_id=%s, device_id=%s)", plan_id, user_id, device_id)
    return plan_id, manager


def load_plan(
    plan_id: str, *, user_id: Optional[str] = None, device_id: Optional[str] = None
) -> tuple[PlanManager, str]:
    """
    Load a plan, enforcing ownership by user_id or device_id. Returns
    (manager, name). A soft-deleted (archived) plan is treated as not-found —
    the row and its plan_versions history are preserved, but it's invisible
    to every /v2/plans read/write path.
    """
    res = _client.table("plans").select("*").eq("id", plan_id).limit(1).execute()
    if not res.data:
        raise PlanNotFoundError(f"Plan {plan_id} not found")

    row = res.data[0]
    if row.get("archived"):
        raise PlanNotFoundError(f"Plan {plan_id} not found")

    owned_by_user = bool(user_id) and row.get("user_id") == user_id
    owned_by_device = (
        bool(device_id) and row.get("device_id") == device_id and not row.get("user_id")
    )
    if not (owned_by_user or owned_by_device):
        raise PlanAccessDeniedError(f"Plan {plan_id} not accessible")

    _client.table("plans").update({"last_opened_at": "now()"}).eq("id", plan_id).execute()
    return _deserialize(row["state_json"], plan_id), row.get("name", "Untitled Plan")


def save_plan(manager: PlanManager, *, instruction: Optional[str] = None) -> None:
    """Persist the current state, append a version-history row, and trim old versions."""
    state = _serialize(manager)
    _client.table("plans").update(
        {
            "state_json": state,
            "version": manager.state.version,
            "room_count": manager.room_count,
        }
    ).eq("id", manager.plan_id).execute()

    _client.table("plan_versions").insert(
        {
            "plan_id": manager.plan_id,
            "version": manager.state.version,
            "state_json": state,
            "instruction": instruction,
        }
    ).execute()

    _client.rpc(
        "trim_plan_versions", {"p_plan_id": manager.plan_id, "p_keep": _VERSION_KEEP}
    ).execute()


def list_plans(*, user_id: str, include_archived: bool = False) -> list[dict]:
    """List an authenticated user's plans, most recently opened first."""
    query = (
        _client.table("plans")
        .select(
            "id, name, room_count, version, thumbnail_url, "
            "created_at, updated_at, last_opened_at, archived"
        )
        .eq("user_id", user_id)
        .order("last_opened_at", desc=True)
    )
    if not include_archived:
        query = query.eq("archived", False)
    return query.execute().data


def rename_plan(plan_id: str, name: str, *, user_id: str) -> None:
    _fetch_owned_row(plan_id, user_id=user_id)
    _client.table("plans").update({"name": name}).eq("id", plan_id).execute()


def delete_plan(plan_id: str, *, user_id: str) -> None:
    """Soft-delete: mark archived rather than removing the row."""
    _fetch_owned_row(plan_id, user_id=user_id)
    _client.table("plans").update({"archived": True}).eq("id", plan_id).execute()


# TODO: anonymous plans older than ~30 days with no claim should eventually be
# soft-deleted (archived) by a scheduled job. Not built here — out of scope
# for this pass; revisit once there's usage data to size a real retention window.


def claim_anonymous_plans(device_id: str, user_id: str) -> list[str]:
    """Reassign all of a device's anonymous plans to a newly authenticated user."""
    res = _client.rpc(
        "claim_anonymous_plans", {"p_device_id": device_id, "p_user_id": user_id}
    ).execute()
    return res.data[0]["plan_ids"] if res.data else []


def log_llm_call(
    *,
    user_id: Optional[str],
    device_id: Optional[str],
    plan_id: Optional[str],
    model: str,
    status: str,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """Record one Claude call for audit + rate-limiting (see count_llm_calls_since)."""
    _client.table("llm_call_log").insert(
        {
            "user_id": user_id,
            "device_id": device_id,
            "plan_id": plan_id,
            "model": model,
            "status": status,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
            "error_message": error_message,
        }
    ).execute()


def count_llm_calls_since(
    *, user_id: Optional[str], device_id: Optional[str], since_iso: str
) -> int:
    """Count llm_call_log rows for a caller since a given ISO timestamp (rate limiting)."""
    query = _client.table("llm_call_log").select("id", count="exact").gte(
        "created_at", since_iso
    )
    query = query.eq("user_id", user_id) if user_id else query.eq("device_id", device_id)
    res = query.execute()
    return res.count if res.count is not None else len(res.data or [])
