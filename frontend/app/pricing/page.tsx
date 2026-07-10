import type { Metadata } from "next";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export const metadata: Metadata = {
  title: { absolute: "Pricing — Cognitect" },
  description: "Free during beta. Paid tiers with higher limits and team features are coming soon.",
};

const RATE_LIMITS = [
  { tier: "Anonymous", limit: "1 plan / hour" },
  { tier: "Authenticated (free)", limit: "20 plans / day" },
  { tier: "Paid tier", limit: "Coming soon" },
];

export default function PricingPage() {
  return (
    <main className="mx-auto flex max-w-3xl flex-col items-center gap-6 px-6 py-16 text-center">
      <h1 className="text-4xl font-bold tracking-tight text-brand sm:text-5xl">
        Free during beta
      </h1>
      <p className="max-w-xl text-lg text-muted-foreground">
        Cognitect is free to use while we iterate. Paid tiers with higher limits, team features,
        and priority support are coming soon.
      </p>

      <Card className="w-full max-w-md">
        <CardContent className="p-0">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b">
                <th className="p-4 font-semibold">Tier</th>
                <th className="p-4 font-semibold">Rate limit</th>
              </tr>
            </thead>
            <tbody>
              {RATE_LIMITS.map(({ tier, limit }, i) => (
                <tr key={tier} className={i < RATE_LIMITS.length - 1 ? "border-b" : undefined}>
                  <td className="p-4 text-muted-foreground">{tier}</td>
                  <td className="p-4 text-muted-foreground">{limit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Button asChild size="lg">
        <Link href="/">Sign in with Google</Link>
      </Button>
    </main>
  );
}
