import type { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: { absolute: "Privacy Policy — Cognitect" },
};

const SECTIONS = [
  {
    title: "Introduction",
    body: (
      <>
        <p>
          Cognitect is currently in <strong>public beta</strong>. This policy describes what
          data we collect and how we use it today — it will evolve as the product does, and
          we&apos;ll update this page when it does.
        </p>
      </>
    ),
  },
  {
    title: "Data we collect",
    body: (
      <>
        <p>We collect the minimum needed to run the service:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Your name and email address, provided by Google when you sign in</li>
          <li>The floor plans you create — room layouts, dimensions, and the prompts you type</li>
          <li>Your IP address, used only to enforce rate limits on anonymous usage</li>
        </ul>
      </>
    ),
  },
  {
    title: "Data storage",
    body: (
      <p>
        Plan data and account records are stored in Supabase; the API that processes requests
        runs on Google Cloud Run. Both are hosted in US regions.
      </p>
    ),
  },
  {
    title: "Third parties",
    body: (
      <>
        <p>We rely on a small number of third-party services:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Google Cloud Platform (Cloud Run), to host the backend API — US region</li>
          <li>Vercel, to host the web frontend — US region</li>
          <li>Supabase, to store your account and plan data — US region</li>
          <li>Google OAuth, to authenticate sign-ins</li>
          <li>Anthropic&apos;s API, to parse the natural-language descriptions you type and generate floor plan candidates</li>
          <li>Sentry, to collect anonymous error telemetry for debugging</li>
        </ul>
      </>
    ),
  },
  {
    title: "Your rights",
    body: (
      <p>
        You can view the data associated with your account at any time by signing in. To
        request deletion of your account or data, email us at{" "}
        <a href="mailto:yashbolishetti@gmail.com" className="underline hover:text-foreground">
          yashbolishetti@gmail.com
        </a>
        .
      </p>
    ),
  },
  {
    title: "Contact",
    body: (
      <p>
        Questions about this policy? Reach out at{" "}
        <a href="mailto:yashbolishetti@gmail.com" className="underline hover:text-foreground">
          yashbolishetti@gmail.com
        </a>
        .
      </p>
    ),
  },
];

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-4xl font-bold tracking-tight text-brand">Privacy Policy</h1>
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
