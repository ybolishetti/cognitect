import { createClient } from "@/lib/supabase/server";
import { ApiError, type PlanListItem, type PlanState } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL!;

// Server-component counterpart to lib/api.ts's authHeaders/apiFetch. The
// browser client's authHeaders() calls supabase.auth.getSession() on a
// client-side Supabase instance, which can't run in a Server Component —
// this reads the same session from the cookie-bound server client instead.
// Anonymous (device-id) plans aren't reachable this way since there's no
// server-side concept of the browser's localStorage device id; that's why
// GET /v2/plans requires an authenticated user anyway (see api/auth.py),
// and /plans/[id] here is only used for the authed route, not /try.
async function serverAuthHeaders(): Promise<Record<string, string>> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const h: Record<string, string> = {};
  if (session?.access_token) h["Authorization"] = `Bearer ${session.access_token}`;
  return h;
}

async function serverFetch(path: string) {
  const headers = await serverAuthHeaders();
  const res = await fetch(`${API_URL}${path}`, { headers, cache: "no-store" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body?.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const listPlansServer = (): Promise<PlanListItem[]> => serverFetch("/v2/plans");

export const getPlanServer = (id: string): Promise<PlanState> => serverFetch(`/v2/plans/${id}`);
