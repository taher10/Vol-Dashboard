"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { SymbolPicker } from "@/components/symbol-picker";
import { RefreshButton } from "@/components/refresh-button";
import { useSettingsStore, primarySymbol } from "@/lib/store";
import { NAV } from "@/lib/nav";
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
        <span className="text-sm font-semibold tracking-tight">Vol Dashboard</span>
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
            : pathname === "/";
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
              )}
            >
              <Icon className="size-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto flex flex-col gap-1 px-4 py-4 text-xs text-sidebar-foreground/50">
        <span>Personal use — real snapshot data</span>
      </div>
    </aside>
  );
}
