"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { NAV } from "@/lib/nav";
import { api, type SymbolInfo } from "@/lib/api";
import { primarySymbol, useSettingsStore } from "@/lib/store";

/** Lets any component ask for the palette without threading state through
 * a context for a single button. */
export const OPEN_PALETTE_EVENT = "vol-dashboard:open-palette";

export function openCommandPalette() {
  document.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT));
}

/** Keyboard-first navigation, mounted once in the root layout.
 *
 * Getting from one symbol's backtest to another's used to mean reaching for
 * the sidebar picker, then the page list -- two aimed clicks for something
 * done dozens of times a session. Everything here is reachable as
 * type-a-few-letters-then-Enter.
 *
 * Symbol selection sets the primary symbol rather than navigating directly,
 * which lets SymbolRouteSync do what it already does: stay put on a
 * book-wide page, or swap the symbol in place on a symbol-scoped one. That
 * keeps one rule for "what happens when the symbol changes" instead of a
 * second, subtly different one living in here. */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [available, setAvailable] = useState<SymbolInfo[]>([]);
  const router = useRouter();
  const pathname = usePathname();

  const symbols = useSettingsStore((s) => s.symbols);
  const setPrimary = useSettingsStore((s) => s.setPrimary);
  const bumpRefreshNonce = useSettingsStore((s) => s.bumpRefreshNonce);
  const primary = primarySymbol(symbols);

  useEffect(() => {
    let cancelled = false;
    api
      .symbols()
      .then((res) => !cancelled && setAvailable(res))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onRequest = () => setOpen(true);
    // Capture phase: once the dialog is open its input has focus and cmdk's
    // own keydown handling sits between the event and document, so a
    // bubble-phase listener never sees the second press and the shortcut
    // opens but won't close. Capturing runs before any of that.
    document.addEventListener("keydown", onKey, true);
    document.addEventListener(OPEN_PALETTE_EVENT, onRequest);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener(OPEN_PALETTE_EVENT, onRequest);
    };
  }, []);

  function run(action: () => void) {
    setOpen(false);
    action();
  }

  // Symbol-scoped destinations resolve against the current primary, so
  // "Backtest" means "backtest what I'm looking at" rather than dumping the
  // user on a route with no symbol in it.
  const pages = useMemo(
    () =>
      NAV.map((item) => ({
        ...item,
        target: item.symbolScoped ? `${item.href}/${primary}` : item.href,
      })),
    [primary]
  );

  // Tracked symbols first: those are the ones being worked on today, and
  // otherwise a 24-item alphabetical list buries them.
  const orderedSymbols = useMemo(() => {
    const tracked = new Set(symbols);
    const known = available.length > 0 ? available : symbols.map((s) => ({ symbol: s, color: "#8a93a1" }));
    return [...known].sort((a, b) => {
      const at = tracked.has(a.symbol) ? 0 : 1;
      const bt = tracked.has(b.symbol) ? 0 : 1;
      return at !== bt ? at - bt : a.symbol.localeCompare(b.symbol);
    });
  }, [available, symbols]);

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title="Command palette"
      description="Jump to a page, switch symbol, or refresh data"
    >
      <CommandInput placeholder="Jump to a page or symbol…" />
      <CommandList>
        <CommandEmpty>No match.</CommandEmpty>

        <CommandGroup heading="Go to">
          {pages.map((page) => {
            const Icon = page.icon;
            const active = pathname === page.href || pathname.startsWith(`${page.href}/`);
            return (
              <CommandItem
                key={page.href}
                value={`${page.label} ${page.href}`}
                onSelect={() => run(() => router.push(page.target))}
              >
                <Icon className="size-4 opacity-70" />
                <span>{page.label}</span>
                {page.symbolScoped && <span className="text-xs text-muted-foreground">{primary}</span>}
                {active && <CommandShortcut>current</CommandShortcut>}
              </CommandItem>
            );
          })}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Switch symbol">
          {orderedSymbols.map((info) => (
            <CommandItem
              key={info.symbol}
              value={`${info.symbol} symbol`}
              onSelect={() => run(() => setPrimary(info.symbol))}
            >
              <span className="size-1.5 rounded-full" style={{ backgroundColor: info.color }} />
              <span className="font-mono">{info.symbol}</span>
              {info.symbol === primary && <CommandShortcut>primary</CommandShortcut>}
            </CommandItem>
          ))}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Actions">
          <CommandItem value="refresh live data" onSelect={() => run(bumpRefreshNonce)}>
            <RefreshCw className="size-4 opacity-70" />
            <span>Refresh live data</span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
