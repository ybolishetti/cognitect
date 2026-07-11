"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/components/providers/auth-provider";
import { createClient } from "@/lib/supabase/client";

export default function AccountPage() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const [displayName, setDisplayName] = useState(
    (user?.user_metadata?.display_name as string | undefined) ?? ""
  );
  const [saving, setSaving] = useState(false);

  const saveDisplayName = async () => {
    setSaving(true);
    try {
      const supabase = createClient();
      const { error } = await supabase.auth.updateUser({ data: { display_name: displayName } });
      if (error) throw error;
      toast.success("Display name updated.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not update display name.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto max-w-md px-6 py-10">
      <h1 className="mb-6 text-2xl font-bold tracking-tight">Account</h1>

      <label className="text-sm font-medium">Email</label>
      <p className="mb-4 mt-1 text-sm text-muted-foreground">{user?.email}</p>

      <label className="text-sm font-medium">Display name</label>
      <div className="mt-1 flex gap-2">
        <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        <Button onClick={saveDisplayName} disabled={saving}>
          Save
        </Button>
      </div>

      <Separator className="my-6" />

      <div className="flex flex-col gap-2">
        <Button
          variant="outline"
          onClick={async () => {
            await signOut();
            router.push("/");
          }}
        >
          Sign out
        </Button>
        <p className="text-sm text-muted-foreground">
          Need to delete your account? Email{" "}
          <a href="mailto:support@cognitect.app" className="underline">
            support@cognitect.app
          </a>
          .
        </p>
      </div>
    </main>
  );
}
