/**
 * Typed fetch client for src/api (FastAPI backend). Every function mirrors
 * one endpoint in src/api/routes.py 1:1 — see that file for the source of
 * truth on shapes/params.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

function buildUrl(path: string, params?: Record<string, string | number | boolean | undefined>): URL {
  const url = new URL(path, API_BASE);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url;
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // response wasn't JSON; fall back to statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

async function apiGet<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const res = await fetch(buildUrl(path, params).toString());
  return handleResponse<T>(res);
}

async function apiPost<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const res = await fetch(buildUrl(path, params).toString(), { method: "POST" });
  return handleResponse<T>(res);
}

async function apiPostJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(buildUrl(path).toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(res);
}

// ---------------------------------------------------------------------------
// Types (mirror src/api/routes.py + src/api/schemas.py)
// ---------------------------------------------------------------------------

export interface SymbolInfo {
  symbol: string;
  color: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatResponse {
  reply: string;
}

export interface TermStructurePoint {
  expiration: string;
  dte: number;
  atm_iv: number | null;
}

export interface SkewPoint {
  expiration: string;
  dte: number;
  iv_25p: number | null;
  iv_25c: number | null;
  skew: number | null;
}

export interface CurvaturePoint {
  expiration: string;
  dte: number;
  curvature: number | null;
}

export interface VrpPoint {
  expiration: string;
  dte: number;
  realized_vol: number | null;
  vrp: number | null;
}

export interface ExpiryScoreRow {
  expiration: string;
  dte: number;
  atm_iv: number | null;
  iv_25p?: number | null;
  iv_25c?: number | null;
  skew: number | null;
  skew_ratio?: number | null;
  curvature: number | null;
  realized_vol?: number | null;
  vrp: number | null;
  vrp_z: number | null;
  iv_z: number | null;
  richness_z: number | null;
  richness_basis: "iv_history" | "vrp" | null;
  richness_label: string;
  skew_bias: string;
  has_wing_data: boolean;
}

export interface SymbolOverview {
  color: string;
  underlying_price: number | null;
  term_structure: TermStructurePoint[];
  skew: SkewPoint[];
  curvature: CurvaturePoint[];
  vrp: VrpPoint[] | null;
}

/** example_trade mirrors StrategyCandidate (declared below) -- same shape, reused rather than duplicated. */
export interface Commentary {
  headline: string;
  interpretation: string;
  trade_angle: string;
  example_trade: StrategyCandidate | null;
}

export interface OverviewResponse {
  primary: string;
  as_of: string;
  requested_symbols: string[];
  missing_symbols: string[];
  symbols: Record<string, SymbolOverview>;
  expiry_scores: ExpiryScoreRow[];
  takeaway: string | null;
  commentary: Commentary | null;
}

export interface ExpiryOption {
  expiration: string;
  dte: number;
}

export interface IVRank {
  current_iv: number;
  iv_rank: number;
  iv_percentile: number;
  lookback_low: number;
  lookback_high: number;
  n_observations: number;
}

export interface ZScore {
  current_value: number;
  trailing_mean: number;
  trailing_std: number;
  zscore: number;
  n_observations: number;
}

export interface StrategyLeg {
  action: "buy" | "sell";
  optionType: "CALL" | "PUT";
  strike: number;
  delta: number | null;
  mid: number;
  /** Only set for calendar_call's legs (its two legs expire on different
   * dates) -- null for every other structure, which shares one expiration
   * at the StrategyCandidate level. */
  expiration: string | null;
  implied_volatility: number | null;
  vega: number | null;
}

export interface PayoffPoint {
  underlying: number;
  pnl: number;
}

/** The forward-variance IV-crush edge estimate for a calendar spread (see
 * calendar_variance_edge() in strategy_engine.py) -- a modeled estimate
 * using real broker-supplied vega, not a guarantee. */
export interface VarianceEdge {
  iv_ex: number;
  front_crush: number;
  back_crush: number;
  front_vega_pnl: number;
  back_vega_pnl: number;
  net_vega_pnl: number;
}

export interface StrategyCandidate {
  structure: string;
  direction: "bullish" | "bearish" | "neutral";
  expiration: string;
  dte: number;
  legs: StrategyLeg[];
  net_debit_credit: number;
  /** Null for calendar_call -- expiration-intrinsic-value math would be
   * wrong for it (its long leg still has real time value at the short
   * leg's expiration). Every other structure always sets these. */
  max_profit: number | null;
  max_loss: number | null;
  breakevens: number[];
  approx_pop: number | null;
  payoff: PayoffPoint[];
  /** Calendar-only edge estimate; null for every other structure. */
  variance_edge: VarianceEdge | null;
}

export interface PositionSizing {
  capital_available: number;
  max_loss_per_contract: number;
  contracts: number;
  capital_used: number;
  capital_used_pct: number;
  total_max_profit: number;
  total_max_loss: number;
}

