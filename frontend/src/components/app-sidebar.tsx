"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { SymbolPicker } from "@/components/symbol-picker";
import { RefreshButton } from "@/components/refresh-button";
import { useSettingsStore, primarySymbol } from "@/lib/store";
import { NAV } from "@/lib/nav";
import { Search } from "lucide-react";

import { openCommandPalette } from "@/components/command-palette";
import { cn } from "@/lib/utils";

export function AppSidebar() {
  const pathname = usePathname();
  const symbols = useSettingsStore((s) => s.symbols);
  const primary = primarySymbol(symbols);

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground md:flex">
      <div className="flex h-14 items-center gap-2 px-4">
        <div className="flex size-6 items-center justify-center rounded-md bg-sidebar-primary text-xs font-bold text-sidebar-primary-foreground">
          V
        </div>
        <span className="text-[13px] font-semibold tracking-tight">Vol Dashboard</span>
      </div>

      <div className="px-3 pb-3">
        <SymbolPicker />
      </div>

      <RefreshButton />

      <nav className="flex flex-col gap-0.5 px-2">
        {NAV.map((item) => {
          const href = item.symbolScoped ? `${item.href}/${primary}` : item.href;
          const active = item.symbolScoped
            ? pathname.startsWith(item.href)
            : pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={href}
              className={cn(
                "relative flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] transition-colors",
                "before:absolute before:left-0 before:top-1/2 before:h-4 before:w-0.5 before:-translate-y-1/2",
                "before:rounded-r before:transition-colors",
                active
                  ? "bg-sidebar-accent/70 font-medium text-sidebar-accent-foreground before:bg-sidebar-primary"
                  : "font-normal text-sidebar-foreground/65 before:bg-transparent hover:bg-sidebar-accent/40 hover:text-sidebar-foreground"
              )}
            >
              <Icon className={cn("size-4 shrink-0", active ? "text-sidebar-primary" : "opacity-60")} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto flex flex-col gap-2 px-3 py-4">
        {/* The shortcut is the point of a palette, but an unadvertised
            shortcut is one nobody uses -- this both announces it and works
            as a click target for anyone who'd rather not learn it. */}
        <button
          type="button"
          onClick={openCommandPalette}
          className="flex items-center gap-2 rounded-md border border-sidebar-border/70 px-2.5 py-1.5 text-xs text-sidebar-foreground/55 transition-colors hover:border-sidebar-border hover:text-sidebar-foreground"
        >
          <Search className="size-3.5 shrink-0" />
          <span>Jump to…</span>
          <kbd className="ml-auto rounded border border-sidebar-border/80 px-1 py-px font-mono text-[10px] text-sidebar-foreground/60">
            ⌘K
          </kbd>
        </button>
        <span className="px-1 text-[11px] text-sidebar-foreground/40">Personal use — real snapshot data</span>
      </div>
    </aside>
  );
}
