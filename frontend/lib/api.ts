import { createClient } from "@/lib/supabase/client";
import { getDeviceId } from "@/lib/device-id";
import type { RoomType } from "@/lib/constants";

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

// ── Architecture C — FloorPlanSpec + generate flow ──
// Wire format matches /v2/plans/generate on the backend. See:
// engine/layout/spec.py and api/routes/plans_v2_generate.py

export type RoomRequirement = {
  name: string;
  room_type: RoomType;
  min_area_sqft?: number;
  max_area_sqft?: number;
  preferred_area_sqft?: number;
  aspect_ratio?: number;
  adjacencies?: string[];
  metadata?: Record<string, unknown>;
};

export type SiteConstraints = {
  lot_width_ft?: number;
  lot_depth_ft?: number;
  setback_front_ft?: number;
  setback_rear_ft?: number;
  setback_side_ft?: number;
  max_footprint_sqft?: number;
  jurisdiction?: string; // default "IRC-2021"
  north_bearing_deg?: number; // default 0
};

export type FloorPlanSpec = {
  spec_id: string; // must match /^spec_[a-z0-9_]+$/
  original_nl: string;
  room_requirements: RoomRequirement[];
  site_constraints?: SiteConstraints;
  n_candidates?: number;
  metadata?: Record<string, unknown>;
};

export type GenerateLayoutSummary = {
  selection_rank: number;
  user_score: number | null;
  plan_id: string;
};

export type GenerateResponse = {
  generated_plan_id: string;
  spec_hash: string;
  generator_name: string;
  generator_version: string;
  total_candidates: number;
  survived_layer_a: number;
  survived_layer_c: number;
  elapsed_ms: number;
  layouts: GenerateLayoutSummary[];
  cached: boolean;
  layouts_full?: LayoutWithRank[];
};

export type GenerateRequest = {
  spec: FloorPlanSpec;
  top_k?: number; // default 1, max 8
  force_regenerate?: boolean;
};

// ── Architecture C — Layout geometry (full Layout returned by ?include=layout) ──
// Wire format matches engine/layout/schemas.py:Layout
export type Vertex = [number, number];

export type LayoutRoom = {
  id: string;
  name: string;
  room_type: string; // matches RoomType but backend may add more values later
  vertices: Vertex[]; // closed polygon (first == last), CCW
  area_sqft: number;
  boundary_wall_ids: string[];
  ceiling_height_ft?: number;
  metadata?: Record<string, unknown>;
};

export type LayoutWall = {
  id: string;
  start: Vertex;
  end: Vertex;
  thickness_ft?: number;
  bounds_rooms: string[]; // 0, 1, or 2 room ids
  is_load_bearing?: boolean | null;
  metadata?: Record<string, unknown>;
};

export type LayoutOpening = {
  id: string;
  opening_type: "door" | "window" | "archway" | "wall_opening";
  wall_id: string;
  offset_ft: number;
  width_ft: number;
  sill_height_ft?: number;
  height_ft?: number;
  swings_into_room_id?: string | null;
};

export type Layout = {
  plan_id: string;
  schema_version: string;
  rooms: LayoutRoom[];
  walls: LayoutWall[];
  openings: LayoutOpening[];
  extent_x_ft: number;
  extent_y_ft: number;
};

export type LayoutWithRank = {
  selection_rank: number;
  user_score: number | null;
  layout: Layout;
};

// Separate export from `api` — do not merge these into the `api` const above.
export const generateApi = {
  generatePlan: (req: GenerateRequest): Promise<GenerateResponse> =>
    apiFetch("/v2/plans/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  getGeneratedPlan: (id: string): Promise<GenerateResponse> =>
    apiFetch(`/v2/plans/generate/${id}`),
  getGeneratedPlanWithLayouts: (id: string): Promise<GenerateResponse> =>
    apiFetch(`/v2/plans/generate/${id}?include=layout`),
  materializeCandidate: (
    generatedPlanId: string,
    selectionRank: number,
    name?: string
  ): Promise<{ plan_id: string; name: string; materialized_from_layout_id: string; created: boolean }> =>
    apiFetch(`/v2/plans/generate/${generatedPlanId}/materialize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selection_rank: selectionRank, ...(name ? { name } : {}) }),
    }),
};

// Backend regex: ^spec_[a-z0-9_]+$ — hyphens from crypto.randomUUID() must
// become underscores, and the whole thing must be lowercase.
export function generateSpecId(): string {
  const raw =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
  return `spec_${raw.toLowerCase().replace(/-/g, "_")}`;
}
