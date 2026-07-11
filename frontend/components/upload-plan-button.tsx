"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { AuthModal } from "@/components/auth-modal";
import { useAuth } from "@/components/providers/auth-provider";
import { api, ApiError } from "@/lib/api";
import { handle429 } from "@/lib/rate-limit";

export function UploadPlanButton({
  variant = "outline",
}: {
  variant?: "default" | "outline";
}) {
  const router = useRouter();
  const { user } = useAuth();
  const ref = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);

  const handleFile = async (file: File) => {
    setUploading(true);
    try {
      const { plan_id } = await api.uploadPlan(file);
      toast.success("Plan uploaded.");
      router.push(`/plans/${plan_id}`);
    } catch (e) {
      if (handle429(e, { isAnonymous: !user, openAuthModal: () => setAuthOpen(true), router })) return;
      if (e instanceof ApiError && e.status === 413) {
        toast.error("File too large (max 10 MB).");
      } else if (e instanceof ApiError && e.status === 400) {
        toast.error(e.message);
      } else {
        toast.error(e instanceof Error ? e.message : "Upload failed.");
      }
    } finally {
      setUploading(false);
    }
  };

  return (
    <>
      <input
        ref={ref}
        type="file"
        accept=".dxf,.json"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) handleFile(f);
          e.target.value = "";
        }}
      />
      <Button variant={variant} disabled={uploading} onClick={() => ref.current?.click()}>
        <Upload className="mr-2 h-4 w-4" />
        Upload
      </Button>
      <AuthModal open={authOpen} onOpenChange={setAuthOpen} />
    </>
  );
}
