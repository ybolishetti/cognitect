"use client";

import { Loader2 } from "lucide-react";
import { usePreviewBlob } from "@/lib/hooks/use-preview-blob";
import { Skeleton } from "@/components/ui/skeleton";

type EditorPreviewProps = {
  planId: string;
  version: number | null;
};

export function EditorPreview({ planId, version }: EditorPreviewProps) {
  const { url, loading } = usePreviewBlob(planId, version);

  return (
    <section className="flex flex-1 items-center justify-center bg-panel p-8">
      {!url && !loading && (
        <p className="text-muted-foreground">Start describing your floor plan</p>
      )}
      {loading && !url && (
        <Skeleton className="h-[70vh] max-h-[600px] w-[90%] max-w-3xl rounded-lg" />
      )}
      {url && (
        <div className="relative max-h-full max-w-full overflow-hidden rounded-lg bg-white shadow-lg">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          )}
          {/* eslint-disable-next-line @next/next/no-img-element -- blob: URL, next/image can't optimize it */}
          <img
            src={url}
            alt="Floor plan preview"
            className="block max-h-[calc(100vh-4rem)] max-w-full object-contain"
          />
        </div>
      )}
    </section>
  );
}
