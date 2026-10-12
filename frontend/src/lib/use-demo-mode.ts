"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

/** Whether this deployment is the public demo.
 *
 * Read from the API rather than from a NEXT_PUBLIC_* build variable so there
 * is one source of truth. Two flags drift, and the drift shows up as a
 * button that is still rendered and simply errors when a visitor clicks it --
 * worse than either state on its own.
 *
 * Starts as `null` (unknown) rather than `false`, and callers hide
 * demo-gated controls until it resolves. Defaulting to "not a demo" would
 * flash the refresh button on every public page load before the answer
 * arrived; a control that appears and then vanishes reads as a bug, and on
 * the demo it advertises exactly the route that is switched off. A brief
 * absence on localhost is the cheaper mistake.
 *
 * A failed health check is treated as demo mode for the same reason: if we
 * cannot confirm the credentialed routes are available, do not offer them.
 */
export function useDemoMode(): boolean | null {
  const [demo, setDemo] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((h) => !cancelled && setDemo(Boolean(h.demo_mode)))
      .catch(() => !cancelled && setDemo(true));
    return () => {
      cancelled = true;
    };
  }, []);

  return demo;
}
