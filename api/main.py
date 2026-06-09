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
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes.plans import router as plans_router
from api.routes.plan import router as plan_router

load_dotenv()

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


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok", "version": "0.1.0"}


def run() -> None:
    """Entry point for cognitect-api console script."""
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("RELOAD", "false").lower() == "true",
    )
