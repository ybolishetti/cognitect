"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { AuthModal } from "@/components/auth-modal";

export function PricingCta() {
  const [authOpen, setAuthOpen] = useState(false);
  return (
    <>
      <Button size="lg" onClick={() => setAuthOpen(true)}>
        Sign in with Google
      </Button>
      <AuthModal open={authOpen} onOpenChange={setAuthOpen} />
    </>
  );
}
