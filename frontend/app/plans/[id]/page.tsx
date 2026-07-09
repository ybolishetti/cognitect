import { notFound } from "next/navigation";
import { getPlanServer } from "@/lib/api-server";
import { ApiError } from "@/lib/api";
import { PlanEditor } from "@/components/plan-editor/plan-editor";

export default async function PlanPage({ params }: { params: { id: string } }) {
  let plan;
  try {
    plan = await getPlanServer(params.id);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) notFound();
    throw e;
  }

  return (
    <div className="h-full">
      <PlanEditor planId={params.id} anonymous={false} initialState={plan} />
    </div>
  );
}
