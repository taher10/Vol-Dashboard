"use client";

import { useCallback, useEffect, useState } from "react";

import { api, type UpstreamStatusResponse } from "@/lib/api";
import { useDemoMode } from "@/lib/use-demo-mode";

/** Poll interval. The collection commits once a weekday, so this is not a
 * race -- it exists so a tab left open overnight notices the morning's data
 * without a reload. The server caches the answer for three minutes, so a
 * shorter interval here would mostly re-read the same cached value. */
const POLL_MS = 10 * 60 * 1000;

/** Whether origin has data this checkout has not pulled.
 *
 * Polled rather than read once, because the case this exists for is a tab
 * that has been open since before the snapshot landed -- checking only at
 * mount would miss exactly that. Re-checks on focus too, which is what
 * actually catches it: the data arrives overnight and the first thing that
 * happens is someone switching back to the tab.
 *
 * Silent on failure. Nobody asked for this check; it runs on a timer behind
 * a banner, and a laptop with no network should show no banner rather than
 * an error over the dashboard.
 */
export function useUpstreamStatus() {
  const demo = useDemoMode();
  const [status, setStatus] = useState<UpstreamStatusResponse | null>(null);

  const check = useCallback(() => {
    // `null` is "not yet known" -- see useDemoMode. Checking before the
    // answer arrives would fire a request the demo deployment 404s.
    if (demo !== false) return;
    api
      .syncStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [demo]);

  useEffect(() => {
    if (demo !== false) return;
    check();
    const id = setInterval(check, POLL_MS);
    const onFocus = () => check();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [demo, check]);

  return { status, recheck: check };
}
