"use client";

import { useState } from "react";
import { ChevronDown, Info } from "lucide-react";

import { cn } from "@/lib/utils";

/** One-line orientation with the full explanation behind a toggle.
 *
 * Every page opened with a wall of prose -- 932 characters on Calendar Math,
 * enough to push the first actual data 381px down a 768px screen on Overview.
 * That text is worth keeping (it's what stops these numbers being trusted
 * blindly) but it's reference material: you read it once, then you want the
 * screen back. Collapsed by default, one keystroke away, and the state is
 * per-page so expanding one doesn't expand them all.
 *
 * `summary` should be the single sentence a returning user needs; `children`
 * carries the caveats, the methodology, and the reasons a number is or isn't
 * trustworthy. */
export function PageIntro({
  summary,
  children,
  right,
}: {
  summary: React.ReactNode;
  children?: React.ReactNode;
  right?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mb-3 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <p className="min-w-0 text-xs text-muted-foreground">{summary}</p>
          {children && (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              className="inline-flex shrink-0 items-center gap-1 rounded text-[11px] text-muted-foreground/80 transition-colors hover:text-foreground"
            >
              <Info className="size-3" />
              How this works
              <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
            </button>
          )}
        </div>
        {children && open && (
          <div className="mt-2 max-w-[68ch] border-l-2 border-border pl-3 text-xs leading-relaxed text-muted-foreground">
            {children}
          </div>
        )}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}
