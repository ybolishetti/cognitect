"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { AuthModal } from "@/components/auth-modal";
import { PlanEditor } from "@/components/plan-editor/plan-editor";
import { useAuth } from "@/components/providers/auth-provider";
import { api } from "@/lib/api";
import { handle429 } from "@/lib/rate-limit";
import { ANON_PLAN_STORAGE_KEY } from "@/lib/constants";

// Reconciles two persistence mechanisms the spec docs disagree on: the
// parent spec's ?plan=<id> URL param and the overlay's localStorage key.
// localStorage wins (it's what survives a bare /try visit with no query
// string), and the URL is kept in sync with it for bookmarkability.
function TryPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user } = useAuth();
  const [planId, setPlanId] = useState<string | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const mountedRef = useRef(true);

  const resolvePlan = useCallback(async () => {
    setLoadError(false);
    const stored = localStorage.getItem(ANON_PLAN_STORAGE_KEY);
    if (stored) {
      if (searchParams.get("plan") !== stored) router.replace(`/try?plan=${stored}`);
      if (mountedRef.current) setPlanId(stored);
      return;
    }

    const fromUrl = searchParams.get("plan");
    if (fromUrl) {
      localStorage.setItem(ANON_PLAN_STORAGE_KEY, fromUrl);
      if (mountedRef.current) setPlanId(fromUrl);
      return;
    }

    try {
      const { plan_id } = await api.createPlan();
      localStorage.setItem(ANON_PLAN_STORAGE_KEY, plan_id);
      router.replace(`/try?plan=${plan_id}`);
      if (mountedRef.current) setPlanId(plan_id);
    } catch (e) {
      if (!mountedRef.current) return;
      if (handle429(e, { isAnonymous: true, openAuthModal: () => setAuthOpen(true), router })) {
        setLoadError(true);
        return;
      }
      toast.error(e instanceof Error ? e.message : "Could not start a new plan.");
      setLoadError(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    resolvePlan();
    return () => {
      mountedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loadError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
        <p className="text-sm text-muted-foreground">Could not start a new plan.</p>
        <Button size="sm" variant="outline" onClick={() => resolvePlan()}>
          Try again
        </Button>
        <AuthModal open={authOpen} onOpenChange={setAuthOpen} />
      </div>
    );
  }

  if (!planId) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {!user && (
        <div className="flex items-center justify-between gap-3 border-b bg-panel px-4 py-2 text-sm">
          <span className="text-muted-foreground">Sign in to save this plan across devices</span>
          <Button size="sm" variant="outline" onClick={() => setAuthOpen(true)}>
            Sign in
          </Button>
        </div>
      )}
      <div className="min-h-0 flex-1">
        <PlanEditor planId={planId} anonymous />
      </div>
      <AuthModal open={authOpen} onOpenChange={setAuthOpen} />
    </div>
  );
}

export function TryPageClient() {
  return (
    <Suspense fallback={null}>
      <TryPageInner />
    </Suspense>
  );
}
