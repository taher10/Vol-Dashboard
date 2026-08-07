"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetClose, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { SymbolPicker } from "@/components/symbol-picker";
import { RefreshButton } from "@/components/refresh-button";
import { SettingsDrawer } from "@/components/settings-drawer";
import { useSettingsStore, primarySymbol } from "@/lib/store";
import { NAV } from "@/lib/nav";
import { cn } from "@/lib/utils";

export function SiteHeader({ title }: { title: string }) {
  const pathname = usePathname();
  const symbols = useSettingsStore((s) => s.symbols);
  const primary = primarySymbol(symbols);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/80 md:px-6">
      <Button
        variant="ghost"
        size="icon"
        className="md:hidden"
        onClick={() => setMobileOpen(true)}
        aria-label="Open navigation"
      >
        <Menu className="size-5" />
      </Button>

      <h1 className="flex-1 truncate text-sm font-semibold text-foreground md:text-base">{title}</h1>

      <SettingsDrawer />

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent
          side="left"
          showCloseButton={false}
          className="w-72 border-sidebar-border bg-sidebar p-0 text-sidebar-foreground"
        >
          <SheetHeader className="flex-row items-center gap-2 border-b border-sidebar-border">
            <div className="flex size-6 items-center justify-center rounded-md bg-sidebar-primary text-xs font-bold text-sidebar-primary-foreground">
              V
            </div>
            <SheetTitle className="text-sidebar-foreground">Vol Dashboard</SheetTitle>
          </SheetHeader>
          <SheetClose asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              className="absolute top-3 right-3 text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
              aria-label="Close navigation"
            >
              <X className="size-4" />
            </Button>
          </SheetClose>
          <div className="flex flex-col gap-4 p-4">
            <SymbolPicker />
            <div className="-mx-4">
              <RefreshButton />
            </div>
            <nav className="flex flex-col gap-0.5">
              {NAV.map((item) => {
                const href = item.symbolScoped ? `${item.href}/${primary}` : item.href;
                const active = item.symbolScoped ? pathname.startsWith(item.href) : pathname === item.href;
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={href}
                    onClick={() => setMobileOpen(false)}
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
          </div>
          <div className="mt-auto flex flex-col gap-1 px-4 py-4 text-xs text-sidebar-foreground/50">
            <span>Personal use — real snapshot data</span>
          </div>
        </SheetContent>
      </Sheet>
    </header>
  );
}
