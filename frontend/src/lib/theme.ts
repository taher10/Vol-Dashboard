/**
 * Chart color tokens, ported 1:1 from src/dashboard/chart_components.py so
 * the web charts read as the same product as the existing Streamlit/static
 * ones. These are raw hex strings (not CSS variables) because Recharts
 * needs literal color values for its `stroke`/`fill` props.
 */

export const COLOR_CALL = "#2a78d6";
export const COLOR_PUT = "#e34948";
export const COLOR_LINE_DEFAULT = "#2a78d6";
export const COLOR_GRID = "#e1e0d9";
export const COLOR_TEXT_MUTED = "#898781";
export const COLOR_HIGHLIGHT = "#0b0b0b";

export const STATUS_GOOD = "#0ca30c"; // Cheap
export const STATUS_CRITICAL = "#d03b3b"; // Rich
export const STATUS_NEUTRAL = "#898781"; // Neutral

export const RICHNESS_BG: Record<string, string> = {
  cheap: "rgba(12, 163, 12, 0.14)",
  rich: "rgba(208, 59, 59, 0.14)",
  neutral: "rgba(137, 135, 129, 0.12)",
};

export const RICHNESS_TEXT: Record<string, string> = {
  cheap: "#0b5c0b",
  rich: "#8f2323",
  neutral: COLOR_TEXT_MUTED,
};

export function richnessKey(label: string | null | undefined): "cheap" | "rich" | "neutral" {
  const lower = (label ?? "").toLowerCase();
  if (lower === "cheap") return "cheap";
  if (lower === "rich") return "rich";
  return "neutral";
}
