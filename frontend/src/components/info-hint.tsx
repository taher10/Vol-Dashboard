"use client";

import { Info } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Small inline (?) icon that shows an explanation on hover/focus — for jargon
 * (Skew, Curvature, VRP, IV Rank, etc.) that isn't self-evident to a reader
 * who isn't already an options trader. */
export function InfoHint({ text, className }: { text: string; className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={cn("inline-flex shrink-0 cursor-help align-middle opacity-60 hover:opacity-100", className)}
          aria-label="More info"
        >
          <Info className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-64 text-wrap">{text}</TooltipContent>
    </Tooltip>
  );
}
