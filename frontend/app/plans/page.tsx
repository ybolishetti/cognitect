import type { Metadata } from "next";
import Link from "next/link";
import { listPlansServer } from "@/lib/api-server";
import { PlansList } from "@/components/plans-list";
import { NewPlanButton } from "@/components/new-plan-button";
import { UploadPlanButton } from "@/components/upload-plan-button";

export const metadata: Metadata = {
  title: "Your Plans",
  description: "View, manage, and continue editing your saved floor plans.",
};

export default async function PlansPage() {
  const plans = await listPlansServer();

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Your plans</h1>
        <div className="flex gap-2">
          <UploadPlanButton />
          <NewPlanButton />
        </div>
      </div>

      {plans.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed py-20 text-center">
          <h2 className="text-lg font-medium">No plans yet</h2>
          <div className="flex gap-2">
            <NewPlanButton />
            <UploadPlanButton />
          </div>
          <p className="text-sm text-muted-foreground">
            Or{" "}
            <Link href="/try" className="underline underline-offset-4">
              try it without signing in
            </Link>
          </p>
        </div>
      ) : (
        <PlansList initialPlans={plans} />
      )}
    </main>
  );
}
