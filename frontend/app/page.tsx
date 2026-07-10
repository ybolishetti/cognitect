import type { Metadata } from "next";
import { LandingPageClient } from "./page-client";

export const metadata: Metadata = {
  title: "Cognitect — Floor Plans from Natural Language",
  description:
    "Type natural language, watch a floor plan draw itself. Export precise, CAD-ready DXF or PDF files in seconds.",
};

export default function Page() {
  return <LandingPageClient />;
}
