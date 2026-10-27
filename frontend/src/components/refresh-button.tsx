"use client";

import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useDemoMode } from "@/lib/use-demo-mode";
import { useSync } from "@/lib/use-sync";
import { useUpstreamStatus } from "@/lib/use-upstream-status";
import { cn } from "@/lib/utils";

/** Pulls the data the daily pipeline has already collected.
 *
 * This used to trigger a live Schwab fetch. That became wrong once the
 * dashboard started reading the committed database, and then actively
 * harmful: a successful pull rotates the Schwab refresh token, invalidating
 * the copy in SCHWAB_TOKEN_B64 and stopping the next scheduled collection.
 * Outside market hours it did nothing instead, and a developer's local token
 * goes stale within a day of CI's next run -- so the common outcome was a
 * raw OAuth error for an operation that should not have been offered.
 *
 * What a user means by "refresh" here is "show me the latest collected
 * data", which is a git pull. See src/dashboard/data_sync.py.
 *
 * Carries a count when something is waiting. The button was always
 * available and never indicated whether pressing it would do anything, so
 * the honest reasons to press it -- new data exists -- looked the same as
 * the pointless ones. UpstreamBanner makes the loud version of this point;
 * the badge is for when that has been dismissed.
 */
export function RefreshButton() {
  // Gated here rather than at the two call sites (site-header and
  // app-sidebar): one of them would eventually be missed, and a third
  // mount added later would be unguarded by default.
  const demo = useDemoMode();
  const { sync, loading, status } = useSync();
  const { status: upstream, recheck } = useUpstreamStatus();

  // Hidden until health confirms this is not the demo -- see useDemoMode
  // for why unknown is treated as hidden.
  if (demo !== false) return null;

  const waiting = upstream?.has_updates ? upstream.behind : null;

  const handleClick = async () => {
    const res = await sync();
    if (res?.changed) recheck();
  };

  return (
    <div className="flex flex-col gap-1.5 px-3 pb-3">
      <Button
        variant="outline"
        size="sm"
        className={cn(
          "w-full justify-center gap-1.5 border-sidebar-border bg-sidebar-accent/40 text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground",
          upstream?.has_updates && "border-sky-500/50 bg-sky-500/10"
        )}
        onClick={handleClick}
        disabled={loading}
      >
        <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
        {loading ? "Syncing…" : "Sync Latest Data"}
        {!loading && upstream?.has_updates && (
          <span className="ml-0.5 rounded-full bg-sky-500/25 px-1.5 py-px text-[10px] font-semibold tabular-nums text-sky-100">
            {waiting ?? "new"}
          </span>
        )}
      </Button>
      {status && (
        <p
          className={cn(
            "text-xs leading-snug",
            status.type === "success" && "text-emerald-400",
            status.type === "info" && "text-amber-400",
            status.type === "error" && "text-red-400"
          )}
        >
          {status.message}
        </p>
      )}
    </div>
  );
}
