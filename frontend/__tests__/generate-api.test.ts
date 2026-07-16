import { describe, it, expect, vi, beforeEach } from "vitest";

const getSessionMock = vi.fn();

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: { getSession: getSessionMock },
  }),
}));

describe("generateApi", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    getSessionMock.mockReset();
    getSessionMock.mockResolvedValue({ data: { session: null } });
  });

  it("POSTs the spec + top_k to /v2/plans/generate with the right headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({
        generated_plan_id: "gp1",
        spec_hash: "hash",
        generator_name: "stub",
        generator_version: "2026-07-14",
        total_candidates: 1,
        survived_layer_a: 1,
        survived_layer_c: 1,
        elapsed_ms: 11,
        layouts: [{ selection_rank: 0, user_score: 0.71, plan_id: "p1" }],
        cached: false,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { generateApi } = await import("@/lib/api");
    const req = {
      spec: {
        spec_id: "spec_abc123",
        original_nl: "test",
        room_requirements: [{ name: "Bedroom 1", room_type: "bedroom" as const }],
        n_candidates: 8,
      },
      top_k: 4,
    };
    const result = await generateApi.generatePlan(req);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/v2\/plans\/generate$/);
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.headers["X-Device-Id"]).toBeTruthy();
    expect(JSON.parse(init.body)).toEqual(req);
    expect(result.generated_plan_id).toBe("gp1");
  });

  it("GETs /v2/plans/generate/{id} with no body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        generated_plan_id: "gp1",
        spec_hash: "hash",
        generator_name: "stub",
        generator_version: "2026-07-14",
        total_candidates: 1,
        survived_layer_a: 1,
        survived_layer_c: 1,
        elapsed_ms: 5,
        layouts: [],
        cached: true,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { generateApi } = await import("@/lib/api");
    await generateApi.getGeneratedPlan("gp1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/v2\/plans\/generate\/gp1$/);
    expect(init.body).toBeUndefined();
  });

  it("throws an ApiError with status 422 on a validation error response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      headers: { get: () => null },
      json: async () => ({ detail: [{ msg: "Unterminated string" }] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { generateApi, ApiError } = await import("@/lib/api");
    let error: unknown;
    try {
      await generateApi.generatePlan({
        spec: {
          spec_id: "spec_abc",
          original_nl: "test",
          room_requirements: [{ name: "Bedroom 1", room_type: "bedroom" as const }],
        },
      });
    } catch (e) {
      error = e;
    }
    expect(error).toBeInstanceOf(ApiError);
    expect((error as InstanceType<typeof ApiError>).status).toBe(422);
  });
});

describe("generateSpecId", () => {
  it("produces ids matching the backend regex ^spec_[a-z0-9_]+$", async () => {
    const { generateSpecId } = await import("@/lib/api");
    const id = generateSpecId();
    expect(id).toMatch(/^spec_[a-z0-9_]+$/);
  });

  it("produces different ids across calls", async () => {
    const { generateSpecId } = await import("@/lib/api");
    const a = generateSpecId();
    const b = generateSpecId();
    expect(a).not.toBe(b);
  });
});
