-- Cognitect productization: row-level security
-- Service role (used by the FastAPI backend) bypasses RLS entirely — these
-- policies only matter if a client ever queries Supabase directly with a
-- user JWT (not the current architecture, but cheap insurance).

alter table public.consumer_profiles enable row level security;
create policy "users read own profile" on public.consumer_profiles for select using (auth.uid() = id);
create policy "users update own profile" on public.consumer_profiles for update using (auth.uid() = id);

alter table public.plans enable row level security;
create policy "users read own plans" on public.plans for select using (auth.uid() = user_id);
create policy "users insert own plans" on public.plans for insert with check (auth.uid() = user_id);
create policy "users update own plans" on public.plans for update using (auth.uid() = user_id);
create policy "users delete own plans" on public.plans for delete using (auth.uid() = user_id);

alter table public.plan_versions enable row level security;
create policy "users read own plan versions" on public.plan_versions for select using (
  exists (select 1 from public.plans where plans.id = plan_versions.plan_id and plans.user_id = auth.uid())
);

alter table public.llm_call_log enable row level security;
create policy "users read own llm log" on public.llm_call_log for select using (auth.uid() = user_id);
