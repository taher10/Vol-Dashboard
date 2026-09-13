/**
 * Chart color tokens. Raw hex strings rather than CSS variables because
 * Recharts needs literal values for its `stroke`/`fill` props.
 *
 * These were originally ported 1:1 from src/dashboard/chart_components.py's
 * light palette. They're now tuned for the dark terminal theme instead: the
 * old values were dark-on-light (a #0b5c0b green, a near-white #e1e0d9 grid)
 * and either vanished or glared against a near-black ground. The static
 * Streamlit charts still use the light palette, so the two intentionally
 * diverge now -- keep that in mind before "restoring" parity.
 */

export const COLOR_CALL = "#5b9ce6";
export const COLOR_PUT = "#f6465d";
export const COLOR_LINE_DEFAULT = "#5b9ce6";
// Grid lines sit barely above the card surface -- on a near-black ground a
// visible grid competes with the data. The previous #e1e0d9 was a near-white
// line chosen for a light background and would have glared here.
export const COLOR_GRID = "#20252d";
export const COLOR_TEXT_MUTED = "#8a93a1";
export const COLOR_HIGHLIGHT = "#e8eaee";

export const STATUS_GOOD = "#0ecb81"; // Cheap
export const STATUS_CRITICAL = "#f6465d"; // Rich
export const STATUS_NEUTRAL = "#8a93a1"; // Neutral

export const RICHNESS_BG: Record<string, string> = {
  cheap: "rgba(14, 203, 129, 0.14)",
  rich: "rgba(246, 70, 93, 0.14)",
  neutral: "rgba(138, 147, 161, 0.12)",
};

export const RICHNESS_TEXT: Record<string, string> = {
  cheap: "#0ecb81",
  rich: "#f6465d",
  neutral: COLOR_TEXT_MUTED,
};

export function richnessKey(label: string | null | undefined): "cheap" | "rich" | "neutral" {
  const lower = (label ?? "").toLowerCase();
  if (lower === "cheap") return "cheap";
  if (lower === "rich") return "rich";
  return "neutral";
}

// Shared column-header explainer text -- same wording everywhere (Overview,
// Vol Scanner, Trade Ideas) so "Richness" and "Skew Bias" mean one consistent
// thing across the app, not a slightly different explanation per page.
export const RICHNESS_HINT =
  "How expensive this expiry's IV looks vs. its own history (or realized vol, when available) -- " +
  "Rich means premium looks expensive to sell, Cheap means it looks expensive to buy. Says nothing " +
  "about which side (puts or calls) is pricier -- that's Skew Bias.";

export const SKEW_BIAS_HINT =
  "Which side of the smile is priced richer: put IV vs. call IV the same distance from the money. " +
  "Independent of Richness -- a cheap-IV expiry can still have a strong skew tilt, and vice versa, " +
  "so the two labels can disagree without it being a contradiction.";
