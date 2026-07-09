import { describe, it, expect, vi, beforeEach } from "vitest";

const getSessionMock = vi.fn();

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: { getSession: getSessionMock },
  }),
}));

describe("api.uploadPlan", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    getSessionMock.mockReset();
    getSessionMock.mockResolvedValue({ data: { session: null } });
  });

  it("sends a multipart FormData body with no Content-Type header, plus auth headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ plan_id: "abc123", name: "plan", message: "Uploaded 1 room(s)." }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await import("@/lib/api");
    const file = new File(["dummy"], "plan.json", { type: "application/json" });
    await api.uploadPlan(file);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/v2\/plans\/upload$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBe(file);
    expect(init.headers["X-Device-Id"]).toBeTruthy();
    expect(init.headers["Content-Type"]).toBeUndefined();
  });

  it("appends name to the form when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ plan_id: "abc123", name: "My Plan", message: "Uploaded 1 room(s)." }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await import("@/lib/api");
    const file = new File(["dummy"], "plan.dxf");
    await api.uploadPlan(file, "My Plan");

    const [, init] = fetchMock.mock.calls[0];
    expect((init.body as FormData).get("name")).toBe("My Plan");
  });

  it("parses a successful 201 response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ plan_id: "abc123", name: "plan", message: "Uploaded 1 room(s)." }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await import("@/lib/api");
    const result = await api.uploadPlan(new File(["dummy"], "plan.json"));
    expect(result).toEqual({ plan_id: "abc123", name: "plan", message: "Uploaded 1 room(s)." });
  });

  it("throws an ApiError with status 413 on an oversized-file response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 413,
      headers: { get: () => null },
      json: async () => ({ detail: "File too large. Max 10 MB." }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { api, ApiError } = await import("@/lib/api");
    let error: unknown;
    try {
      await api.uploadPlan(new File(["dummy"], "plan.json"));
    } catch (e) {
      error = e;
    }
    expect(error).toBeInstanceOf(ApiError);
    expect((error as InstanceType<typeof ApiError>).status).toBe(413);
    expect((error as InstanceType<typeof ApiError>).message).toBe("File too large. Max 10 MB.");
  });
});
