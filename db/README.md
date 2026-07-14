# Database migrations

Schema for the Supabase-backed persistent plan store (`api/storage/plan_store.py`).

## Applying migrations

Claude Code does not run these — apply them by hand against the Supabase
project (`ynyjxmgfkptlvxrnfgmq`).

**Option A — Supabase SQL Editor (recommended for this project):**

1. Open the project's SQL Editor at
   `https://supabase.com/dashboard/project/ynyjxmgfkptlvxrnfgmq/sql/new`.
2. Paste and run each file **in order**, one at a time: `001_init.sql` →
   `002_rls.sql` → `003_claim_rpc.sql` → `004_trim_versions.sql` →
   `005_generated_layouts.sql`.
3. Confirm no errors before moving to the next file — `002`–`005` depend on
   tables created in `001`.

**Option B — Supabase CLI**, if installed and linked to the project:

```bash
supabase db push
```

This applies every file under `db/migrations/` in filename order.

## What each migration does

- `001_init.sql` — core tables (`consumer_profiles`, `plans`, `plan_versions`,
  `llm_call_log`), indexes, the `handle_new_user` trigger (auto-creates a
  `consumer_profiles` row on Supabase Auth signup), and `updated_at` triggers.
- `002_rls.sql` — row-level security policies. The backend uses the Supabase
  **service role** key, which bypasses RLS entirely; these policies only take
  effect if a client ever queries Supabase directly with a user JWT.
- `003_claim_rpc.sql` — `claim_anonymous_plans(device_id, user_id)`, a
  `security definer` RPC that atomically reassigns all of a device's
  anonymous plans to a newly authenticated user.
- `004_trim_versions.sql` — `trim_plan_versions(plan_id, keep)`, called after
  every save to cap `plan_versions` history at the most recent `keep` rows
  (the backend calls this with `keep=50`).
- `005_generated_layouts.sql` — `generated_plans` (header row per
  `POST /v2/plans/generate` call, with `spec_hash` for cache lookups) and
  `generated_layout_versions` (per-candidate Layout + audit manifest, keyed
  by `selection_rank`), plus RLS policies mirroring `plans`/`plan_versions`.
