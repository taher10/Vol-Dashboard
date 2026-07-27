import { LayoutGrid, Crosshair, ListFilter, Target, History } from "lucide-react";

export const NAV = [
  { href: "/", label: "Overview", icon: LayoutGrid, symbolScoped: false },
  { href: "/expiry", label: "Expiry Drilldown", icon: Crosshair, symbolScoped: true },
  { href: "/strikes", label: "Strike Selector", icon: Target, symbolScoped: true },
  { href: "/screener", label: "Decision Screener", icon: ListFilter, symbolScoped: true },
  { href: "/history", label: "History", icon: History, symbolScoped: true },
] as const;
