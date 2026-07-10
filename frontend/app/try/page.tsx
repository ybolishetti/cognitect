import type { Metadata } from "next";
import { TryPageClient } from "./page-client";

export const metadata: Metadata = {
  title: "Try Cognitect",
  description: "Draw and refine a floor plan from natural language — no sign-in required.",
};

export default function Page() {
  return <TryPageClient />;
}
