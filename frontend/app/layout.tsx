import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/components/providers/auth-provider";
import { Header } from "@/components/header";
import { Toaster } from "@/components/ui/sonner";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

const siteUrl = "https://cognitect-six.vercel.app";
const defaultTitle = "Cognitect — AI Floor Plan Engine";
const description = "Type natural language, watch a floor plan draw itself.";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: defaultTitle,
    template: "%s — Cognitect",
  },
  description,
  openGraph: {
    title: defaultTitle,
    description,
    url: siteUrl,
    siteName: "Cognitect",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "Cognitect — Floor Plans from Natural Language",
      },
    ],
    locale: "en_US",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: defaultTitle,
    description,
    images: ["/og-image.png"],
  },
  icons: {
    icon: [
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
    ],
    shortcut: "/favicon.ico",
    apple: "/apple-touch-icon.png",
    other: {
      rel: "icon",
      url: "/favicon.ico",
    },
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="font-sans">
        <AuthProvider>
          <div className="flex h-screen flex-col">
            <Header />
            <div className="min-h-0 flex-1">{children}</div>
          </div>
          <Toaster />
        </AuthProvider>
      </body>
    </html>
  );
}
