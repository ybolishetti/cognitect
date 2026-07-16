"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { AuthModal } from "@/components/auth-modal";
import { PlanEditor } from "@/components/plan-editor/plan-editor";
import { SpecBuilder } from "@/components/plan-editor/spec-builder";
import { CandidateGallery } from "@/components/plan-editor/candidate-gallery";
import { useAuth } from "@/components/providers/auth-provider";
import { generateApi, type FloorPlanSpec, type GenerateLayoutSummary, type GenerateResponse } from "@/lib/api";
import { handle429 } from "@/lib/rate-limit";
import { ANON_PLAN_STORAGE_KEY, DEFAULT_TOP_K } from "@/lib/constants";

// Reconciles two persistence mechanisms the spec docs disagree on: the
// parent spec's ?plan=<id> URL param and the overlay's localStorage key.
// localStorage wins (it's what survives a bare /try visit with no query
// string), and the URL is kept in sync with it for bookmarkability. Neither
// present means a first-time visitor — they land in the structured spec
// builder instead of an auto-created blank plan (see resolvePlan below).
function TryPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user } = useAuth();
  const [planId, setPlanId] = useState<string | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const [specBuilderMode, setSpecBuilderMode] = useState(false);
  const [generationResponse, setGenerationResponse] = useState<GenerateResponse | null>(null);
  const [generating, setGenerating] = useState(false);
  const [materializingRank, setMaterializingRank] = useState<number | null>(null);
  const mountedRef = useRef(true);

  const resolvePlan = useCallback(() => {
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

    if (mountedRef.current) setSpecBuilderMode(true);
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

  const handleGenerate = useCallback(
    async (spec: FloorPlanSpec) => {
      setGenerating(true);
      try {
        const response = await generateApi.generatePlan({ spec, top_k: DEFAULT_TOP_K });
        if (!mountedRef.current) return;
        setGenerationResponse(response);
        try {
          const withLayouts = await generateApi.getGeneratedPlanWithLayouts(response.generated_plan_id);
          if (mountedRef.current) setGenerationResponse(withLayouts);
        } catch {
          // Gallery still renders with the summary response and its fallback
          // placeholder preview — don't block generation on this second call.
        }
      } catch (e) {
        if (handle429(e, { isAnonymous: !user, openAuthModal: () => setAuthOpen(true), router })) return;
        toast.error(e instanceof Error ? e.message : "Generation failed.");
      } finally {
        if (mountedRef.current) setGenerating(false);
      }
    },
    [user, router]
  );

  const handleGenerateAgain = useCallback(() => {
    setGenerationResponse(null);
    setMaterializingRank(null);
  }, []);

  const handleUseCandidate = useCallback(
    async (layout: GenerateLayoutSummary) => {
      if (!generationResponse) return;
      setMaterializingRank(layout.selection_rank);
      try {
        const { plan_id } = await generateApi.materializeCandidate(
          generationResponse.generated_plan_id,
          layout.selection_rank
        );
        if (!user) localStorage.setItem(ANON_PLAN_STORAGE_KEY, plan_id);
        router.push(`/plans/${plan_id}`);
      } catch (e) {
        if (handle429(e, { isAnonymous: !user, openAuthModal: () => setAuthOpen(true), router })) return;
        toast.error(e instanceof Error ? e.message : "Could not open this candidate.");
        if (mountedRef.current) setMaterializingRank(null);
      }
    },
    [generationResponse, user, router]
  );

  const signInBanner = !user && (
    <div className="flex items-center justify-between gap-3 border-b bg-panel px-4 py-2 text-sm">
      <span className="text-muted-foreground">Sign in to save this plan across devices</span>
      <Button size="sm" variant="outline" onClick={() => setAuthOpen(true)}>
        Sign in
      </Button>
    </div>
  );

  if (specBuilderMode) {
    return (
      <div className="flex h-full flex-col overflow-y-auto">
        {signInBanner}
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-8">
          <CandidateGallery
            response={generationResponse}
            onGenerateAgain={handleGenerateAgain}
            onUseCandidate={handleUseCandidate}
            materializingRank={materializingRank}
          />
          <SpecBuilder onSubmit={handleGenerate} disabled={generating} />
        </div>
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
      {signInBanner}
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
