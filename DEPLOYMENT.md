# Deployment

Cognitect's backend runs on Fly.io, persists plans in Supabase Postgres, and
authenticates via Supabase Auth (Google OAuth, verified server-side as a
Supabase JWT). The frontend (Vercel/Next.js) is a separate track.

```
Vercel (Next.js)  ──HTTPS+JWT──>  Fly.io (FastAPI + FreeCAD Docker)  ──PG──>  Supabase (Auth + Postgres)
                                          │
                                          └── Claude API (engine/intent_parser)
```

## 1. Apply the Supabase schema

Migrations live in `db/migrations/`. Apply `001_init.sql` → `002_rls.sql` →
`003_claim_rpc.sql` → `004_trim_versions.sql`, in order, via the project's SQL
Editor (`https://supabase.com/dashboard/project/ynyjxmgfkptlvxrnfgmq/sql/new`).
See `db/README.md` for details, including the CLI alternative
(`supabase db push`). This is a manual step — nothing in this repo runs
migrations automatically.

You can confirm the schema is live by hitting `/health` once the API is
running: it returns `503` until Supabase's `plans` table exists, `200` after.

## 2. Fly.io setup (one-time)

```bash
flyctl auth login
fly launch --no-deploy   # creates the app from fly.toml, skips first deploy
fly secrets set \
  SUPABASE_URL=https://ynyjxmgfkptlvxrnfgmq.supabase.co \
  SUPABASE_SERVICE_KEY=<service role key, from Supabase project settings> \
  SUPABASE_JWT_SECRET=<JWT secret, from Supabase project settings> \
  COGNITECT_CLAUDE_API_KEY=<Claude API key> \
  CORS_ORIGINS=https://cognitect.vercel.app,http://localhost:3000
```

`SUPABASE_SERVICE_KEY` bypasses row-level security — treat it like a root
credential. Never put it in `.env.example`, a committed file, or a client
bundle.

## 3. Deploy

```bash
./scripts/deploy_fly.sh   # wraps `fly deploy`
```

Fly builds from the root `Dockerfile` (Python 3.12-slim, FreeCAD AppImage
extracted at build time — see the comment in that file about why it lands at
`/data/workspace/cognitect/squashfs-root` specifically: `engine/cad_generator/generator.py`
hardcodes that path and isn't part of this change). The app scales to zero
when idle (`min_machines_running = 0` in `fly.toml`) and back up on request.

## 4. Local development

Two separate Dockerfiles exist:

- **`Dockerfile`** (repo root) — the Fly/production image described above.
- **`Dockerfile.dev`** — used by `docker-compose.yml` for local development
  (Python 3.11, FreeCAD mounted as a volume, Postgres + Redis containers for
  parity with the original local stack). The API and Celery worker still talk
  to **Supabase**, not the local Postgres container — nothing in `api/` or
  `engine/` uses `DATABASE_URL` today, it's kept only so the compose stack
  doesn't need to change shape later if that changes.

Two ways to run locally:

```bash
# Option A: plain uvicorn (fastest iteration)
cp .env.example .env   # fill in real values
uvicorn api.main:app --reload

# Option B: full local stack (adds Celery worker, Redis, local Postgres)
docker compose up --build
```

## 5. Testing the claim flow end-to-end

1. Create an anonymous plan:
   ```bash
   curl -X POST localhost:8000/v2/plans \
     -H 'X-Device-Id: <a uuid, e.g. from `uuidgen`>' \
     -H 'Content-Type: application/json' \
     -d '{"name": "Test Plan"}'
   ```
2. Sign in via the frontend's Google OAuth flow to get a Supabase JWT (or mint
   one directly against the project for testing).
3. Claim the anonymous plan:
   ```bash
   curl -X POST localhost:8000/v2/plans/claim \
     -H 'Authorization: Bearer <jwt>' \
     -H 'Content-Type: application/json' \
     -d '{"device_id": "<the same uuid from step 1>"}'
   ```
4. Confirm: `GET /v2/plans/{plan_id}` with the device header now returns 403,
   and with the JWT returns 200.

## Notes / known limitations

- `plan_store.py` uses the Supabase **service role** key and enforces
  ownership in application code (see `load_plan`/`_fetch_owned_row`), not
  Postgres RLS — RLS policies in `002_rls.sql` are defense-in-depth for the
  (currently unused) case of a client querying Supabase directly.
- Old `/plan/*` and `/plans/*` routes are untouched, in-memory, and not
  persisted — they remain for backward compatibility.
- Anonymous-plan cleanup (deleting unclaimed plans after some retention
  window) isn't built yet — see the `TODO` in `plan_store.py`.
