import { LayoutGrid, Lightbulb, Layers, CalendarClock, Rewind, Rows3, TrendingUp, Grid3x3 } from "lucide-react";

export const NAV = [
  { href: "/", label: "Overview", icon: LayoutGrid, symbolScoped: false },
  { href: "/ideas", label: "Trade Ideas", icon: Lightbulb, symbolScoped: false },
  { href: "/scanner", label: "Vol Scanner", icon: Rows3, symbolScoped: false },
  { href: "/term-structure", label: "Term Structure", icon: TrendingUp, symbolScoped: false },
  { href: "/surface", label: "Vol Surface", icon: Grid3x3, symbolScoped: false },
  { href: "/calendar-math", label: "Calendar Math", icon: CalendarClock, symbolScoped: false },
  { href: "/strategy", label: "Strategy Builder", icon: Layers, symbolScoped: true },
  { href: "/backtest", label: "Backtest", icon: Rewind, symbolScoped: true },
] as const;
