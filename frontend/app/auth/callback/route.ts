import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

// OAuth callback handler for Supabase Auth (PKCE flow).
// Supabase redirects the user here with `?code=...` after Google sign-in.
// We exchange the code for a session (which writes the auth cookies via the
// server client's cookie adapter), then send the user on to `?next=<path>`
// or `/plans` by default.
export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const next = url.searchParams.get("next") ?? "/plans";
  const errorDescription = url.searchParams.get("error_description");

  // Only allow relative redirect targets — prevents open-redirect via ?next=https://evil.com
  const safeNext = next.startsWith("/") ? next : "/plans";

  if (errorDescription) {
    const errUrl = new URL("/", url.origin);
    errUrl.searchParams.set("auth_error", errorDescription);
    return NextResponse.redirect(errUrl);
  }

  if (!code) {
    const errUrl = new URL("/", url.origin);
    errUrl.searchParams.set("auth_error", "missing_code");
    return NextResponse.redirect(errUrl);
  }

  const supabase = createClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);

  if (error) {
    const errUrl = new URL("/", url.origin);
    errUrl.searchParams.set("auth_error", error.message);
    return NextResponse.redirect(errUrl);
  }

  return NextResponse.redirect(new URL(safeNext, url.origin));
}
