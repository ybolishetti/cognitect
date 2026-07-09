import { useEffect, useRef, useState } from "react";
import { fetchPreviewBlob } from "@/lib/api";

// Fetches the plan preview PNG as a blob: URL (required since the endpoint
// needs an auth header a plain <img src> can't attach) and manages its
// lifecycle: revokes the previous object URL right before swapping in a new
// one, and revokes on unmount, to avoid leaking memory across a long editing
// session. Keyed on [planId, version] rather than re-fetching on every
// render — version comes from the plan/instruct response, so this only
// re-fetches when the plan actually changed.
export function usePreviewBlob(planId: string | null, version: number | null) {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!planId || version === null) return;

    let cancelled = false;
    setLoading(true);
    fetchPreviewBlob(planId)
      .then((blobUrl) => {
        if (cancelled) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = blobUrl;
        setUrl(blobUrl);
      })
      .catch(() => {
        // non-fatal — preview refresh is best-effort
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [planId, version]);

  useEffect(() => {
    return () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);

  return { url, loading };
}
