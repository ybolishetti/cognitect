import { v4 as uuidv4 } from "uuid";

const KEY = "cognitect_device_id";

// Anonymous ownership = an X-Device-Id header with a parseable UUID string.
// Generated once per browser and persisted so an anonymous user's plans
// stay associated with this device across visits.
export function getDeviceId(): string {
  if (typeof window === "undefined") return "";
  let id = localStorage.getItem(KEY);
  if (!id) {
    id = uuidv4();
    localStorage.setItem(KEY, id);
  }
  return id;
}
