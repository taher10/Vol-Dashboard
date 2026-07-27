"use client";

import { useEffect, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { api, type SymbolInfo } from "@/lib/api";
import { useSettingsStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export function SymbolPicker() {
  const [open, setOpen] = useState(false);
  const [allSymbols, setAllSymbols] = useState<SymbolInfo[]>([]);
  const symbols = useSettingsStore((s) => s.symbols);
  const toggleSymbol = useSettingsStore((s) => s.toggleSymbol);

  useEffect(() => {
    api
      .symbols()
      .then(setAllSymbols)
      .catch(() => setAllSymbols([]));
  }, []);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between bg-sidebar-accent/40 border-sidebar-border text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          <span className="flex items-center gap-1.5 overflow-hidden">
            {symbols.slice(0, 3).map((sym) => {
              const info = allSymbols.find((s) => s.symbol === sym);
              return (
                <span
                  key={sym}
                  className="inline-flex items-center gap-1 rounded-full bg-black/20 px-2 py-0.5 text-xs font-medium"
                >
                  <span
                    className="size-1.5 rounded-full"
                    style={{ backgroundColor: info?.color ?? "#999" }}
                  />
                  {sym}
                </span>
              );
            })}
            {symbols.length > 3 && (
              <span className="text-xs opacity-70">+{symbols.length - 3}</span>
            )}
          </span>
          <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-0" align="start">
        <Command>
          <CommandInput placeholder="Search symbols..." />
          <CommandList>
            <CommandEmpty>No symbol found.</CommandEmpty>
            <CommandGroup>
              {allSymbols.map((info) => {
                const selected = symbols.includes(info.symbol);
                return (
                  <CommandItem
                    key={info.symbol}
                    value={info.symbol}
                    onSelect={() => toggleSymbol(info.symbol)}
                  >
                    <span
                      className="size-2 rounded-full"
                      style={{ backgroundColor: info.color }}
                    />
                    <span className="flex-1">{info.symbol}</span>
                    <Check className={cn("size-4", selected ? "opacity-100" : "opacity-0")} />
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
