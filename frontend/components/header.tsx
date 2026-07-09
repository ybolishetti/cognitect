"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AuthModal } from "@/components/auth-modal";
import { useAuth } from "@/components/providers/auth-provider";

function initials(email: string) {
  return email.slice(0, 2).toUpperCase();
}

export function Header() {
  const { user, loading, signOut } = useAuth();
  const router = useRouter();
  const [authOpen, setAuthOpen] = useState(false);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
      <Link href={user ? "/plans" : "/"} className="font-bold tracking-tight text-brand">
        Cognitect
      </Link>

      {loading ? null : user ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="rounded-full">
              <Avatar className="h-8 w-8">
                <AvatarFallback>{initials(user.email ?? "?")}</AvatarFallback>
              </Avatar>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem asChild>
              <Link href="/plans">Plans</Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/account">Account</Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={async () => {
                await signOut();
                router.push("/");
              }}
            >
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <div className="flex items-center gap-2">
          <Link href="/try" className="text-sm text-muted-foreground hover:text-foreground">
            Try it
          </Link>
          <Button size="sm" onClick={() => setAuthOpen(true)}>
            Sign in
          </Button>
        </div>
      )}

      <AuthModal open={authOpen} onOpenChange={setAuthOpen} showSkip={false} />
    </header>
  );
}
