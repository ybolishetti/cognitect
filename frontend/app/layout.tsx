import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cognitect — AI Floor Plan Engine",
  description: "Type natural language, watch a floor plan draw itself.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
