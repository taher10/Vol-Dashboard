"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError, type SyncResponse } from "@/lib/api";
import { useSettingsStore } from "@/lib/store";

export type SyncStatus = { type: "success" | "info" | "error"; message: string } | null;

/** The one place that performs a sync.
 *
 * Two controls offer it now -- the sidebar button and the freshness banner
 * -- and both have to do the same three things: call the endpoint, bump the
 * refresh nonce when files actually changed, and surface the message rather
 * than an exception. Copying that into both is how one of them ends up
 * forgetting the nonce and quietly leaves every open page rendering
 * superseded numbers after a successful pull.
 */
export function useSync() {
  const bumpRefreshNonce = useSettingsStore((s) => s.bumpRefreshNonce);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<SyncStatus>(null);

  useEffect(() => {
    if (!status) return;
    const t = setTimeout(() => setStatus(null), 6000);
    return () => clearTimeout(t);
  }, [status]);

  const sync = useCallback(async (): Promise<SyncResponse | null> => {
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
      return res;
    } catch (err) {
      setStatus({
        type: "error",
        message: err instanceof ApiError ? err.message : "Sync failed — is the backend running?",
      });
      return null;
    } finally {
      setLoading(false);
    }
  }, [bumpRefreshNonce]);

  return { sync, loading, status };
}
