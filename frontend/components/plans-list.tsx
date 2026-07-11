"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from "@/components/ui/alert-dialog";
import { PlanCard } from "@/components/plan-card";
import { api, type PlanListItem } from "@/lib/api";

export function PlansList({ initialPlans }: { initialPlans: PlanListItem[] }) {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [renameTarget, setRenameTarget] = useState<PlanListItem | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<PlanListItem | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return initialPlans;
    return initialPlans.filter((p) => p.name.toLowerCase().includes(q));
  }, [initialPlans, search]);

  // Once the refreshed plan list no longer contains the deleted id, the
  // overlay has served its purpose.
  useEffect(() => {
    if (deletingId && !initialPlans.some((p) => p.id === deletingId)) {
      setDeletingId(null);
    }
  }, [initialPlans, deletingId]);

  const handleRename = async () => {
    if (!renameTarget || !renameDraft.trim()) return;
    setRenaming(true);
    try {
      await api.renamePlan(renameTarget.id, renameDraft.trim());
      setRenameTarget(null);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not rename this plan.");
    } finally {
      setRenaming(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeletingId(deleteTarget.id);
    setDeleteTarget(null);
    try {
      await api.deletePlan(deleteTarget.id);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete this plan.");
      setDeletingId(null);
    }
  };

  return (
    <>
      <Input
        placeholder="Search plans…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="mb-6 max-w-xs"
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((plan) => (
          <PlanCard
            key={plan.id}
            plan={plan}
            onRename={(p) => {
              setRenameTarget(p);
              setRenameDraft(p.name);
            }}
            onDelete={setDeleteTarget}
            isDeleting={plan.id === deletingId}
          />
        ))}
      </div>

      <Dialog open={!!renameTarget} onOpenChange={(open) => !open && setRenameTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename plan</DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            value={renameDraft}
            onChange={(e) => setRenameDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleRename()}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameTarget(null)}>
              Cancel
            </Button>
            <Button onClick={handleRename} disabled={!renameDraft.trim() || renaming}>
              {renaming ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{deleteTarget?.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              This can&apos;t be undone from the app.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
