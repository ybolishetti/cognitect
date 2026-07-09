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
  "Add a 300 sqft living room",
  "Add a kitchen next to the living room, 150 sqft",
  "Add a master bedroom of 200 sqft",
  "Make the kitchen bigger",
  "Remove the hallway",
];

export const ANON_PLAN_STORAGE_KEY = "cognitect_current_anon_plan";
