import { LayoutGrid, Crosshair, Layers, History } from "lucide-react";

export const NAV = [
  { href: "/", label: "Overview", icon: LayoutGrid, symbolScoped: false },
  { href: "/expiry", label: "Expiry Drilldown", icon: Crosshair, symbolScoped: true },
  { href: "/strategy", label: "Strategy Builder", icon: Layers, symbolScoped: true },
  { href: "/history", label: "History", icon: History, symbolScoped: true },
] as const;
