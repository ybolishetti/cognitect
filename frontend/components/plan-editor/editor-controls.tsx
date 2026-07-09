"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { ChevronLeft, Loader2, Download, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";
import { downloadExport } from "@/lib/api";
import { ROOM_COLORS, EXAMPLES } from "@/lib/constants";

type RoomInfo = { name: string; room_type: string; area_sqft: number | null };

type EditorControlsProps = {
  anonymous: boolean;
  planId: string;
  planName: string;
  version: number;
  rooms: Record<string, RoomInfo>;
  instruction: string;
  onInstructionChange: (value: string) => void;
  onSubmitInstruction: () => void;
  busy: boolean;
  saving: boolean;
  lastSavedAt: Date | null;
  onRename: (name: string) => void;
  onNewPlan: () => void;
  onUpload: (file: File) => void;
  onRequestAuth: (reason?: string) => void;
};

export function EditorControls({
  anonymous,
  planId,
  planName,
  version,
  rooms,
  instruction,
  onInstructionChange,
  onSubmitInstruction,
  busy,
  saving,
  lastSavedAt,
  onRename,
  onNewPlan,
  onUpload,
  onRequestAuth,
}: EditorControlsProps) {
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(planName);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const roomEntries = Object.entries(rooms);

  const commitName = () => {
    setEditingName(false);
    const trimmed = nameDraft.trim();
    if (trimmed && trimmed !== planName) onRename(trimmed);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      onSubmitInstruction();
    }
  };

  return (
    <section className="flex h-full w-full flex-col overflow-y-auto border-r px-6 py-7 sm:w-[380px] sm:min-w-[340px]">
      {!anonymous && (
        <Link
          href="/plans"
          className="mb-4 flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="h-4 w-4" />
          Back to plans
        </Link>
      )}

      <header className="mb-1">
        {editingName ? (
          <Input
            autoFocus
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitName();
              if (e.key === "Escape") {
                setNameDraft(planName);
                setEditingName(false);
              }
            }}
            className="h-auto p-0 text-xl font-bold tracking-tight"
          />
        ) : (
          <h1
            className="cursor-pointer text-xl font-bold tracking-tight"
            onClick={() => {
              if (anonymous) {
                onRequestAuth("Sign in to rename plans.");
              } else {
                setNameDraft(planName);
                setEditingName(true);
              }
            }}
          >
            {planName || "Untitled Plan"}
          </h1>
        )}
        <p className="mt-0.5 text-xs text-muted-foreground">
          v{version} · {saving ? "Saving…" : lastSavedAt ? "Saved" : "Not yet saved"}
        </p>
      </header>

      <Separator className="my-5" />

      <div className="flex gap-2">
        {anonymous && (
          <Button variant="outline" size="sm" onClick={onNewPlan}>
            New Plan
          </Button>
        )}
        {!anonymous && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept=".dxf,.json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onUpload(f);
                e.target.value = ""; // allow re-uploading the same file
              }}
            />
            <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
              <Upload className="mr-1.5 h-3.5 w-3.5" />
              Upload
            </Button>
          </>
        )}
        {roomEntries.length > 0 && (
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                downloadExport(planId, "dxf").catch(() => toast.error("Export failed."))
              }
            >
              <Download className="mr-1.5 h-3.5 w-3.5" />
              DXF
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                downloadExport(planId, "pdf").catch(() => toast.error("Export failed."))
              }
            >
              <Download className="mr-1.5 h-3.5 w-3.5" />
              PDF
            </Button>
          </>
        )}
      </div>

      <div className="mt-6">
        <Textarea
          value={instruction}
          onChange={(e) => onInstructionChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Describe your floor plan or give an instruction..."
          rows={3}
        />
        <div className="mt-2 flex flex-wrap gap-1.5">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => onInstructionChange(ex)}
              className="rounded-full border bg-panel px-2.5 py-1 text-xs text-muted-foreground hover:bg-secondary"
            >
              {ex}
            </button>
          ))}
        </div>
        <Button
          onClick={onSubmitInstruction}
          disabled={busy || !instruction.trim()}
          className="mt-2.5 w-full"
        >
          {busy ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Working…
            </>
          ) : (
            "Send (⌘/Ctrl + Enter)"
          )}
        </Button>
      </div>

      {roomEntries.length > 0 && (
        <div className="mt-6">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Rooms ({roomEntries.length})
          </h2>
          <div className="flex flex-col gap-1.5">
            {roomEntries.map(([id, room]) => (
              <Card key={id} className="flex items-center gap-2.5 px-3 py-2 text-sm shadow-none">
                <span
                  className="h-3 w-3 shrink-0 rounded-sm border border-black/20"
                  style={{ background: ROOM_COLORS[room.room_type] ?? ROOM_COLORS.other }}
                />
                <span className="flex-1 truncate">{room.name}</span>
                <span className="text-xs text-muted-foreground">
                  {room.area_sqft ? `${Math.round(room.area_sqft)} sqft` : "—"}
                </span>
              </Card>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
