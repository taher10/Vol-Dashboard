"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { useSettingsStore } from "@/lib/store";
import { useDemoMode } from "@/lib/use-demo-mode";
import { cn } from "@/lib/utils";

type Status = { type: "success" | "info" | "error"; message: string } | null;

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
 * data", which is a git pull. See src/dashboard/data_sync.py. */
export function RefreshButton() {
  // Gated here rather than at the two call sites (site-header and
  // app-sidebar): one of them would eventually be missed, and a third
  // mount added later would be unguarded by default.
  const demo = useDemoMode();
  const bumpRefreshNonce = useSettingsStore((s) => s.bumpRefreshNonce);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<Status>(null);

  useEffect(() => {
    if (!status) return;
    const t = setTimeout(() => setStatus(null), 6000);
    return () => clearTimeout(t);
  }, [status]);

  // Hidden until health confirms this is not the demo -- see useDemoMode
  // for why unknown is treated as hidden.
  if (demo !== false) return null;

  const handleClick = async () => {
    setLoading(true);
    setStatus(null);
    try {
      const res = await api.sync();
      setStatus({ type: res.changed ? "success" : "info", message: res.message });
      if (res.changed) {
        // Chain and metrics both come from the files just replaced, so every
        // open page is now showing superseded numbers.
        bumpRefreshNonce();
      }
    } catch (err) {
      setStatus({
        type: "error",
        message: err instanceof ApiError ? err.message : "Sync failed — is the backend running?",
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-1.5 px-3 pb-3">
      <Button
        variant="outline"
        size="sm"
        className="w-full justify-center gap-1.5 border-sidebar-border bg-sidebar-accent/40 text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        onClick={handleClick}
        disabled={loading}
      >
        <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
        {loading ? "Syncing…" : "Sync Latest Data"}
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
