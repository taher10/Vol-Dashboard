import { LayoutGrid, Lightbulb, Layers, History, Rewind, Rows3 } from "lucide-react";

export const NAV = [
  { href: "/", label: "Overview", icon: LayoutGrid, symbolScoped: false },
  { href: "/ideas", label: "Trade Ideas", icon: Lightbulb, symbolScoped: false },
  { href: "/scanner", label: "Vol Scanner", icon: Rows3, symbolScoped: false },
  { href: "/strategy", label: "Strategy Builder", icon: Layers, symbolScoped: true },
  { href: "/history", label: "History", icon: History, symbolScoped: true },
  { href: "/backtest", label: "Backtest", icon: Rewind, symbolScoped: true },
] as const;
