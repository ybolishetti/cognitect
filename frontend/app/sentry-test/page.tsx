"use client";

import { useEffect } from "react";

// Throwing in an effect (not during render) keeps this route out of Next's
// static-generation pass at build time; it still throws in the browser once
// mounted, which Sentry's client error handler picks up.
export default function SentryTestPage() {
  useEffect(() => {
    throw new Error("Cognitect Sentry smoke test — 2026-07-16");
  }, []);
  return null;
}
