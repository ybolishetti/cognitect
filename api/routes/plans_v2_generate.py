"""POST /v2/plans/generate — Architecture C best-of-N endpoint.

The generate loop runs run_best_of_n behind Supabase auth + device-id
anon flow (same conventions as api/routes/plans_v2.py). Persists results
to generated_plans + generated_layout_versions. Rate-limited via
llm_call_log (share the same table as /instruct — one bucket).

Two response modes:
  - Default (Accept: application/json)     — synchronous: run the whole
                                             pipeline, return top-K
  - Accept: text/event-stream              — SSE: fire progress events at
                                             checkpoints, then final result
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import optional_user
from api.routes.plans_v2 import _check_rate_limit, _require_owner
from api.storage import generated_plan_store, plan_store
from engine.generators import GeneratorFailure
from engine.layout import FloorPlanSpec
from engine.pipeline import PipelineFailure, run_best_of_n
from engine.pipeline.spec_hash import spec_hash

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/plans", tags=["plans_v2_generate"])


# ── Request / response models ─────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    spec: FloorPlanSpec
    top_k: int = Field(default=1, ge=1, le=8)
    force_regenerate: bool = Field(default=False)


class GenerateLayoutSummary(BaseModel):
    selection_rank: int
    user_score: Optional[float]
    plan_id: str  # UUID of the generated_layout_versions row for the frontend to fetch later


class GenerateResponse(BaseModel):
    generated_plan_id: str
    spec_hash: str
    generator_name: str
    generator_version: str
    total_candidates: int
    survived_layer_a: int
    survived_layer_c: int
    elapsed_ms: int
    layouts: list[GenerateLayoutSummary]
    cached: bool = False  # true if we returned a cached generation


# ── Sync endpoint ─────────────────────────────────────────────────────────────

@router.post("/generate", response_model=GenerateResponse, status_code=status.HTTP_201_CREATED)
async def generate_plan(
    req: GenerateRequest,
    owner: tuple = Depends(optional_user),
) -> GenerateResponse:
    user, device_id = owner
    _require_owner(user, device_id)

    h = spec_hash(req.spec)

    # 1. Cache check — a hit costs nothing (no LLM/pipeline call), so it must
    # be checked before the rate limit, not after: otherwise a cache hit for
    # an already-rate-limited caller would incorrectly 429 instead of serving
    # the cached result.
    if not req.force_regenerate:
        cached = generated_plan_store.find_cached_generation(
            spec_hash=h,
            user_id=user.id if user else None,
            device_id=device_id,
            max_age_hours=24,
        )
        if cached is not None:
            return _to_response(cached, cached_flag=True)

    _check_rate_limit(user, device_id)

    # 2. Run best-of-N
    try:
        result = await asyncio.to_thread(
            run_best_of_n, req.spec, top_k=req.top_k, include_layer_b=True
        )
    except GeneratorFailure as exc:
        # Generator produced 0 candidates — surface as 502 (upstream problem)
        _log_llm_failure(user, device_id, "generator_failure", str(exc))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Generator {exc.generator_name} failed to produce candidates: {exc.reason_code}",
        )
    except PipelineFailure as exc:
        # All candidates rejected by hard gates — surface as 422 (spec un-plannable)
        _log_llm_failure(user, device_id, "pipeline_failure", str(exc))
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Generated {exc.total_candidates} candidates but all failed code compliance "
            f"(A: {exc.survived_layer_a}, C: {exc.survived_layer_c})",
        )
    except NotImplementedError as exc:
        # LAYOUT_GENERATOR=finetuned in prod (not shipped yet). 501.
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc))

    # 3. Persist
    generated_plan_id = generated_plan_store.create_generated_plan(
        user_id=user.id if user else None,
        device_id=device_id,
        spec_json=req.spec.model_dump(mode="json"),
        spec_hash=h,
        generator_name=result.generator_name,
        generator_version=result.generator_version,
        total_candidates=result.total_candidates,
        survived_layer_a=result.survived_layer_a,
        survived_layer_c=result.survived_layer_c,
        elapsed_ms=int(result.elapsed_ms),
        top_layouts=result.layouts,
    )

    # 4. Log success
    _log_llm_success(user, device_id, generated_plan_id, result)

    # 5. Reload from store (canonical source of plan_id UUIDs) and respond
    row = generated_plan_store.load_generated_plan(
        generated_plan_id, user_id=user.id if user else None, device_id=device_id
    )
    return _to_response(row, cached_flag=False)


# ── SSE endpoint ──────────────────────────────────────────────────────────────

@router.post("/generate/stream")
async def generate_plan_stream(
    req: GenerateRequest,
    owner: tuple = Depends(optional_user),
) -> StreamingResponse:
    user, device_id = owner
    _require_owner(user, device_id)
    _check_rate_limit(user, device_id)

    async def event_stream() -> AsyncIterator[str]:
        yield _sse("progress", {"phase": "generating", "detail": "calling generator"})
        try:
            result = await asyncio.to_thread(
                run_best_of_n, req.spec, top_k=req.top_k, include_layer_b=True
            )
        except GeneratorFailure as exc:
            yield _sse("error", {"kind": "generator_failure", "detail": str(exc)})
            return
        except PipelineFailure as exc:
            yield _sse("error", {
                "kind": "pipeline_failure",
                "detail": f"0/{exc.total_candidates} candidates survived hard gates",
                "survived_layer_a": exc.survived_layer_a,
                "survived_layer_c": exc.survived_layer_c,
            })
            return
        except NotImplementedError as exc:
            yield _sse("error", {"kind": "not_implemented", "detail": str(exc)})
            return

        yield _sse("progress", {
            "phase": "verified",
            "total_candidates": result.total_candidates,
            "survived_layer_a": result.survived_layer_a,
            "survived_layer_c": result.survived_layer_c,
        })

        h = spec_hash(req.spec)
        generated_plan_id = generated_plan_store.create_generated_plan(
            user_id=user.id if user else None,
            device_id=device_id,
            spec_json=req.spec.model_dump(mode="json"),
            spec_hash=h,
            generator_name=result.generator_name,
            generator_version=result.generator_version,
            total_candidates=result.total_candidates,
            survived_layer_a=result.survived_layer_a,
            survived_layer_c=result.survived_layer_c,
            elapsed_ms=int(result.elapsed_ms),
            top_layouts=result.layouts,
        )
        _log_llm_success(user, device_id, generated_plan_id, result)

        row = generated_plan_store.load_generated_plan(
            generated_plan_id, user_id=user.id if user else None, device_id=device_id
        )
        response = _to_response(row, cached_flag=False)
        yield _sse("result", response.model_dump())

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── GET single generated plan ─────────────────────────────────────────────────

@router.get("/generate/{generated_plan_id}", response_model=GenerateResponse)
async def get_generated_plan(
    generated_plan_id: str,
    owner: tuple = Depends(optional_user),
) -> GenerateResponse:
    user, device_id = owner
    _require_owner(user, device_id)
    try:
        row = generated_plan_store.load_generated_plan(
            generated_plan_id,
            user_id=user.id if user else None,
            device_id=device_id,
        )
    except generated_plan_store.GeneratedPlanNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except generated_plan_store.GeneratedPlanAccessDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    return _to_response(row, cached_flag=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_response(row: dict, cached_flag: bool) -> GenerateResponse:
    return GenerateResponse(
        generated_plan_id=row["id"],
        spec_hash=row["spec_hash"],
        generator_name=row["generator_name"],
        generator_version=row["generator_version"],
        total_candidates=row["total_candidates"],
        survived_layer_a=row["survived_layer_a"],
        survived_layer_c=row["survived_layer_c"],
        elapsed_ms=int(row["elapsed_ms"]),
        cached=cached_flag,
        layouts=[
            GenerateLayoutSummary(
                selection_rank=layout["selection_rank"],
                user_score=layout.get("user_score"),
                # Client fetches full Layout JSON via GET /v2/plans/generate/{id};
                # the summary just gives them the rank + score to render a picker.
                plan_id=row["id"],
            )
            for layout in row["layouts"]
        ],
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _log_llm_success(user, device_id, generated_plan_id, result) -> None:
    plan_store.log_llm_call(
        user_id=user.id if user else None,
        device_id=device_id,
        plan_id=None,  # generate output lives in generated_plans, not plans
        model=f"{result.generator_name}-{result.generator_version}",
        latency_ms=int(result.elapsed_ms),
        status="ok",
        error_message=None,
    )


def _log_llm_failure(user, device_id, kind: str, detail: str) -> None:
    plan_store.log_llm_call(
        user_id=user.id if user else None,
        device_id=device_id,
        plan_id=None,
        model="best_of_n",
        latency_ms=0,
        status=kind,
        error_message=detail[:2000],  # column is text; cap for safety
    )
