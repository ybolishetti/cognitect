import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

// Cookie-bound Supabase client for use in Server Components, Route Handlers,
// and Server Actions. The try/catch around cookie writes is required: Next
// 14's `cookies()` throws when `set`/`remove` is called from a Server
// Component render (only Route Handlers / Server Actions may write cookies).
// Middleware is what actually persists refreshed session cookies; this
// try/catch just keeps a stray write from a Server Component from crashing
// the render.
export const createClient = () => {
  const cookieStore = cookies();
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        get: (name: string) => cookieStore.get(name)?.value,
        set: (name: string, value: string, options: Record<string, unknown>) => {
          try {
            cookieStore.set({ name, value, ...options });
          } catch {
            // called from a Server Component — safe to ignore
          }
        },
        remove: (name: string, options: Record<string, unknown>) => {
          try {
            cookieStore.set({ name, value: "", ...options });
          } catch {
            // called from a Server Component — safe to ignore
          }
        },
      },
    }
  );
};
