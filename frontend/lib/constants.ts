// Matches engine/previewer.py ROOM_COLORS so the sidebar dots align with the canvas.
export const ROOM_COLORS: Record<string, string> = {
  bedroom: "#AED6F1",
  bathroom: "#A9DFBF",
  kitchen: "#FAD7A0",
  living: "#F9E79F",
  dining: "#F5CBA7",
  hallway: "#D7DBDD",
  office: "#D2B4DE",
  garage: "#BFC9CA",
  other: "#EAEDED",
};

export const EXAMPLES = [
  "A 12×14 bedroom next to a bathroom",
  "3-bedroom apartment with an open kitchen and living room",
  "L-shaped studio with a 10×10 sleeping alcove",
  "Two-bedroom cottage, master ensuite, shared bath, 900 sqft total",
];

export const ANON_PLAN_STORAGE_KEY = "cognitect_current_anon_plan";

// Architecture C — matches RoomRequirement.room_type enum in FloorPlanSpec.
// Keep in sync with backend at engine/layout/spec.py:RoomType.
export const ROOM_TYPES = [
  "bedroom",
  "bathroom",
  "kitchen",
  "living",
  "dining",
  "hallway",
  "office",
  "garage",
  "closet",
  "utility",
  "other",
] as const;
export type RoomType = (typeof ROOM_TYPES)[number];

// Display labels for room_type dropdowns. Wire format uses the raw enum value;
// this map is UI-only.
export const ROOM_TYPE_LABELS: Record<RoomType, string> = {
  bedroom: "Bedroom",
  bathroom: "Bathroom",
  kitchen: "Kitchen",
  living: "Living room",
  dining: "Dining room",
  hallway: "Hallway",
  office: "Office",
  garage: "Garage",
  closet: "Closet",
  utility: "Utility",
  other: "Other",
};

// Default top_k for the /v2/plans/generate call. 4 gives a demo-friendly
// best-of-N gallery without paying to render 8 candidates.
export const DEFAULT_TOP_K = 4;

// Default number of candidates the pipeline should GENERATE (inside spec).
// Backend caps at 32; 4 is the demo-friendly candidate count.
export const DEFAULT_N_CANDIDATES = 4;

// Set NEXT_PUBLIC_ENABLE_MATERIALIZE=true in Vercel env once the backend
// image with POST /v2/plans/generate/{gpid}/materialize is deployed to
// production and migration 006 has been applied to Supabase.
export const ENABLE_MATERIALIZE =
  process.env.NEXT_PUBLIC_ENABLE_MATERIALIZE === "true";
