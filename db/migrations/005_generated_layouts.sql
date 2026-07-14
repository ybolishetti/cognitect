-- 005: generated layouts (Architecture C best-of-N output)
-- Applies to: Supabase Postgres (run in SQL Editor or via `supabase db push`)

create table public.generated_plans (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid references auth.users(id) on delete cascade,
  device_id text,
  spec_hash text not null,               -- from engine.pipeline.spec_hash — enables reuse detection
  spec_json jsonb not null,              -- the FloorPlanSpec that produced this run
  generator_name text not null,          -- "stub" | "prompted" | "finetuned"
  generator_version text not null,
  total_candidates int not null,
  survived_layer_a int not null,
  survived_layer_c int not null,
  elapsed_ms int not null,
  created_at timestamptz not null default now(),
  archived boolean not null default false,
  constraint generated_plans_owner_check check (user_id is not null or device_id is not null)
);
create index generated_plans_user_id_idx on public.generated_plans(user_id) where user_id is not null;
create index generated_plans_device_id_idx on public.generated_plans(device_id) where device_id is not null and user_id is null;
create index generated_plans_spec_hash_idx on public.generated_plans(spec_hash);
create index generated_plans_created_at_idx on public.generated_plans(created_at desc);

create table public.generated_layout_versions (
  id uuid primary key default uuid_generate_v4(),
  generated_plan_id uuid not null references public.generated_plans(id) on delete cascade,
  selection_rank int not null,           -- 0 = top; matches LayoutAuditManifest.selection_rank
  layout_json jsonb not null,            -- the full Layout including .audit manifest
  user_score double precision,           -- LayoutAuditManifest.user_score, for UI ranking
  created_at timestamptz not null default now(),
  unique(generated_plan_id, selection_rank)
);
create index generated_layout_versions_plan_id_idx on public.generated_layout_versions(generated_plan_id, selection_rank);

-- RLS policies — mirror plans/plan_versions
alter table public.generated_plans enable row level security;
alter table public.generated_layout_versions enable row level security;

-- Owner-only read
create policy generated_plans_read_owner on public.generated_plans
  for select using (auth.uid() = user_id or (user_id is null and true /* device access enforced in app */));

create policy generated_plans_insert_owner on public.generated_plans
  for insert with check (auth.uid() = user_id or user_id is null);

create policy generated_plans_update_owner on public.generated_plans
  for update using (auth.uid() = user_id);

create policy generated_layout_versions_read_owner on public.generated_layout_versions
  for select using (
    exists(
      select 1 from public.generated_plans p
      where p.id = generated_layout_versions.generated_plan_id
      and (p.user_id = auth.uid() or p.user_id is null)
    )
  );

create policy generated_layout_versions_insert_owner on public.generated_layout_versions
  for insert with check (
    exists(
      select 1 from public.generated_plans p
      where p.id = generated_layout_versions.generated_plan_id
      and (p.user_id = auth.uid() or p.user_id is null)
    )
  );
