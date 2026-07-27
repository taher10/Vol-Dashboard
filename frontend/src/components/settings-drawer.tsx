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
import { Separator } from "@/components/ui/separator";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Input } from "@/components/ui/input";
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
          <SheetDescription>
            Shared across every page — trade intent, scoring weights, and contract filters.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-6 px-4 pb-6">
          <section className="flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-foreground">Trade intent</h3>
            <ToggleGroup
              type="single"
              value={s.intent}
              onValueChange={(v) => v && s.setIntent(v as "buy" | "sell")}
              className="w-full"
            >
              <ToggleGroupItem value="buy" className="flex-1">
                Buy
              </ToggleGroupItem>
              <ToggleGroupItem value="sell" className="flex-1">
                Sell
              </ToggleGroupItem>
            </ToggleGroup>

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs text-muted-foreground">Target delta</Label>
                <span className="font-mono text-xs tabular-nums">{s.targetDelta.toFixed(2)}</span>
              </div>
              <Slider
                min={0.05}
                max={0.5}
                step={0.01}
                value={[s.targetDelta]}
                onValueChange={([v]) => s.setTargetDelta(v)}
              />
            </div>

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs text-muted-foreground">Delta tolerance</Label>
                <span className="font-mono text-xs tabular-nums">{s.deltaTolerance.toFixed(2)}</span>
              </div>
              <Slider
                min={0.05}
                max={0.3}
                step={0.01}
                value={[s.deltaTolerance]}
                onValueChange={([v]) => s.setDeltaTolerance(v)}
              />
            </div>
          </section>

          <Separator />

          <section className="flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-foreground">Score weights</h3>
            {[
              { key: "value" as const, label: "Value", value: s.weightValue },
              { key: "deltaFit" as const, label: "Delta fit", value: s.weightDeltaFit },
              { key: "liquidity" as const, label: "Liquidity", value: s.weightLiquidity },
            ].map((w) => (
              <div key={w.key} className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <Label className="text-xs text-muted-foreground">{w.label}</Label>
                  <span className="font-mono text-xs tabular-nums">{w.value.toFixed(2)}</span>
                </div>
                <Slider
                  min={0}
                  max={1}
                  step={0.05}
                  value={[w.value]}
                  onValueChange={([v]) => s.setWeights({ [w.key]: v })}
                />
              </div>
            ))}
          </section>

          <Separator />

          <section className="flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-foreground">Contract filters</h3>

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

            <div className="flex flex-col gap-2">
              <Label className="text-xs text-muted-foreground">Option types</Label>
              <ToggleGroup
                type="single"
                value={s.optionTypes}
                onValueChange={(v) => v && s.setOptionTypes(v as "BOTH" | "CALL" | "PUT")}
                className="w-full"
              >
                <ToggleGroupItem value="BOTH" className="flex-1">
                  Both
                </ToggleGroupItem>
                <ToggleGroupItem value="CALL" className="flex-1">
                  Call
                </ToggleGroupItem>
                <ToggleGroupItem value="PUT" className="flex-1">
                  Put
                </ToggleGroupItem>
              </ToggleGroup>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">Min volume</Label>
                <Input
                  type="number"
                  min={0}
                  value={s.minVolume}
                  onChange={(e) => s.setMinVolume(Number(e.target.value) || 0)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">Min open interest</Label>
                <Input
                  type="number"
                  min={0}
                  value={s.minOpenInterest}
                  onChange={(e) => s.setMinOpenInterest(Number(e.target.value) || 0)}
                />
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs text-muted-foreground">Max spread %</Label>
                <span className="font-mono text-xs tabular-nums">{s.maxSpreadPct.toFixed(0)}%</span>
              </div>
              <Slider
                min={0}
                max={100}
                step={1}
                value={[s.maxSpreadPct]}
                onValueChange={([v]) => s.setMaxSpreadPct(v)}
              />
            </div>
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}
