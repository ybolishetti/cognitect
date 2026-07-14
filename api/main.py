"""
Cognitect FastAPI application.

Endpoints:
  GET  /health           — liveness probe
  POST /plans            — create floor plan
  POST /plans/{id}/generate — NL → coordinate matrix (async via Celery)
  GET  /plans/{id}/status/{task_id} — poll task
  GET  /plans/{id}/export/{format}  — download DXF or PDF
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # must run before importing plans_v2 -> plan_store, which reads
                # SUPABASE_URL/SUPABASE_SERVICE_KEY at module import time

import sentry_sdk

if os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
    )

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .routes.plans import router as plans_router
from api.routes.plan import router as plan_router
from api.routes.preview import router as preview_router
from api.routes.load import router as load_router
from api.routes.plans_v2 import router as plans_v2_router
from api.routes.plans_v2_generate import router as plans_v2_generate_router
from api.storage import plan_store

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Cognitect API starting up…")
    # TODO: init DB connection pool, Redis ping, etc.
    yield
    logger.info("Cognitect API shutting down.")


app = FastAPI(
    title="Cognitect",
    description="Headless NL→parametric CAD engine for architectural floor plans.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — tighten in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(plans_router, prefix="/plans", tags=["plans"])
app.include_router(plan_router)
app.include_router(preview_router)
app.include_router(load_router)
app.include_router(plans_v2_router)
app.include_router(plans_v2_generate_router)


@app.get("/health", tags=["meta"])
async def health() -> JSONResponse:
    """Liveness probe. 503 if Supabase (persistent plan store) is unreachable."""
    if not plan_store.ping():
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "version": "0.1.0", "supabase": "unreachable"},
        )
    return JSONResponse(content={"status": "ok", "version": "0.1.0", "supabase": "ok"})


def run() -> None:
    """Entry point for cognitect-api console script."""
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("RELOAD", "false").lower() == "true",
    )
