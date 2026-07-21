"use client";

import { LayoutTemplate, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardContent, CardFooter } from "@/components/ui/card";
import { LayoutPreview } from "@/components/plan-editor/layout-preview";
import { ENABLE_MATERIALIZE } from "@/lib/constants";
import type { GenerateLayoutSummary, GenerateResponse } from "@/lib/api";

type CandidateGalleryProps = {
  response: GenerateResponse | null;
  onGenerateAgain: () => void;
  onUseCandidate: (layout: GenerateLayoutSummary) => Promise<void>;
  materializingRank: number | null;
};

export function CandidateGallery({
  response,
  onGenerateAgain,
  onUseCandidate,
  materializingRank,
}: CandidateGalleryProps) {
  if (!response) return null;

  const layouts = [...response.layouts].sort((a, b) => a.selection_rank - b.selection_rank);
  const layoutsByRank = new Map(
    (response.layouts_full ?? []).map((entry) => [entry.selection_rank, entry])
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-panel px-4 py-3">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2 text-sm">
            <span>
              Generated {response.total_candidates} candidate{response.total_candidates === 1 ? "" : "s"} in{" "}
              {response.elapsed_ms}ms · {response.survived_layer_a} passed geometry ·{" "}
              {response.survived_layer_c} passed building code
            </span>
            {response.cached && (
              <Badge variant="secondary" title="Same spec as a previous request — returning cached candidates.">
                Cached
              </Badge>
            )}
          </div>
          <span className="text-xs text-muted-foreground">Showing top {layouts.length} by user score</span>
        </div>
        <Button variant="outline" size="sm" onClick={onGenerateAgain}>
          Regenerate
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {layouts.map((layout) => {
          const layoutWithRank = layoutsByRank.get(layout.selection_rank);
          const isMaterializing = materializingRank === layout.selection_rank;
          return (
            <Card key={layout.selection_rank}>
              <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
                <span className="font-semibold">Candidate #{layout.selection_rank + 1}</span>
                <Badge>{layout.user_score != null ? `${Math.round(layout.user_score * 100)} / 100` : "—"}</Badge>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                <div className="text-xs text-muted-foreground">
                  <p>✓ Layer A verified</p>
                  <p>✓ Layer C verified</p>
                  <p>
                    Generator: {response.generator_name}@{response.generator_version}
                  </p>
                  <p className="font-mono">{layout.plan_id.slice(0, 8)}…</p>
                </div>
                {layoutWithRank ? (
                  <LayoutPreview layout={layoutWithRank.layout} className="aspect-[4/3] w-full rounded-md border bg-white" />
                ) : (
                  <div className="flex aspect-[4/3] flex-col items-center justify-center gap-2 rounded-md bg-muted text-muted-foreground">
                    <LayoutTemplate className="h-8 w-8" />
                    <span className="text-xs">Preview render coming soon</span>
                  </div>
                )}
              </CardContent>
              <CardFooter>
                <Button
                  disabled={!ENABLE_MATERIALIZE || materializingRank !== null}
                  onClick={() => onUseCandidate(layout)}
                  className="w-full"
                  title={!ENABLE_MATERIALIZE ? "Coming soon" : undefined}
                >
                  {isMaterializing ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Opening…
                    </>
                  ) : (
                    "Use this candidate"
                  )}
                </Button>
              </CardFooter>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
