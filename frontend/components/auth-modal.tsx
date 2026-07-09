"use client";

import { useState } from "react";
import { Check } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/components/providers/auth-provider";

const BENEFITS = [
  "Access your floor plans from any device",
  "Undo history across sessions",
  "Higher rate limits (20 plans/day vs 1/hour anonymous)",
];

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09a6.6 6.6 0 0 1 0-4.18V7.07H2.18a11 11 0 0 0 0 9.86l3.66-2.84z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
      />
    </svg>
  );
}

type AuthModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Overrides the default benefits copy with a single reason sentence, e.g. "Sign in to rename plans." */
  reason?: string;
  /** Where to land after a successful sign-in. Defaults to /plans. */
  next?: string;
  /** Hidden when opened from the header's explicit "Sign in" button; shown when triggered by a gated action. */
  showSkip?: boolean;
};

export function AuthModal({ open, onOpenChange, reason, next, showSkip = true }: AuthModalProps) {
  const { signInWithGoogle } = useAuth();
  const [submitting, setSubmitting] = useState(false);

  const handleGoogle = async () => {
    setSubmitting(true);
    try {
      await signInWithGoogle(next);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Save your work</DialogTitle>
          {reason ? (
            <DialogDescription>{reason}</DialogDescription>
          ) : (
            <DialogDescription>Sign in to unlock:</DialogDescription>
          )}
        </DialogHeader>
        {!reason && (
          <ul className="space-y-2 py-1">
            {BENEFITS.map((b) => (
              <li key={b} className="flex items-start gap-2 text-sm text-muted-foreground">
                <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                {b}
              </li>
            ))}
          </ul>
        )}
        <Button onClick={handleGoogle} disabled={submitting} className="w-full gap-2">
          <GoogleIcon />
          Continue with Google
        </Button>
        {showSkip && (
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="text-center text-sm text-muted-foreground underline-offset-4 hover:underline"
          >
            Skip for now
          </button>
        )}
      </DialogContent>
    </Dialog>
  );
}
