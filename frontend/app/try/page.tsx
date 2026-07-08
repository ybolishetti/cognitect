"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AuthModal } from "@/components/auth-modal";
import { PlanEditor } from "@/components/plan-editor/plan-editor";
import { api } from "@/lib/api";
import { ANON_PLAN_STORAGE_KEY } from "@/lib/constants";

// Reconciles two persistence mechanisms the spec docs disagree on: the
// parent spec's ?plan=<id> URL param and the overlay's localStorage key.
// localStorage wins (it's what survives a bare /try visit with no query
// string), and the URL is kept in sync with it for bookmarkability.
function TryPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [planId, setPlanId] = useState<string | null>(null);
  const [authOpen, setAuthOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function resolvePlan() {
      const stored = localStorage.getItem(ANON_PLAN_STORAGE_KEY);
      if (stored) {
        if (searchParams.get("plan") !== stored) router.replace(`/try?plan=${stored}`);
        if (!cancelled) setPlanId(stored);
        return;
      }

      const fromUrl = searchParams.get("plan");
      if (fromUrl) {
        localStorage.setItem(ANON_PLAN_STORAGE_KEY, fromUrl);
        if (!cancelled) setPlanId(fromUrl);
        return;
      }

      const { plan_id } = await api.createPlan();
      localStorage.setItem(ANON_PLAN_STORAGE_KEY, plan_id);
      router.replace(`/try?plan=${plan_id}`);
      if (!cancelled) setPlanId(plan_id);
    }

    resolvePlan();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!planId) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b bg-panel px-4 py-2 text-sm">
        <span className="text-muted-foreground">Sign in to save this plan across devices</span>
        <Button size="sm" variant="outline" onClick={() => setAuthOpen(true)}>
          Sign in
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        <PlanEditor planId={planId} anonymous />
      </div>
      <AuthModal open={authOpen} onOpenChange={setAuthOpen} />
    </div>
  );
}

export default function TryPage() {
  return (
    <Suspense fallback={null}>
      <TryPageInner />
    </Suspense>
  );
}
