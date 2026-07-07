-- Cognitect productization: initial schema
-- Applies to: Supabase Postgres (run in SQL Editor or via `supabase db push`)

create extension if not exists "uuid-ossp";
create extension if not exists "pgcrypto";

create table public.consumer_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.plans (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid references auth.users(id) on delete cascade,
  device_id text,
  name text not null default 'Untitled Plan',
  state_json jsonb not null,
  version int not null default 1,
  room_count int not null default 0,
  thumbnail_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_opened_at timestamptz not null default now(),
  archived boolean not null default false,
  constraint plans_owner_check check (user_id is not null or device_id is not null)
);

create index plans_user_id_idx on public.plans(user_id) where user_id is not null;
create index plans_device_id_idx on public.plans(device_id) where device_id is not null and user_id is null;
create index plans_updated_at_idx on public.plans(updated_at desc);

create table public.plan_versions (
  id uuid primary key default uuid_generate_v4(),
  plan_id uuid not null references public.plans(id) on delete cascade,
  version int not null,
  state_json jsonb not null,
  instruction text,
  created_at timestamptz not null default now(),
  unique(plan_id, version)
);
create index plan_versions_plan_id_idx on public.plan_versions(plan_id, version desc);

create table public.llm_call_log (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid references auth.users(id) on delete set null,
  device_id text,
  plan_id uuid references public.plans(id) on delete set null,
  model text not null,
  prompt_tokens int,
  completion_tokens int,
  latency_ms int,
  status text not null,
  error_message text,
  created_at timestamptz not null default now()
);
create index llm_call_log_user_id_idx on public.llm_call_log(user_id, created_at desc);
create index llm_call_log_device_id_idx on public.llm_call_log(device_id, created_at desc);

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.consumer_profiles (id, email, display_name)
  values (new.id, new.email,
    coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name', split_part(new.email, '@', 1)));
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end;
$$;

create trigger plans_updated_at before update on public.plans
  for each row execute function public.set_updated_at();
create trigger consumer_profiles_updated_at before update on public.consumer_profiles
  for each row execute function public.set_updated_at();
