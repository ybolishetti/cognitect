"use client";

import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // TODO(sentry): Sentry.captureException(error) — hooked in DRAFT G
  }, [error]);

  return (
    <html lang="en">
      <body>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            height: "100vh",
            gap: "1rem",
            textAlign: "center",
            fontFamily: "sans-serif",
          }}
        >
          <p>The application encountered a critical error.</p>
          <button onClick={reset}>Try again</button>
        </div>
      </body>
    </html>
  );
}
