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
`POST /v2/plans/upload` seeds a persistent plan from an uploaded `.dxf` or
`.json` file (multipart, 10 MB max), sharing its DXF/JSON parsing with the
legacy `/plan/load` route via `engine/importers`.

## How it works

A first-time `/try` visit lands in a structured spec builder (rooms, sizes,
adjacencies, optional site constraints) instead of a blank auto-created plan.
Submitting calls `POST /v2/plans/generate`, which runs the request through
Architecture C's layout pipeline — geometry checks (Layer A) and IRC-2021
building-code verification (Layer C) — and returns the top scoring
candidates as a gallery. Returning to an in-progress plan (via a saved link
or local browser state) skips the spec builder and goes straight into the
existing natural-language instruction editor.

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for Fly.io + Supabase setup, local dev
options, and testing the anonymous-plan claim flow end-to-end.
