"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { useSettingsStore } from "@/lib/store";
import { cn } from "@/lib/utils";

type Status = { type: "success" | "info" | "error"; message: string } | null;

export function RefreshButton() {
  const symbols = useSettingsStore((s) => s.symbols);
  const bumpRefreshNonce = useSettingsStore((s) => s.bumpRefreshNonce);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<Status>(null);

  useEffect(() => {
    if (!status) return;
    const t = setTimeout(() => setStatus(null), 6000);
    return () => clearTimeout(t);
  }, [status]);

  const handleClick = async () => {
    setLoading(true);
    setStatus(null);
    try {
      const res = await api.refresh(symbols);
      if (res.failed.length > 0) {
        const failedNote = res.failed.map((f) => `${f.symbol}: ${f.error}`).join(" ");
        setStatus({
          type: "error",
          message: res.succeeded.length
            ? `Refreshed ${res.succeeded.join(", ")}; failed: ${failedNote}`
            : `Refresh failed: ${failedNote}`,
        });
      } else if (res.unavailable.length > 0) {
        setStatus({
          type: "info",
          message: res.succeeded.length
            ? `Refreshed ${res.succeeded.join(", ")}. Live data unavailable for ${res.unavailable.map((u) => u.symbol).join(", ")} (likely outside market hours) — still showing the last available snapshot.`
            : `Live data unavailable right now (likely outside market hours) — still showing the last available snapshot.`,
        });
      } else {
        setStatus({ type: "success", message: `Refreshed: ${res.succeeded.join(", ")}.` });
      }
      if (res.succeeded.length > 0) {
        bumpRefreshNonce();
      }
    } catch (err) {
      setStatus({
        type: "error",
        message: err instanceof ApiError ? err.message : "Refresh failed — is the backend running?",
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
        {loading ? "Refreshing…" : "Refresh Live Data"}
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
