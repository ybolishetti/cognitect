import { createClient } from "@/lib/supabase/client";
import { getDeviceId } from "@/lib/device-id";

const API_URL = process.env.NEXT_PUBLIC_API_URL!;

// ── Types (match the actual FastAPI response models in api/routes/plans_v2.py) ──

// GET /v2/plans — note the id field here is `id`, NOT `plan_id` like every
// other endpoint below. That's a real inconsistency in the backend, not a
// typo — don't "fix" it by renaming, it needs to match the wire format.
export type PlanListItem = {
  id: string;
  name: string;
  room_count: number;
  version: number;
  thumbnail_url: string | null; // always null in Phase 1, no generator exists
  created_at: string;
  updated_at: string;
  last_opened_at: string;
  archived: boolean;
};

export type PlanState = {
  plan_id: string;
  name: string;
  version: number;
  room_count: number;
  rooms: Record<string, { name: string; room_type: string; area_sqft: number | null }>;
  connections: Array<{ room_a: string; room_b: string; type: string }>;
};

export type CreatePlanResponse = {
  plan_id: string;
  name: string;
  message: string;
};

export type InstructResponse = {
  plan_id: string;
  ops_applied: number;
  op_types: string[];
  room_count: number;
  version: number;
  coordinate_matrix: Record<string, unknown>;
  message: string;
};

export type ClaimResponse = {
  claimed_count: number;
  plan_ids: string[];
};

// PATCH /v2/plans/{id} returns a plain dict, not a full PlanState.
export type RenamePlanResponse = {
  plan_id: string;
  name: string;
};

// ── Fetch plumbing ──

export async function authHeaders(): Promise<Record<string, string>> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const h: Record<string, string> = { "X-Device-Id": getDeviceId() };
  if (session?.access_token) h["Authorization"] = `Bearer ${session.access_token}`;
  return h;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public retryAfterSeconds?: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// FastAPI's `detail` is a plain string for hand-raised HTTPExceptions (e.g.
// 401/403/404/429), but a required-but-missing header (e.g. a dropped
// Authorization header on a require_user route) fails FastAPI's own request
// validation first and comes back as a 422 with `detail` as an array of
// {loc, msg, type} objects instead — verified against the live backend.
export function extractErrorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === "object" && d && "msg" in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
      .join("; ");
  }
  return `HTTP ${status}`;
}

export async function apiFetch(path: string, init: RequestInit = {}) {
  const headers = { ...(init.headers || {}), ...(await authHeaders()) };
  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const retryAfter = res.headers.get("Retry-After");
    throw new ApiError(
      res.status,
      extractErrorMessage(body, res.status),
      retryAfter ? parseInt(retryAfter, 10) : undefined
    );
  }
  if (res.status === 204) return null; // 204 No Content — DELETE
  return res.json();
}

// ── Typed helpers ──

export const api = {
  createPlan: (name?: string): Promise<CreatePlanResponse> =>
    apiFetch("/v2/plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // backend defaults to "Untitled Plan" server-side; sending
      // {"name": null} instead of omitting it would 422, so guard here.
      body: JSON.stringify(name ? { name } : {}),
    }),
  listPlans: (): Promise<PlanListItem[]> => apiFetch("/v2/plans"),
  getPlan: (id: string): Promise<PlanState> => apiFetch(`/v2/plans/${id}`),
  instruct: (id: string, instruction: string): Promise<InstructResponse> =>
    apiFetch(`/v2/plans/${id}/instruct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction }),
    }),
  renamePlan: (id: string, name: string): Promise<RenamePlanResponse> =>
    apiFetch(`/v2/plans/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  deletePlan: (id: string): Promise<null> =>
    apiFetch(`/v2/plans/${id}`, { method: "DELETE" }),
  claimAnonymous: (): Promise<ClaimResponse> =>
    apiFetch("/v2/plans/claim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: getDeviceId() }),
    }),
  // Do NOT set Content-Type here — the browser must set the multipart
  // boundary itself. apiFetch only merges in auth headers, so this is safe.
  uploadPlan: (file: File, name?: string): Promise<CreatePlanResponse> => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    return apiFetch("/v2/plans/upload", { method: "POST", body: form });
  },
};

// ── Preview / export ──
//
// GET /v2/plans/{id}/preview and /export both require an auth header
// (Bearer or X-Device-Id). A plain <img src> or <a href> can't attach
// custom headers, so both are fetched via apiFetch's auth plumbing and
// converted to blob: URLs. Caller owns the returned URL and must call
// URL.revokeObjectURL on it when done (see lib/hooks/use-preview-blob.ts).

export async function fetchPreviewBlob(planId: string, w = 900, h = 700): Promise<string> {
  const headers = await authHeaders();
  const res = await fetch(`${API_URL}/v2/plans/${planId}/preview?width=${w}&height=${h}`, {
    headers,
  });
  if (!res.ok) throw new Error(`Preview failed: ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function downloadExport(planId: string, format: "dxf" | "pdf"): Promise<void> {
  const headers = await authHeaders();
  const res = await fetch(`${API_URL}/v2/plans/${planId}/export?format=${format}`, {
    headers,
  });
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${planId}.${format}`;
  a.click();
  URL.revokeObjectURL(url);
}
