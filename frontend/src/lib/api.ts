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

export interface SurfaceCell {
  dte: number;
  /** strike / spot - 1, snapped to a 2.5% bucket so expirations share one axis. */
  moneyness: number;
  iv: number;
  /** Which side the quote came from -- OTM only, so puts below spot and calls
   * above. ITM quotes are wide and intrinsic-dominated and would put a
   * spurious step at the money. */
  side: "CALL" | "PUT";
  contracts: number;
  /** Only present in the comparison view. */
  iv_prev?: number;
  change?: number;
}

export interface SurfaceComparison {
  compare_date: string;
  /** Real elapsed days, not an assumed week -- pipeline outages mean the
   * nearest stored snapshot is often much further back than intended. */
  days_elapsed: number;
  prior_spot: number | null;
  spot_change_pct: number | null;
  cells: SurfaceCell[];
  change_min: number | null;
  change_max: number | null;
}

export interface SurfaceResponse {
  symbol: string;
  as_of: string;
  spot: number | null;
  expirations: { dte: number; expiration: string }[];
  moneyness: number[];
  cells: SurfaceCell[];
  iv_min: number | null;
  iv_max: number | null;
  available_compare_dates: string[];
  comparison: SurfaceComparison | null;
}

export interface CalendarEdgeRow {
  symbol: string;
  color: string;
  front_expiration: string;
  back_expiration: string;
  front_dte: number;
  back_dte: number;
  /** Net debit paid to open one contract -- the real capital required. */
  net_debit_credit: number | null;
  /** A genuine worst-case upper bound (real math off the strikes/premium,
   * not a fabricated number) -- conservatively ignores the back leg's
   * remaining time value, so the real worst case is typically a bit lower. */
  est_max_loss: number | null;
  net_vega_pnl: number;
  /** Modeled edge as a % of capital required -- a more comparable ranking
   * than the raw dollar edge alone. Null when est_max_loss is 0/unavailable. */
  edge_per_capital_pct: number | null;
  /** When the underlying snapshot was taken. The ranking is only as current
   * as the data behind it. */
  as_of: string;
  /** The full structure behind the ranking -- both legs and the
   * variance_edge decomposition. Note payoff/max_profit/breakevens are
   * empty for calendars by design: there's no honest payoff curve without
   * an options-pricing model, so don't plot one from this. */
  candidate: StrategyCandidate;
}

export interface TermStructureRow {
  symbol: string;
  color: string;
  near_dte: number;
  far_dte: number;
  near_iv: number;
  far_iv: number;
  /** far_iv - near_iv -- positive = normal/contango, negative = inverted. */
  iv_slope: number;
  near_skew: number;
  far_skew: number;
  /** far_skew - near_skew -- whether downside skew gets more or less
   * pronounced further out. */
  skew_slope: number;
}

export interface TermStructureResponse {
  near_dte: number;
  far_dte: number;
  rows: TermStructureRow[];
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

// ---------------------------------------------------------------------------
// Trade Ideas (cross-symbol actionable trade feed)
// ---------------------------------------------------------------------------

/** An earnings announcement landing before this trade expires, and how much
 * of the premium the market is charging for it. Null when no announcement
 * falls before expiry -- check `calendar_known` to tell that apart from
 * "no date on file". See src/dashboard/earnings.py. */
export interface IdeaEvent {
  date: string;
  days_away: number;
  /** Implied one-off move as a % of spot, from the term structure. Null when
   * the chain cannot support the calculation -- never a guess. */
  implied_move_pct: number | null;
  before_dte: number | null;
  after_dte: number | null;
}

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
  event: IdeaEvent | null;
  /** False means the hand-maintained calendar has no dates for this symbol,
   * which is a gap in our knowledge -- not an all-clear. */
  calendar_known: boolean;
  skew_bias: string | null;
  skew: number | null;
  has_wing_data: boolean;
}

export interface TradeIdeasResponse {
  ideas: TradeIdea[];
}

/** `demo_mode` is true on the shared deployment, where the credential-backed
 * routes (/api/refresh, /api/chat) are removed. See src/api/demo.py. */
export interface HealthResponse {
  status: string;
  demo_mode: boolean;
}

export interface SyncResponse {
  changed: boolean;
  message: string;
  before: string | null;
  after: string | null;
  seconds: number | null;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  health: () => apiGet<HealthResponse>("/api/health"),

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

  /** Pulls whatever the daily pipeline has collected. Does NOT call Schwab --
   * see src/dashboard/data_sync.py for why a live pull is the wrong
   * operation now that the dashboard reads the committed database. */
  sync: () => apiPost<SyncResponse>("/api/sync", {}),

  refresh: (symbols: string[]) => apiPost<RefreshResponse>("/api/refresh", { symbols: symbols.join(",") }),




  scanner: (targetDte = 30) => apiGet<ScannerResponse>("/api/scanner", { target_dte: targetDte }),

  scannerPcr: (expiration?: string) => apiGet<PcrResponse>("/api/scanner/pcr", { expiration }),

  calendarEdge: (frontDte = 7, backDte = 30, targetDelta = 0.25) =>
    apiGet<CalendarEdgeResponse>("/api/scanner/calendar-edge", {
      front_dte: frontDte,
      back_dte: backDte,
      target_delta: targetDelta,
    }),

  termStructure: (nearDte = 7, farDte = 60) =>
    apiGet<TermStructureResponse>("/api/scanner/term-structure", { near_dte: nearDte, far_dte: farDte }),

  surface: (symbol: string, compare?: string) =>
    apiGet<SurfaceResponse>("/api/surface", { symbol, compare }),


  tradeIdeas: () => apiGet<TradeIdeasResponse>("/api/trade-ideas"),

  chat: (message: string, history: ChatMessage[]) =>
    apiPostJson<ChatResponse>("/api/chat", { message, history }),
};
