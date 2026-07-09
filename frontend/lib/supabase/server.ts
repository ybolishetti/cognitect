import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { cookies } from "next/headers";

// Cookie-bound Supabase client for use in Server Components, Route Handlers,
// and Server Actions. Uses the getAll/setAll adapter (matches the middleware)
// so cookie chunking is consistent between server and browser clients — the
// get/set/remove adapter can miss chunked auth cookies produced by newer
// @supabase/ssr browser clients.
//
// The try/catch around setAll is required: Next 14's `cookies()` throws when
// `set` is called from a Server Component render (only Route Handlers /
// Server Actions may write cookies). Middleware is what actually persists
// refreshed session cookies; this try/catch just keeps a stray write from a
// Server Component render from crashing.
export const createClient = () => {
  const cookieStore = cookies();
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => cookieStore.getAll(),
        setAll: (
          cookiesToSet: { name: string; value: string; options: CookieOptions }[]
        ) => {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set({ name, value, ...options })
            );
          } catch {
            // called from a Server Component — safe to ignore
          }
        },
      },
    }
  );
};
