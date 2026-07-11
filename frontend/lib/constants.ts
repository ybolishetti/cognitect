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
