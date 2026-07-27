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
  richness_label: string;
  skew_bias: string;
  has_wing_data: boolean;
}

export interface SymbolOverview {
  color: string;
  term_structure: TermStructurePoint[];
  skew: SkewPoint[];
  curvature: CurvaturePoint[];
  vrp: VrpPoint[] | null;
}

export interface OverviewResponse {
  primary: string;
  as_of: string;
  requested_symbols: string[];
  missing_symbols: string[];
  symbols: Record<string, SymbolOverview>;
  expiry_scores: ExpiryScoreRow[];
  takeaway: string | null;
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
  smile: SmilePoint[];
  score: ExpiryScoreRow | null;
  neighbors: (ExpiryScoreRow & { position: string })[];
}

export interface ContractRow {
  optionType: "CALL" | "PUT";
  expiration: string;
  dte: number;
  strikePrice: number;
  bid: number;
  ask: number;
  mid: number;
  spread_pct: number;
  volume: number;
  openInterest: number;
  delta: number;
  impliedVolatility: number;
  liquidity_score: number | null;
  delta_fit_score: number | null;
  value_score: number | null;
  composite_score: number | null;
  rank: number;
  [key: string]: unknown;
}

export interface ContractsResponse {
  symbol: string;
  expiration: string | null;
  total_matched: number;
  contracts: ContractRow[];
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

export interface ContractQueryParams {
  expiration?: string;
  option_type?: "BOTH" | "CALL" | "PUT";
  intent?: "buy" | "sell";
  target_delta?: number;
  delta_tolerance?: number;
  w_value?: number;
  w_delta_fit?: number;
  w_liquidity?: number;
  dte_min?: number;
  dte_max?: number;
  min_volume?: number;
  min_open_interest?: number;
  max_spread_pct?: number;
  limit?: number;
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

  contracts: (symbol: string, params: ContractQueryParams) =>
    apiGet<ContractsResponse>(`/api/contracts/${symbol}`, { ...params }),

  ivRank: (symbol: string) => apiGet<IVRank | null>(`/api/history/${symbol}/iv-rank`),

  zscore: (symbol: string, metric: string) =>
    apiGet<ZScore | null>(`/api/history/${symbol}/zscore`, { metric }),

  priceSeries: (symbol: string, start?: string, end?: string) =>
    apiGet<{ symbol: string; prices: PriceBar[] }>(`/api/history/${symbol}/price-series`, { start, end }),

  metricSeries: (symbol: string, metric: HistoryMetric, targetDte = 30, lookbackDays = 365) =>
    apiGet<{ symbol: string; metric: string; series: MetricSeriesPoint[] }>(
      `/api/history/${symbol}/metric-series`,
      { metric, target_dte: targetDte, lookback_days: lookbackDays }
    ),

  refresh: (symbols: string[]) => apiPost<RefreshResponse>("/api/refresh", { symbols: symbols.join(",") }),
};
