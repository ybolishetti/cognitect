"use client";

import Link from "next/link";
import { MoreVertical } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatRelativeTime } from "@/lib/format";
import type { PlanListItem } from "@/lib/api";

const GRADIENTS = [
  "from-blue-500 to-indigo-600",
  "from-emerald-500 to-teal-600",
  "from-amber-500 to-orange-600",
  "from-pink-500 to-rose-600",
  "from-violet-500 to-purple-600",
];

function gradientFor(id: string) {
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return GRADIENTS[hash % GRADIENTS.length];
}

function initials(name: string) {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase()).join("") || "?";
}

type PlanCardProps = {
  plan: PlanListItem;
  onRename: (plan: PlanListItem) => void;
  onDelete: (plan: PlanListItem) => void;
};

export function PlanCard({ plan, onRename, onDelete }: PlanCardProps) {
  return (
    <Card className="relative overflow-hidden p-0">
      <Link href={`/plans/${plan.id}`} className="block">
        {/* TODO(phase-3): use thumbnail_url when generation lands — it's
            always null in Phase 1, no generator job exists yet. */}
        <div
          className={`flex h-32 items-center justify-center bg-gradient-to-br text-2xl font-semibold text-white ${gradientFor(
            plan.id
          )}`}
        >
          {initials(plan.name)}
        </div>
        <div className="p-3">
          <p className="truncate font-medium">{plan.name}</p>
          <p className="text-xs text-muted-foreground">
            {plan.room_count} room{plan.room_count === 1 ? "" : "s"} ·{" "}
            {formatRelativeTime(plan.last_opened_at)}
          </p>
        </div>
      </Link>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="absolute right-2 top-2 h-7 w-7 bg-black/40 text-white hover:bg-black/60"
          >
            <MoreVertical className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => onRename(plan)}>Rename</DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => onDelete(plan)}
            className="text-destructive focus:text-destructive"
          >
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </Card>
  );
}
