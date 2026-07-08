import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/components/providers/auth-provider";
import { Header } from "@/components/header";
import { Toaster } from "@/components/ui/sonner";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

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
