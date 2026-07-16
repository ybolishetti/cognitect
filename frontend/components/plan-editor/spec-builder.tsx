"use client";

import { useState, type FormEvent } from "react";
import { Trash2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardHeader, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import {
  DEFAULT_N_CANDIDATES,
  DEFAULT_TOP_K,
  ROOM_TYPES,
  ROOM_TYPE_LABELS,
  type RoomType,
} from "@/lib/constants";
import {
  generateSpecId,
  type FloorPlanSpec,
  type RoomRequirement,
  type SiteConstraints,
} from "@/lib/api";

type SpecBuilderProps = {
  onSubmit: (spec: FloorPlanSpec) => Promise<void>;
  disabled?: boolean;
};

type RoomRow = {
  id: string;
  name: string;
  roomType: RoomType;
  preferredArea: string;
  adjacencies: string;
};

// Input's className minus the file:* variants (irrelevant to <select>). No
// shadcn Select primitive or @radix-ui/react-select exists in this codebase
// yet, and adding one would be a new dependency for a single dropdown.
const selectClassName =
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-base shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 md:text-sm";

let rowIdCounter = 0;
function nextRowId(): string {
  rowIdCounter += 1;
  return `row_${rowIdCounter}`;
}

// Deliberate demo bait: partners landing on /try see a valid spec pre-filled
// and can hit Generate immediately. Do not change the names/count.
function defaultRows(): RoomRow[] {
  return [
    { id: nextRowId(), name: "Bedroom 1", roomType: "bedroom", preferredArea: "140", adjacencies: "" },
    { id: nextRowId(), name: "Kitchen", roomType: "kitchen", preferredArea: "140", adjacencies: "Living" },
    { id: nextRowId(), name: "Living", roomType: "living", preferredArea: "240", adjacencies: "Kitchen" },
  ];
}

const SITE_CONSTRAINT_DEFAULTS = {
  lotWidth: "",
  lotDepth: "",
  setbackFront: "",
  setbackRear: "",
  setbackSide: "",
  maxFootprint: "",
  jurisdiction: "IRC-2021",
};

type SiteConstraintState = typeof SITE_CONSTRAINT_DEFAULTS;

function buildSiteConstraints(state: SiteConstraintState): SiteConstraints | undefined {
  const hasCustomValue =
    state.lotWidth !== "" ||
    state.lotDepth !== "" ||
    state.setbackFront !== "" ||
    state.setbackRear !== "" ||
    state.setbackSide !== "" ||
    state.maxFootprint !== "" ||
    state.jurisdiction !== SITE_CONSTRAINT_DEFAULTS.jurisdiction;

  if (!hasCustomValue) return undefined;

  const constraints: SiteConstraints = {
    jurisdiction: state.jurisdiction || SITE_CONSTRAINT_DEFAULTS.jurisdiction,
  };
  if (state.lotWidth !== "") constraints.lot_width_ft = Number(state.lotWidth);
  if (state.lotDepth !== "") constraints.lot_depth_ft = Number(state.lotDepth);
  if (state.setbackFront !== "") constraints.setback_front_ft = Number(state.setbackFront);
  if (state.setbackRear !== "") constraints.setback_rear_ft = Number(state.setbackRear);
  if (state.setbackSide !== "") constraints.setback_side_ft = Number(state.setbackSide);
  if (state.maxFootprint !== "") constraints.max_footprint_sqft = Number(state.maxFootprint);
  return constraints;
}

export function SpecBuilder({ onSubmit, disabled }: SpecBuilderProps) {
  const [rows, setRows] = useState<RoomRow[]>(defaultRows);
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [siteConstraints, setSiteConstraints] = useState<SiteConstraintState>(SITE_CONSTRAINT_DEFAULTS);

  const updateRow = (id: string, patch: Partial<RoomRow>) => {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  };

  const addRow = () => {
    setRows((prev) => [
      ...prev,
      { id: nextRowId(), name: "", roomType: "bedroom", preferredArea: "", adjacencies: "" },
    ]);
  };

  const removeRow = (id: string) => {
    setRows((prev) => prev.filter((r) => r.id !== id));
  };

  const updateSiteConstraint = (key: keyof SiteConstraintState, value: string) => {
    setSiteConstraints((prev) => ({ ...prev, [key]: value }));
  };

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (rows.length === 0) return;

    const trimmedNames = rows.map((r) => r.name.trim());
    if (trimmedNames.some((n) => !n)) {
      toast.error("Every room needs a name.");
      return;
    }

    const lowerNames = trimmedNames.map((n) => n.toLowerCase());
    if (new Set(lowerNames).size !== lowerNames.length) {
      toast.error("Room names must be unique.");
      return;
    }

    for (const row of rows) {
      if (row.preferredArea.trim() === "") continue;
      const area = Number(row.preferredArea);
      if (!Number.isFinite(area) || area <= 0) {
        toast.error("Preferred area must be greater than 0 sq ft.");
        return;
      }
    }

    const room_requirements: RoomRequirement[] = rows.map((row, i) => {
      const preferredArea = row.preferredArea.trim();
      const candidates = row.adjacencies
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const adjacencies = Array.from(
        new Set(
          candidates
            .map((candidate) => {
              const matchIndex = trimmedNames.findIndex(
                (other, idx) => idx !== i && other.toLowerCase() === candidate.toLowerCase()
              );
              return matchIndex === -1 ? null : trimmedNames[matchIndex];
            })
            .filter((n): n is string => n !== null)
        )
      );

      return {
        name: trimmedNames[i],
        room_type: row.roomType,
        ...(preferredArea ? { preferred_area_sqft: Number(preferredArea) } : {}),
        ...(adjacencies.length ? { adjacencies } : {}),
      };
    });

    const siteConstraintsPayload = buildSiteConstraints(siteConstraints);

    const spec: FloorPlanSpec = {
      spec_id: generateSpecId(),
      original_nl: notes.trim() || "Generate a plan matching the above room requirements.",
      room_requirements,
      ...(siteConstraintsPayload ? { site_constraints: siteConstraintsPayload } : {}),
      n_candidates: DEFAULT_N_CANDIDATES,
    };

    setSubmitting(true);
    try {
      await onSubmit(spec);
    } finally {
      setSubmitting(false);
    }
  }

  const busy = Boolean(disabled) || submitting;

  return (
    <Card>
      <CardHeader>
        <h2 className="text-lg font-semibold">Describe your floor plan</h2>
        <p className="text-sm text-muted-foreground">
          Add rooms, set their sizes, and choose adjacencies. Cognitect generates {DEFAULT_N_CANDIDATES} candidates,
          validates each against building code, and shows you the top scoring layouts.
        </p>
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800/50 dark:bg-amber-950/40 dark:text-amber-200">
          <strong>Beta note:</strong> First request after idle can take 20–30s while
          the backend warms up. Subsequent requests return in ~1 second.
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-3">
            {rows.map((row) => (
              <div key={row.id} className="flex flex-wrap items-start gap-2 rounded-md border p-3">
                <Input
                  aria-label="Room name"
                  placeholder="Room name"
                  value={row.name}
                  onChange={(e) => updateRow(row.id, { name: e.target.value })}
                  className="min-w-[140px] flex-1"
                />
                <select
                  aria-label="Room type"
                  className={cn(selectClassName, "min-w-[120px] flex-1")}
                  value={row.roomType}
                  onChange={(e) => updateRow(row.id, { roomType: e.target.value as RoomType })}
                >
                  {ROOM_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {ROOM_TYPE_LABELS[type]}
                    </option>
                  ))}
                </select>
                <Input
                  aria-label="Preferred area (sq ft)"
                  type="number"
                  min={20}
                  max={2000}
                  step={10}
                  placeholder="Sq ft"
                  value={row.preferredArea}
                  onChange={(e) => updateRow(row.id, { preferredArea: e.target.value })}
                  className="w-24 flex-none"
                />
                <Input
                  aria-label="Adjacent to (comma-separated room names)"
                  placeholder="Kitchen, Living"
                  value={row.adjacencies}
                  onChange={(e) => updateRow(row.id, { adjacencies: e.target.value })}
                  className="min-w-[140px] flex-1"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Remove room"
                  onClick={() => removeRow(row.id)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>

          <Button type="button" variant="outline" onClick={addRow} className="self-start">
            + Add room
          </Button>

          <Separator />

          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm font-medium">Site constraints (optional)</summary>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Input
                aria-label="Lot width (ft)"
                type="number"
                placeholder="Lot width (ft)"
                value={siteConstraints.lotWidth}
                onChange={(e) => updateSiteConstraint("lotWidth", e.target.value)}
              />
              <Input
                aria-label="Lot depth (ft)"
                type="number"
                placeholder="Lot depth (ft)"
                value={siteConstraints.lotDepth}
                onChange={(e) => updateSiteConstraint("lotDepth", e.target.value)}
              />
              <Input
                aria-label="Front setback (ft)"
                type="number"
                placeholder="Front setback (ft)"
                value={siteConstraints.setbackFront}
                onChange={(e) => updateSiteConstraint("setbackFront", e.target.value)}
              />
              <Input
                aria-label="Rear setback (ft)"
                type="number"
                placeholder="Rear setback (ft)"
                value={siteConstraints.setbackRear}
                onChange={(e) => updateSiteConstraint("setbackRear", e.target.value)}
              />
              <Input
                aria-label="Side setback (ft)"
                type="number"
                placeholder="Side setback (ft)"
                value={siteConstraints.setbackSide}
                onChange={(e) => updateSiteConstraint("setbackSide", e.target.value)}
              />
              <Input
                aria-label="Max footprint (sq ft)"
                type="number"
                placeholder="Max footprint (sq ft)"
                value={siteConstraints.maxFootprint}
                onChange={(e) => updateSiteConstraint("maxFootprint", e.target.value)}
              />
              <Input
                aria-label="Jurisdiction"
                placeholder="Jurisdiction"
                value={siteConstraints.jurisdiction}
                onChange={(e) => updateSiteConstraint("jurisdiction", e.target.value)}
                className="col-span-2"
              />
            </div>
          </details>

          <div>
            <label className="mb-1.5 block text-sm font-medium">Notes for the AI (optional)</label>
            <Textarea
              placeholder="Anything specific about how these rooms should relate, materials, style, etc."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
            />
          </div>

          <Button type="submit" disabled={busy || rows.length === 0} className="w-full">
            {busy ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Generating…
              </>
            ) : (
              `Generate ${DEFAULT_TOP_K} candidates`
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
