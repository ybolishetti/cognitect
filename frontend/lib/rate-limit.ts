import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { formatRetryAfter } from "@/lib/format";

type RouterLike = { push: (href: string) => void };

// Returns true if `err` was a 429 and has already been toasted — callers
// should `return` immediately in that case instead of also showing their
// own generic error toast.
export function handle429(
  err: unknown,
  ctx: { isAnonymous: boolean; openAuthModal: () => void; router: RouterLike }
): boolean {
  if (!(err instanceof ApiError) || err.status !== 429) return false;

  if (ctx.isAnonymous) {
    toast("You've hit the free tier limit. Sign in for 20/day.", {
      action: { label: "Sign in", onClick: ctx.openAuthModal },
    });
  } else {
    const seconds = err.retryAfterSeconds ?? 0;
    toast(`Daily limit reached. Try again ${formatRetryAfter(seconds)}.`, {
      action: { label: "See pricing", onClick: () => ctx.router.push("/pricing") },
    });
  }
  return true;
}
