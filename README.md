# cognitect
Injecting LLM chat capability into AutoCAD infrastructure

## Architecture

```
Vercel (Next.js)  ──HTTPS+JWT──>  Fly.io (FastAPI + FreeCAD Docker)  ──PG──>  Supabase (Auth + Postgres)
                                          │
                                          └── Claude API (engine/intent_parser)
```

The backend is a FastAPI app (`api/`) wrapping a headless NL→CAD engine
(`engine/`): natural-language instructions are parsed into typed ops by
Claude, applied to a floor plan state, resolved into room coordinates by a
constraint solver, and exported to DXF/PDF (optionally via FreeCAD for 3D).
`/v2/plans/*` is the persistent, multi-tenant API (Supabase-backed, JWT auth,
anonymous-plan support); the original `/plan/*` and `/plans/*` routes remain
as in-memory, non-persistent endpoints for backward compatibility.

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for Fly.io + Supabase setup, local dev
options, and testing the anonymous-plan claim flow end-to-end.
