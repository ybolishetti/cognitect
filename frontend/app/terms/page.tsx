import type { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: { absolute: "Terms of Service — Cognitect" },
};

const SECTIONS = [
  {
    title: "Acceptance of terms",
    body: (
      <p>
        Cognitect is a <strong>beta</strong> service. By using it, you agree to these terms,
        which may change without notice as the product evolves.
      </p>
    ),
  },
  {
    title: "User accounts",
    body: (
      <p>
        You must be at least 13 years old to use Cognitect. Accounts are created and
        authenticated through Google OAuth only — there is no separate username/password login.
      </p>
    ),
  },
  {
    title: "Acceptable use",
    body: (
      <p>
        Don&apos;t use Cognitect to generate, store, or share illegal content, and don&apos;t
        attempt to circumvent or abuse rate limits (anonymous or authenticated).
      </p>
    ),
  },
  {
    title: "Content ownership",
    body: (
      <p>
        You own the floor plans you create with Cognitect, including any exported DXF or PDF
        files.
      </p>
    ),
  },
  {
    title: "Service availability",
    body: (
      <p>
        As a beta product, Cognitect is provided without any uptime guarantee or service-level
        agreement. Features, limits, and availability may change at any time.
      </p>
    ),
  },
  {
    title: "Limitation of liability",
    body: (
      <p>
        Cognitect is provided &quot;as is,&quot; without warranties of any kind. To the fullest
        extent permitted by law, we are not liable for any damages arising from your use of the
        service.
      </p>
    ),
  },
  {
    title: "Contact",
    body: (
      <p>
        Questions about these terms? Reach out at{" "}
        <a href="mailto:yashbolishetti@gmail.com" className="underline hover:text-foreground">
          yashbolishetti@gmail.com
        </a>
        .
      </p>
    ),
  },
];

export default function TermsPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-4xl font-bold tracking-tight text-brand">Terms of Service</h1>
      <p className="mt-2 text-sm text-muted-foreground">Last updated: July 20, 2026</p>

      <div className="mt-10 flex flex-col gap-6">
        {SECTIONS.map(({ title, body }) => (
          <Card key={title}>
            <CardHeader>
              <CardTitle className="text-xl">{title}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm text-muted-foreground">
              {body}
            </CardContent>
          </Card>
        ))}
      </div>
    </main>
  );
}
