"use client";

import { SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useSettingsStore } from "@/lib/store";

export function SettingsDrawer() {
  const s = useSettingsStore();

  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          <SlidersHorizontal className="size-3.5" />
          Settings
        </Button>
      </SheetTrigger>
      <SheetContent className="w-full overflow-y-auto sm:max-w-sm">
        <SheetHeader>
          <SheetTitle>Dashboard settings</SheetTitle>
          <SheetDescription>Shared across every page.</SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-6 px-4 pb-6">
          <section className="flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-foreground">Viewing window</h3>

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs text-muted-foreground">DTE range</Label>
                <span className="font-mono text-xs tabular-nums">
                  {s.dteRange[0]}–{s.dteRange[1]}
                </span>
              </div>
              <Slider
                min={0}
                max={730}
                step={1}
                value={s.dteRange}
                onValueChange={(v) => s.setDteRange([v[0], v[1]])}
              />
            </div>
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}
