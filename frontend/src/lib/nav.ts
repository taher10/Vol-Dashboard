import { LayoutGrid, Lightbulb, Layers, CalendarClock, Scale, Rewind, Rows3, TrendingUp, Zap, ShieldCheck } from "lucide-react";

export const NAV = [
  { href: "/", label: "Overview", icon: LayoutGrid, symbolScoped: false },
  { href: "/ideas", label: "Trade Ideas", icon: Lightbulb, symbolScoped: false },
  { href: "/scanner", label: "Vol Scanner", icon: Rows3, symbolScoped: false },
  { href: "/term-structure", label: "Term Structure", icon: TrendingUp, symbolScoped: false },
  { href: "/gamma-exposure", label: "Gamma Exposure", icon: Zap, symbolScoped: false },
  { href: "/calendar-math", label: "Calendar Math", icon: CalendarClock, symbolScoped: false },
  { href: "/delta-neutral", label: "Delta Neutral", icon: Scale, symbolScoped: false },
  { href: "/strategy", label: "Strategy Builder", icon: Layers, symbolScoped: true },
  { href: "/backtest", label: "Backtest", icon: Rewind, symbolScoped: true },
  { href: "/data-trust", label: "Data Trust", icon: ShieldCheck, symbolScoped: false },
] as const;