export interface RecommendResponse {
  symbol: string;
  direction: "bullish" | "bearish";
  timeline: "short" | "medium" | "long";
  risk: "conservative" | "moderate" | "aggressive";
  spot: number | null;
  recommendation: StrategyCandidate | null;
  sizing: PositionSizing | null;
  commentary: string | null;
}

export interface RecommendQueryParams {
  direction: "bullish" | "bearish";
  timeline: "short" | "medium" | "long";
  risk: "conservative" | "moderate" | "aggressive";
  capital?: number;
}

export interface RefreshResponse {
  succeeded: string[];
  /** Live pull returned no usable quotes (e.g. outside market hours) — not a failure, the last good snapshot is still being served. */
  unavailable: { symbol: string; message: string }[];
  failed: { symbol: string; error: string }[];
}

export type HistoryMetric = "atm_iv" | "skew" | "curvature" | "realized_vol" | "vrp";

export interface MetricSeriesPoint {
  snapshot_date: string;
  [metric: string]: unknown;
}

export interface PriceBar {
  symbol: string;
  price_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
}

// ---------------------------------------------------------------------------
// Scanner (cross-symbol snapshot table)
// ---------------------------------------------------------------------------

export interface ScannerRow {
  symbol: string;
  color: string;
  underlying_price: number | null;
  as_of: string | null;
  dte: number | null;
  expiration: string | null;
  atm_iv: number | null;
  iv_25p: number | null;
  iv_25c: number | null;
  skew: number | null;
  skew_bias: string | null;
  has_wing_data: boolean;
  curvature: number | null;
  realized_vol: number | null;
  richness_z: number | null;
  richness_label: string | null;
  richness_basis: "iv_history" | "vrp" | null;
  iv_rank: number | null;
  iv_percentile: number | null;
  days_of_history: number;
}

export interface ScannerResponse {
  target_dte: number;
  rows: ScannerRow[];
}

export interface CalendarEdgeRow {
  symbol: string;
  color: string;
  front_expiration: string;
  back_expiration: string;
  front_dte: number;
  back_dte: number;
  net_vega_pnl: number;
}

export interface CalendarEdgeResponse {
  front_dte: number;
  back_dte: number;
  target_delta: number;
  rows: CalendarEdgeRow[];
}

export interface PcrRow {
  symbol: string;
  color: string;
  put_oi: number;
  call_oi: number;
  pcr: number;
}

export interface PcrResponse {
  expiration: string | null;
  available_expirations: ExpiryOption[];
  rows: PcrRow[];
}

export interface StrikeProfileRow {
  strike: number;
  call_iv: number | null;
  put_iv: number | null;
  call_delta: number | null;
  put_delta: number | null;
  call_gamma: number | null;
  put_gamma: number | null;
  call_oi: number | null;
  put_oi: number | null;
}

export interface StrikeProfileResponse {
  symbol: string;
  expiration: string | null;
  available_expirations: ExpiryOption[];
  underlying_price: number | null;
  strikes: StrikeProfileRow[];
}

// ---------------------------------------------------------------------------
// Trade Ideas (cross-symbol actionable trade feed)
// ---------------------------------------------------------------------------

export interface TradeIdea {
  symbol: string;
  color: string;
  underlying_price: number | null;
  as_of: string | null;
  headline: string;
  reason: string;
  structure: string;
  direction: "bullish" | "bearish";
  is_credit: boolean;
  expiration: string;
  dte: number;
  legs: StrategyLeg[];
  net_debit_credit: number;
  max_profit: number;
  max_loss: number;
  reward_risk: number | null;
  approx_pop: number;
  breakevens: number[];
  payoff: PayoffPoint[];
  richness_label: string | null;
  richness_z: number | null;
  richness_basis: "iv_history" | "vrp" | null;
  skew_bias: string | null;
  skew: number | null;
  has_wing_data: boolean;
}

export interface TradeIdeasResponse {
  ideas: TradeIdea[];
}

// ---------------------------------------------------------------------------
// Backtest (historical trade simulator)
// ---------------------------------------------------------------------------

export interface BacktestExpirationsResponse {
  symbol: string;
  entry_date: string;
  expirations: ExpiryOption[];
}

export interface EquityPoint {
  date: string;
  dte_remaining: number;
  pnl_per_share: number;
  underlying_price: number | null;
}

export interface BacktestResult {
  entry_date: string;
  entry_candidate: StrategyCandidate;
  equity_curve: EquityPoint[];
  status: "open" | "closed";
  final_pnl_per_share: number;
  days_held: number;
  summary: string;
}

export type BacktestStructure =
  | "bull_call"
  | "bull_put"
  | "bear_call"
  | "bear_put"
  | "cash_secured_put"
  | "covered_call"
  | "calendar_call";

/** Every structure backtestable end to end, in the order shown in pickers --
 * verticals first (grouped bullish/bearish), then the two single-leg
 * structures, then the calendar. Shared by the primary and comparison-mode
 * pickers. */
