-- 006: materialization link — plans row created from a generated_layout_versions row
-- Applies to: Supabase Postgres (run in SQL Editor)

alter table public.plans
  add column materialized_from_layout_id uuid null
    references public.generated_layout_versions(id) on delete set null;

-- Uniqueness: a given (generated_layout_version, owner) can only produce one plan.
-- Enforced via partial unique indexes so anon (device_id) and authed (user_id) both work.
create unique index plans_materialized_from_layout_user_uniq
  on public.plans(materialized_from_layout_id, user_id)
  where materialized_from_layout_id is not null and user_id is not null;

create unique index plans_materialized_from_layout_device_uniq
  on public.plans(materialized_from_layout_id, device_id)
  where materialized_from_layout_id is not null and device_id is not null and user_id is null;

comment on column public.plans.materialized_from_layout_id is
  'If set, this plan was created by materializing generated_layout_versions[id]. '
  'Used as an idempotency key by POST /v2/plans/generate/{gpid}/materialize.';
