"use client";

import { useState } from "react";
import { ArrowDownToLine, RefreshCw, TriangleAlert, X } from "lucide-react";

import { useSync } from "@/lib/use-sync";
import { useUpstreamStatus } from "@/lib/use-upstream-status";
import { cn } from "@/lib/utils";

/** Says when the pipeline has collected data this checkout has not pulled.
 *
 * The dashboard reads a file on disk; the pipeline writes a git commit.
 * Nothing on screen connected those, so a page showing Monday's numbers
 * looked identical whether collection had failed or whether it had
 * succeeded and nobody ran a pull. Only the first is a problem. The second
 * is the common one, and it was twice diagnosed as the first -- the only
 * place the difference was visible was a terminal.
 *
 * The Overview's "data age" counter cannot answer this on its own: age is a
 * property of the data you have, and the question here is about data you do
 * not have yet.
 *
 * Surfaces a blocked pull before the click rather than after. pull_latest
 * refuses while the tracked databases are dirty, and the refusal arrives as
 * a sentence under a button nobody expected to fail -- which is precisely
 * how the Sync control came to look broken once already.
 */
export function UpstreamBanner() {
  const { status } = useUpstreamStatus();
  const { sync, loading, status: syncStatus } = useSync();
  const [dismissed, setDismissed] = useState<string | null>(null);

  if (!status?.has_updates) return null;

  // Dismissal is keyed to what was pending, not to the banner itself: if
  // another session lands while the tab is open, that is new information
  // and the notice has earned its place back.
  const key = `${status.behind ?? "some"}`;
  if (dismissed === key) return null;

  const blocked = status.dirty.length > 0;
  const count = status.behind;
  const headline =
    count === null
      ? "The pipeline has collected newer data."
      : `${count} new ${count === 1 ? "update" : "updates"} collected since your last sync.`;

  return (
    <div
      className={cn(
        "flex items-start gap-3 border-b px-4 py-2.5 text-xs md:px-6",
        blocked
          ? "border-amber-500/30 bg-amber-500/10 text-amber-100"
          : "border-sky-500/30 bg-sky-500/10 text-sky-100"
      )}
    >
      {blocked ? (
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-400" />
      ) : (
        <ArrowDownToLine className="mt-0.5 size-4 shrink-0 text-sky-400" />
      )}

      <div className="flex-1 leading-relaxed">
        <span className="font-semibold">{headline}</span>{" "}
        {blocked ? (
          <>
            Syncing is blocked by local changes to{" "}
            <span className="font-mono">{status.dirty.join(", ")}</span>. These are generated files
            the pipeline owns, so the local copy is almost certainly the one to drop:{" "}
            <span className="font-mono">git checkout -- {status.dirty.join(" ")}</span>
          </>
        ) : (
          <>This page is showing what you last pulled, not what has been collected.</>
        )}
        {syncStatus && (
          <p
            className={cn(
              "mt-1 leading-snug",
              syncStatus.type === "success" && "text-emerald-300",
              syncStatus.type === "info" && "text-amber-300",
              syncStatus.type === "error" && "text-red-300"
            )}
          >
            {syncStatus.message}
          </p>
        )}
      </div>

      {!blocked && (
        <button
          type="button"
          onClick={() => void sync()}
          disabled={loading}
          className="shrink-0 inline-flex items-center gap-1.5 rounded border border-sky-400/40 bg-sky-500/15 px-2.5 py-1 font-medium text-sky-50 transition-colors hover:bg-sky-500/25 disabled:opacity-60"
        >
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
          {loading ? "Syncing…" : "Sync now"}
        </button>
      )}

      <button
        type="button"
        onClick={() => setDismissed(key)}
        aria-label="Dismiss"
        className={cn(
          "shrink-0 rounded p-0.5 transition-colors",
          blocked
            ? "text-amber-300/70 hover:bg-amber-500/20 hover:text-amber-100"
            : "text-sky-300/70 hover:bg-sky-500/20 hover:text-sky-100"
        )}
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
