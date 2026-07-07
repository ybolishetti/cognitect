-- Cognitect productization: version history trimming
-- Called opportunistically by the backend after each save (api/storage/plan_store.py
-- calls this with p_keep=50) rather than as a scheduled job.

create or replace function public.trim_plan_versions(p_plan_id uuid, p_keep int)
returns void language plpgsql as $$
begin
  delete from public.plan_versions
  where plan_id = p_plan_id
    and id not in (
      select id from public.plan_versions
      where plan_id = p_plan_id
      order by version desc
      limit p_keep
    );
end;
$$;
