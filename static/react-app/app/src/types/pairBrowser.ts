export interface Pair {
  symbol: string;
  display: string;
  enabled: boolean;
  type: string;
  group: string;
}

export interface EngineAResult {
  direction?: string;
  confluenceScore?: number;
  maxScore?: number;
  threshold?: number;
  passed?: boolean;
  conviction?: string | number;
  entry?: number;
  sl?: number;
  tp1?: number;
  tp2?: number;
  tp3?: number;
  price?: number;
  regimeName?: string;
  trendState?: string;
  is_forming?: boolean;
  session?: { name?: string };
  aiAnalysis?: string;
  h4Candles?: unknown[];
  d1Candles?: unknown[];
  h1Candles?: unknown[];
  symbol?: string;
  type?: string;
  [key: string]: unknown;
}

export interface EngineBResult {
  direction?: string;
  current_swing_sequence?: string;
  macro_swing_sequence?: string;
  confluenceScore?: number;
  passed?: boolean;
  style?: string;
  current_price?: number;
  recommended_stop_loss?: number;
  stop_loss?: number;
  sl?: number;
  recommended_take_profit?: number;
  take_profit?: number;
  tp1?: number;
  tp?: number;
  rr?: number;
  type?: string;
  ai_analysis?: string;
  [key: string]: unknown;
}

export interface CompareSummary {
  verdict?: string;
  sameDirection?: boolean;
  structureAligned?: boolean;
  macroAligned?: boolean;
  engineAStyle?: string;
  engineBStyle?: string;
  engineBMinScore?: number;
  aiReviewIncluded?: boolean;
}

export interface CompareResult {
  summary?: CompareSummary;
  engineA?: EngineAResult;
  engineB?: EngineBResult;
}

export interface NewsStructured {
  direction?: string;
  sentiment_score?: number;
  confidence?: number;
  major_event_detected?: boolean;
  major_event_description?: string;
  reasoning_summary?: string;
  key_themes?: string[];
  article_count_used?: number;
  eodhd_agreement?: string;
}

export interface NewsResult {
  structured?: NewsStructured;
  confluenceVote?: number;
  eodhdTicker?: string;
}

export interface VisionStructured {
  rating?: string;
  confirms_direction?: boolean;
  right_edge_status?: string;
  style_ratings?: { scalp?: string; intraday?: string; swing?: string };
}

export interface VisionResult {
  model?: string;
  tf?: string;
  structured?: VisionStructured;
  analysis?: string;
  [key: string]: unknown;
}

export interface IntermarketRow {
  target?: string;
  driver?: string;
  relation?: string;
  correlation?: number;
  strength?: number;
  stability?: number;
  bestWindow?: string;
  regimeLabel?: string;
}

export interface IntermarketData {
  relationships?: IntermarketRow[];
}

export interface PBState {
  pairs: Pair[];
  selectedSymbol: string;
  assetClass: string;
  query: string;
  style: string;
  directionMode: string;
  filters: { news: boolean; inter: boolean; graph: boolean; aiVision: boolean };
  engineA: EngineAResult | null;
  engineB: EngineBResult | null;
  compare: CompareResult | null;
  news: NewsResult | null;
  vision: VisionResult | null;
  intermarket: IntermarketData | null;
  intermarketError: string;
  intermarketLoading: boolean;
  loadedPairs: boolean;
  loadingPairs: boolean;
  manualDir: string;
  manualStyle: string;
}
