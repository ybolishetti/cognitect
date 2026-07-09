import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, act } from "@testing-library/react";
import { AuthProvider } from "@/components/providers/auth-provider";

// vi.mock factories are hoisted above the rest of the file, so anything
// they reference must be created via vi.hoisted() rather than a plain
// top-level const/let (which would still be in the temporal dead zone
// when the hoisted factory runs).
const { claimAnonymousMock, callbackHolder } = vi.hoisted(() => ({
  claimAnonymousMock: vi.fn(),
  callbackHolder: { current: null as ((event: string, session: unknown) => void) | null },
}));

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session: null } }),
      onAuthStateChange: (cb: (event: string, session: unknown) => void) => {
        callbackHolder.current = cb;
        return { data: { subscription: { unsubscribe: vi.fn() } } };
      },
      signInWithOAuth: vi.fn(),
      signOut: vi.fn(),
    },
  }),
}));

vi.mock("@/lib/api", () => ({
  api: { claimAnonymous: claimAnonymousMock },
  ApiError: class ApiError extends Error {},
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

describe("AuthProvider", () => {
  beforeEach(() => {
    claimAnonymousMock.mockReset();
    claimAnonymousMock.mockResolvedValue({ claimed_count: 1, plan_ids: ["p1"] });
    callbackHolder.current = null;
  });

  it("claims anonymous plans exactly once on SIGNED_IN", async () => {
    render(
      <AuthProvider>
        <div />
      </AuthProvider>
    );

    await waitFor(() => expect(callbackHolder.current).not.toBeNull());

    act(() => {
      callbackHolder.current!("SIGNED_IN", { user: { id: "u1" }, access_token: "t" });
    });

    await waitFor(() => expect(claimAnonymousMock).toHaveBeenCalledTimes(1));
  });

  it("does not claim on INITIAL_SESSION (returning signed-in user)", async () => {
    render(
      <AuthProvider>
        <div />
      </AuthProvider>
    );

    await waitFor(() => expect(callbackHolder.current).not.toBeNull());

    act(() => {
      callbackHolder.current!("INITIAL_SESSION", { user: { id: "u1" }, access_token: "t" });
    });

    // give any stray microtask a chance to run before asserting the negative
    await new Promise((r) => setTimeout(r, 0));
    expect(claimAnonymousMock).not.toHaveBeenCalled();
  });
});