export const BACKTEST_STRUCTURES: { value: BacktestStructure; label: string }[] = [
  { value: "bull_call", label: "Bull Call Spread" },
  { value: "bull_put", label: "Bull Put Spread" },
  { value: "bear_call", label: "Bear Call Spread" },
  { value: "bear_put", label: "Bear Put Spread" },
  { value: "cash_secured_put", label: "Cash Secured Put" },
  { value: "covered_call", label: "Covered Call" },
  { value: "calendar_call", label: "Calendar Call Spread" },
];

/** Verticals take a width (# strikes between the two legs); the single-leg
 * structures and the calendar (which has its own back-month expiration
 * control instead) don't. */
export function structureHasWidth(structure: BacktestStructure): boolean {
  return structure !== "cash_secured_put" && structure !== "covered_call" && structure !== "calendar_call";
}

/** The calendar is the only structure whose two legs expire on different
 * dates -- it needs its own back-month expiration picker instead of the
 * width control every other structure uses. */
export function structureIsCalendar(structure: BacktestStructure): boolean {
  return structure === "calendar_call";
}

export interface BacktestRunResponse {
  symbol: string;
  entry_date: string;
  expiration: string;
  structure: BacktestStructure;
  target_delta: number;
  width_strikes: number;
  back_expiration: string | null;
  result: BacktestResult | null;
  /** Set (with `result: null`) when no such structure could be built for this date/expiration/delta/width combination. */
  error: string | null;
}

export interface BacktestRunParams {
  entryDate: string;
  expiration: string;
  structure: BacktestStructure;
  targetDelta: number;
  widthStrikes: number;
  /** Required when structure is calendar_call, ignored otherwise. */
  backExpiration?: string;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  symbols: () => apiGet<SymbolInfo[]>("/api/symbols"),

  overview: (symbols: string[], dteMin: number, dteMax: number) =>
    apiGet<OverviewResponse>("/api/overview", {
      symbols: symbols.join(","),
      dte_min: dteMin,
      dte_max: dteMax,
    }),

  recommendStrategy: (symbol: string, params: RecommendQueryParams) =>
    apiGet<RecommendResponse>(`/api/strategy/${symbol}/recommend`, { ...params }),

  ivRank: (symbol: string) => apiGet<IVRank | null>(`/api/history/${symbol}/iv-rank`),

  zscore: (symbol: string, metric: string, targetDte?: number, lookbackDays?: number) =>
    apiGet<ZScore | null>(`/api/history/${symbol}/zscore`, {
      metric,
      target_dte: targetDte,
      lookback_days: lookbackDays,
    }),

  priceSeries: (symbol: string, start?: string, end?: string) =>
    apiGet<{ symbol: string; prices: PriceBar[] }>(`/api/history/${symbol}/price-series`, { start, end }),

  metricSeries: (symbol: string, metric: HistoryMetric, targetDte = 30, lookbackDays = 365) =>
    apiGet<{ symbol: string; metric: string; series: MetricSeriesPoint[] }>(
      `/api/history/${symbol}/metric-series`,
      { metric, target_dte: targetDte, lookback_days: lookbackDays }
    ),

  refresh: (symbols: string[]) => apiPost<RefreshResponse>("/api/refresh", { symbols: symbols.join(",") }),

  backtestDates: (symbol: string) =>
    apiGet<{ symbol: string; dates: string[] }>(`/api/history/${symbol}/options-snapshot-dates`),

  backtestExpirations: (symbol: string, entryDate: string) =>
    apiGet<BacktestExpirationsResponse>(`/api/backtest/${symbol}/expirations`, { entry_date: entryDate }),

  runBacktest: (symbol: string, params: BacktestRunParams) =>
    apiGet<BacktestRunResponse>(`/api/backtest/${symbol}/run`, {
      entry_date: params.entryDate,
      expiration: params.expiration,
      structure: params.structure,
      target_delta: params.targetDelta,
      width_strikes: params.widthStrikes,
      back_expiration: params.backExpiration,
    }),

  scanner: (targetDte = 30) => apiGet<ScannerResponse>("/api/scanner", { target_dte: targetDte }),

  scannerPcr: (expiration?: string) => apiGet<PcrResponse>("/api/scanner/pcr", { expiration }),

  calendarEdge: (frontDte = 7, backDte = 30) =>
    apiGet<CalendarEdgeResponse>("/api/scanner/calendar-edge", { front_dte: frontDte, back_dte: backDte }),

  scannerStrikeProfile: (symbol: string, expiration?: string) =>
    apiGet<StrikeProfileResponse>("/api/scanner/strike-profile", { symbol, expiration }),

  tradeIdeas: () => apiGet<TradeIdeasResponse>("/api/trade-ideas"),

  chat: (message: string, history: ChatMessage[]) =>
    apiPostJson<ChatResponse>("/api/chat", { message, history }),
};
