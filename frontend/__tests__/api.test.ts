import { describe, it, expect, vi, beforeEach } from "vitest";
import { getDeviceId } from "@/lib/device-id";

const getSessionMock = vi.fn();

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: { getSession: getSessionMock },
  }),
}));

describe("getDeviceId", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("generates and persists a uuid on first call", () => {
    const id = getDeviceId();
    expect(id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(localStorage.getItem("cognitect_device_id")).toBe(id);
  });

  it("returns the same id on subsequent calls", () => {
    const first = getDeviceId();
    const second = getDeviceId();
    expect(second).toBe(first);
  });
});

describe("authHeaders", () => {
  beforeEach(() => {
    localStorage.clear();
    getSessionMock.mockReset();
  });

  it("includes a Bearer token when a session exists", async () => {
    getSessionMock.mockResolvedValue({
      data: { session: { access_token: "test-token" } },
    });
    const { authHeaders } = await import("@/lib/api");
    const headers = await authHeaders();
    expect(headers["Authorization"]).toBe("Bearer test-token");
    expect(headers["X-Device-Id"]).toBeTruthy();
  });

  it("omits Authorization but still sends X-Device-Id when signed out", async () => {
    getSessionMock.mockResolvedValue({ data: { session: null } });
    const { authHeaders } = await import("@/lib/api");
    const headers = await authHeaders();
    expect(headers["Authorization"]).toBeUndefined();
    expect(headers["X-Device-Id"]).toBeTruthy();
  });
});
