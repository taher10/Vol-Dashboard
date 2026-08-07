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

// ---------------------------------------------------------------------------
// Types (mirror src/api/routes.py + src/api/schemas.py)
// ---------------------------------------------------------------------------

export interface SymbolInfo {
  symbol: string;
  color: string;
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

export interface SmilePoint {
  optionType: "CALL" | "PUT";
  delta: number;
  impliedVolatility: number;
  strikePrice: number;
}

export interface ExpiryOption {
  expiration: string;
  dte: number;
}

export interface ExpiryResponse {
  symbol: string;
  expiration: string;
  expirations: ExpiryOption[];
  underlying_price: number | null;
  smile: SmilePoint[];
  score: ExpiryScoreRow | null;
  commentary: Commentary | null;
  neighbors: (ExpiryScoreRow & { position: string })[];
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
}

export interface PayoffPoint {
  underlying: number;
  pnl: number;
}

export interface StrategyCandidate {
  structure: string;
  direction: "bullish" | "bearish";
  expiration: string;
  dte: number;
  legs: StrategyLeg[];
  net_debit_credit: number;
  max_profit: number;
  max_loss: number;
  breakevens: number[];
  approx_pop: number;
  payoff: PayoffPoint[];
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
  skew: number | null;
  skew_bias: string | null;
  curvature: number | null;
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

// ---------------------------------------------------------------------------
// Trade Ideas (cross-symbol actionable trade feed)
// ---------------------------------------------------------------------------

export interface TradeIdea {
  symbol: string;
  color: string;
  underlying_price: number | null;
  as_of: string | null;
  headline: string;
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
  richness_label: string | null;
  richness_z: number | null;
  richness_basis: "iv_history" | "vrp" | null;
  skew_bias: string | null;
  skew: number | null;
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

export interface BacktestRunResponse {
  symbol: string;
  entry_date: string;
  expiration: string;
  direction: "bullish" | "bearish";
  risk: "conservative" | "moderate" | "aggressive";
  result: BacktestResult | null;
  /** Set (with `result: null`) when no vertical could be built for this date/expiration/direction/risk combination. */
  error: string | null;
}

export interface BacktestRunParams {
  entryDate: string;
  expiration: string;
  direction: "bullish" | "bearish";
  risk: "conservative" | "moderate" | "aggressive";
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

  expiry: (symbol: string, expiration?: string) =>
    apiGet<ExpiryResponse>(`/api/expiry/${symbol}`, { expiration }),

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
      direction: params.direction,
      risk: params.risk,
    }),

  scanner: (targetDte = 30) => apiGet<ScannerResponse>("/api/scanner", { target_dte: targetDte }),

  tradeIdeas: () => apiGet<TradeIdeasResponse>("/api/trade-ideas"),
};
