import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function PlanNotFound() {
  return (
    <main className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="text-2xl font-semibold">Plan not found</h1>
      <p className="max-w-sm text-muted-foreground">
        This plan doesn&apos;t exist, or it doesn&apos;t belong to your account.
      </p>
      <Button asChild>
        <Link href="/plans">Back to your plans</Link>
      </Button>
    </main>
  );
}
