-- Cognitect productization: claim-on-signup RPC
-- Atomically reassigns all anonymous (device_id-owned) plans to a newly
-- authenticated user. Called by the backend via `plan_store.claim_anonymous_plans`.

create or replace function public.claim_anonymous_plans(p_device_id text, p_user_id uuid)
returns table(claimed_count int, plan_ids uuid[])
language plpgsql security definer set search_path = public as $$
declare v_ids uuid[];
begin
  with updated as (
    update public.plans
      set user_id = p_user_id, device_id = null, updated_at = now()
      where device_id = p_device_id and user_id is null
      returning id
  )
  select array_agg(id) into v_ids from updated;

  return query select coalesce(array_length(v_ids, 1), 0), coalesce(v_ids, array[]::uuid[]);
end;
$$;
