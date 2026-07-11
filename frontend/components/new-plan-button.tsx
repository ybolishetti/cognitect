"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { AuthModal } from "@/components/auth-modal";
import { useAuth } from "@/components/providers/auth-provider";
import { api } from "@/lib/api";
import { handle429 } from "@/lib/rate-limit";

export function NewPlanButton({
  variant = "default",
}: {
  variant?: "default" | "outline";
}) {
  const router = useRouter();
  const { user } = useAuth();
  const [creating, setCreating] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);

  const handleClick = async () => {
    setCreating(true);
    try {
      const { plan_id } = await api.createPlan();
      router.push(`/plans/${plan_id}`);
    } catch (e) {
      if (handle429(e, { isAnonymous: !user, openAuthModal: () => setAuthOpen(true), router })) return;
      toast.error(e instanceof Error ? e.message : "Could not create a new plan.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <>
      <Button onClick={handleClick} disabled={creating} variant={variant}>
        New Plan
      </Button>
      <AuthModal open={authOpen} onOpenChange={setAuthOpen} />
    </>
  );
}
