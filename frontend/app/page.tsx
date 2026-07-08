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
    title: "Draw",
    description: "Describe a room in plain English and watch it appear on the plan.",
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

export default function LandingPage() {
  const [authOpen, setAuthOpen] = useState(false);

  return (
    <main className="relative overflow-hidden">
      <Suspense fallback={null}>
        <AuthQueryParamBridge onAuthParam={() => setAuthOpen(true)} />
      </Suspense>
      <BlueprintBackground />

      <section className="mx-auto flex max-w-3xl flex-col items-center gap-6 px-6 pb-20 pt-28 text-center sm:pt-36">
        <h1 className="text-4xl font-bold tracking-tight text-brand sm:text-5xl">
          Draw floor plans by describing them
        </h1>
        <p className="max-w-xl text-lg text-muted-foreground">
          Cognitect turns natural language into precise, exportable CAD files. DXF, PDF, or
          direct to your CAD software.
        </p>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg">
            <Link href="/try">Try it now</Link>
          </Button>
          <Button size="lg" variant="outline" onClick={() => setAuthOpen(true)}>
            Sign in with Google
          </Button>
        </div>
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

      <footer className="border-t px-6 py-8">
        <div className="mx-auto flex max-w-4xl flex-col items-center justify-between gap-4 text-sm text-muted-foreground sm:flex-row">
          {/* TODO(yash): point at the real repo once it's public/known */}
          <a
            href="https://github.com"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 hover:text-foreground"
          >
            <Github className="h-4 w-4" />
            GitHub
          </a>
          <div className="flex gap-4">
            <span className="cursor-default">Privacy</span>
            <span className="cursor-default">Terms</span>
          </div>
        </div>
      </footer>

      <AuthModal open={authOpen} onOpenChange={setAuthOpen} />
    </main>
  );
}
