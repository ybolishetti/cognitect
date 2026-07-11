import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  tracesSampleRate: 0.1,
  replaysSessionSampleRate: 0,
  replaysOnErrorSampleRate: 1.0,
  // Sentry.replayIntegration() enables session replay on error. Yash's call
  // whether to turn this on — uncomment `integrations` below if so.
  // integrations: [Sentry.replayIntegration()],
});
