"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

export function NewPlanButton({
  variant = "default",
}: {
  variant?: "default" | "outline";
}) {
  const router = useRouter();
  const [creating, setCreating] = useState(false);

  const handleClick = async () => {
    setCreating(true);
    try {
      const { plan_id } = await api.createPlan();
      router.push(`/plans/${plan_id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create a new plan.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <Button onClick={handleClick} disabled={creating} variant={variant}>
      New Plan
    </Button>
  );
}
