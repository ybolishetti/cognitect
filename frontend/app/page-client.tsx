"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { PenTool, Sparkles, Download, Github } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AuthModal } from "@/components/auth-modal";
import { BlueprintBackground } from "@/components/blueprint-bg";

const FEATURES = [
  {
    icon: PenTool,
    title: "Specify",
    description: "List the rooms you need — size, type, adjacencies — and let the pipeline do the layout work.",
  },
  {
    icon: Sparkles,
    title: "Refine",
    description: "Keep instructing — resize, add, remove — the layout updates live.",
  },
  {
    icon: Download,
    title: "Export",
    description: "Download a precise DXF or PDF, ready for your CAD software.",
  },
];

// middleware redirects unauthenticated /plans and /account visits here with
// ?auth=1 so the sign-in modal opens automatically. useSearchParams() opts a
// page out of static rendering unless isolated behind Suspense, so this is
// split out rather than called directly in the page body.
function AuthQueryParamBridge({ onAuthParam }: { onAuthParam: () => void }) {
  const searchParams = useSearchParams();
  useEffect(() => {
    if (searchParams.get("auth") === "1") onAuthParam();
  }, [searchParams, onAuthParam]);
  return null;
}

export function LandingPageClient() {
  const [authOpen, setAuthOpen] = useState(false);

  return (
    <main className="relative overflow-hidden">
      <Suspense fallback={null}>
        <AuthQueryParamBridge onAuthParam={() => setAuthOpen(true)} />
      </Suspense>
      <BlueprintBackground />

      <section className="mx-auto flex max-w-3xl flex-col items-center gap-6 px-6 pb-20 pt-28 text-center sm:pt-36">
        <h1 className="text-4xl font-bold tracking-tight text-brand sm:text-5xl">
          Design floor plans in seconds.
        </h1>
        <p className="max-w-xl text-lg text-muted-foreground">
          Describe your rooms. Cognitect generates 4 candidates, validates each against
          building code, and shows you the best.
        </p>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg">
            <Link href="/try">Try it now</Link>
          </Button>
          <Button size="lg" variant="outline" onClick={() => setAuthOpen(true)}>
            Sign in with Google
          </Button>
        </div>
        <span className="rounded-full border px-3 py-1 text-xs text-muted-foreground">
          Powered by kiwisolver + IRC-2021 verifiers
        </span>
        <Link href="/pricing" className="text-sm text-muted-foreground hover:text-foreground">
          See pricing
        </Link>
      </section>

      <section className="mx-auto grid max-w-4xl gap-8 px-6 pb-24 sm:grid-cols-3">
        {FEATURES.map(({ icon: Icon, title, description }) => (
          <div key={title} className="flex flex-col items-center gap-3 text-center">
            <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Icon className="h-5 w-5" />
            </div>
            <h2 className="font-semibold">{title}</h2>
            <p className="text-sm text-muted-foreground">{description}</p>
          </div>
        ))}
      </section>

      <section className="mx-auto mt-24 max-w-4xl px-6">
        <h2 className="text-3xl font-bold tracking-tight text-brand">
          For architects and builders
        </h2>
        <p className="mt-3 text-lg text-muted-foreground">
          Built for professionals who need code-compliant, dimensionally-precise output.
        </p>
        <div className="mt-8 grid grid-cols-1 gap-6 md:grid-cols-3">
          <div>
            <h3 className="font-semibold">IRC-2021 verified</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              Every layout auto-checked for exit reachability, exterior door presence,
              egress windows per bedroom, and minimum room areas per jurisdiction.
            </p>
          </div>
          <div>
            <h3 className="font-semibold">Geometry-verified</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              Layer A geometry gate catches overlapping rooms, dangling walls,
              and invalid polygons before you ever see them.
            </p>
          </div>
          <div>
            <h3 className="font-semibold">CAD-ready export</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              Precise DXF and PDF output using kiwisolver constraint math —
              ready for AutoCAD, Revit, and your existing workflow.
            </p>
          </div>
        </div>
      </section>

      <footer className="border-t px-6 py-8">
        <div className="mx-auto flex max-w-4xl flex-col items-center justify-between gap-4 text-sm text-muted-foreground sm:flex-row">
          <div className="flex items-center gap-4">
            <a
              href="https://github.com/ybolishetti/cognitect"
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 hover:text-foreground"
            >
              <Github className="h-4 w-4" />
              GitHub
            </a>
            <Link href="/pricing" className="hover:text-foreground">
              Pricing
            </Link>
          </div>
          <div className="flex gap-4">
            <Link href="/privacy" className="hover:text-foreground">
              Privacy
            </Link>
            <Link href="/terms" className="hover:text-foreground">
              Terms
            </Link>
          </div>
        </div>
      </footer>

      <AuthModal open={authOpen} onOpenChange={setAuthOpen} />
    </main>
  );
}
