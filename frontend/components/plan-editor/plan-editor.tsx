"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api, ApiError, type PlanState } from "@/lib/api";
import { AuthModal } from "@/components/auth-modal";
import { EditorControls } from "@/components/plan-editor/editor-controls";
import { EditorPreview } from "@/components/plan-editor/editor-preview";

type PlanEditorProps = {
  planId: string;
  anonymous: boolean;
  initialState?: PlanState;
  /** Only meaningful when anonymous — /try owns the "New Plan" gating logic. */
  onNewPlan?: () => void;
};

export function PlanEditor({ planId, anonymous, initialState, onNewPlan }: PlanEditorProps) {
  const router = useRouter();
  const [plan, setPlan] = useState<PlanState | null>(initialState ?? null);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
  const [authModal, setAuthModal] = useState<{ open: boolean; reason?: string }>({
    open: false,
  });

  const openAuthModal = useCallback((reason?: string) => {
    setAuthModal({ open: true, reason });
  }, []);

  // /try has no server-rendered initial state (no cookie session to SSR
  // with), so it client-fetches on mount instead.
  useEffect(() => {
    if (initialState) return;
    api
      .getPlan(planId)
      .then(setPlan)
      .catch(() => toast.error("Could not load this plan."));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId]);

  const sendInstruction = useCallback(async () => {
    if (!instruction.trim()) return;
    setBusy(true);
    try {
      await api.instruct(planId, instruction.trim());
      const refreshed = await api.getPlan(planId);
      setPlan(refreshed);
      setInstruction("");
      setLastSavedAt(new Date());
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        const hours = Math.ceil((e.retryAfterSeconds ?? 3600) / 3600);
        toast.error(
          `Rate limit reached. Try again in ~${hours}h, or sign in for a higher limit.`,
          anonymous
            ? { action: { label: "Sign in", onClick: () => openAuthModal() } }
            : undefined
        );
      } else {
        toast.error(e instanceof Error ? e.message : "The instruction could not be applied.");
      }
    } finally {
      setBusy(false);
    }
  }, [planId, instruction, anonymous, openAuthModal]);

  const renamePlan = useCallback(
    async (name: string) => {
      if (anonymous) {
        openAuthModal("Sign in to rename plans.");
        return;
      }
      try {
        const { name: savedName } = await api.renamePlan(planId, name);
        setPlan((p) => (p ? { ...p, name: savedName } : p));
        setLastSavedAt(new Date());
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Could not rename this plan.");
      }
    },
    [planId, anonymous, openAuthModal]
  );

  const handleNewPlan = useCallback(() => {
    if (!anonymous) return;
    if (onNewPlan) onNewPlan();
    else openAuthModal("Sign in to create another plan.");
  }, [anonymous, onNewPlan, openAuthModal]);

  const handleUpload = useCallback(
    async (file: File) => {
      if (anonymous) return; // button shouldn't render, but belt-and-suspenders
      try {
        const { plan_id } = await api.uploadPlan(file);
        toast.success("Plan uploaded.");
        router.push(`/plans/${plan_id}`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 413) {
          toast.error("File too large (max 10 MB).");
        } else {
          toast.error(e instanceof Error ? e.message : "Upload failed.");
        }
      }
    },
    [anonymous, router]
  );

  return (
    <div className="flex h-full w-full overflow-hidden">
      <EditorControls
        anonymous={anonymous}
        planId={planId}
        planName={plan?.name ?? ""}
        version={plan?.version ?? 0}
        rooms={plan?.rooms ?? {}}
        instruction={instruction}
        onInstructionChange={setInstruction}
        onSubmitInstruction={sendInstruction}
        busy={busy}
        saving={busy}
        lastSavedAt={lastSavedAt}
        onRename={renamePlan}
        onNewPlan={handleNewPlan}
        onUpload={handleUpload}
        onRequestAuth={openAuthModal}
      />
      <EditorPreview planId={planId} version={plan?.version ?? null} />
      <AuthModal
        open={authModal.open}
        onOpenChange={(open) => setAuthModal((s) => ({ ...s, open }))}
        reason={authModal.reason}
      />
    </div>
  );
}
