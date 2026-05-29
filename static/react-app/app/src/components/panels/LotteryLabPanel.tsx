import { useCallback, useMemo, useState } from 'react';
import apiClient from '@/lib/apiClient';
import { useStore } from '@/hooks/useStore';
import { useApiPoll } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ErrorBanner } from '@/components/shared';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Brain,
  Calculator,
  CheckCircle2,
  Dices,
  GitCompare,
  History,
  ListChecks,
  RefreshCw,
  Search,
  Sparkles,
  Table2,
  Target,
  TrendingUp,
  Upload,
  Wand2,
} from 'lucide-react';

type LotteryGame = 'lotto' | 'powerball' | 'daily_lotto';
type GeneratorMode = 'pure_random' | 'hot_bias' | 'cold_bias' | 'overdue_bias' | 'balanced_mix' | 'pair_bias' | 'anti_crowd';

interface GameConfig {
  value: LotteryGame;
  label: string;
  mainCount: number;
  mainMin: number;
  mainMax: number;
  hasBonus: boolean;
  bonusMin?: number;
  bonusMax?: number;
}

const GAMES: GameConfig[] = [
  { value: 'lotto', label: 'SA Lotto', mainCount: 6, mainMin: 1, mainMax: 58, hasBonus: true, bonusMin: 1, bonusMax: 58 },
  { value: 'powerball', label: 'Powerball', mainCount: 5, mainMin: 1, mainMax: 50, hasBonus: true, bonusMin: 1, bonusMax: 20 },
  { value: 'daily_lotto', label: 'Daily Lotto', mainCount: 5, mainMin: 1, mainMax: 36, hasBonus: false },
];

const GAME_BY_VALUE = GAMES.reduce<Record<LotteryGame, GameConfig>>((acc, item) => {
  acc[item.value] = item;
  return acc;
}, {} as Record<LotteryGame, GameConfig>);

const GENERATOR_MODES: { value: GeneratorMode; label: string }[] = [
  { value: 'pure_random', label: 'Pure random' },
  { value: 'hot_bias', label: 'Hot bias' },
  { value: 'cold_bias', label: 'Cold bias' },
  { value: 'overdue_bias', label: 'Overdue bias' },
  { value: 'balanced_mix', label: 'Balanced mix' },
  { value: 'pair_bias', label: 'Pair bias' },
  { value: 'anti_crowd', label: 'Anti crowd' },
];

const DEFAULT_COMPARE_MODES = 'pure_random, hot_bias, cold_bias, overdue_bias, balanced_mix, pair_bias, anti_crowd';
const DEFAULT_PAYOUT_TABLE = '{\n  "3": 10,\n  "4": 75,\n  "5": 500,\n  "6": 20000\n}';

interface NumberMetricRow {
  number: number;
  count?: number;
  avg_gap_draws?: number | null;
  draws_since_seen?: number;
  last_seen_date?: string | null;
  recent_count?: number;
  alltime_count?: number;
  expected_recent?: number | null;
  z_score?: number | null;
  trend?: string;
  score?: number;
}

interface LotteryRules {
  main_count: number;
  has_bonus: boolean;
  main_min: number;
  main_max: number;
  bonus_min?: number | null;
  bonus_max?: number | null;
}

interface LotteryDashboard {
  game?: LotteryGame;
  rules?: LotteryRules;
  total_draws: number;
  first_draw_date?: string | null;
  last_draw_date?: string | null;
  hot_numbers?: NumberMetricRow[];
  cold_numbers?: NumberMetricRow[];
  overdue_numbers?: NumberMetricRow[];
  number_frequency?: NumberMetricRow[];
  sum_distribution?: { sum: number; count: number }[];
  odd_even_distribution?: PatternRow[];
  low_high_distribution?: PatternRow[];
  recommended_sum_range?: {
    min?: number | null;
    max?: number | null;
    coverage_pct?: number | null;
  };
  consecutive_summary?: {
    draws_with_consecutive?: number;
    draws_with_consecutive_pct?: number;
    average_consecutive_pairs?: number;
    max_consecutive_streak?: number;
    pair_breakdown?: { consecutive_pairs: number; count: number }[];
  };
  repeat_from_last_draw_summary?: {
    comparisons?: number;
    average_repeat_count?: number;
    distribution?: { repeat_count: number; count: number }[];
  };
  entropy?: EntropyResponse | null;
  invalid_rows_skipped?: number;
}

interface LotteryDrawRow {
  draw_date: string;
  numbers: number[];
  bonus?: number | null;
  source_file?: string | null;
  imported_at?: string | null;
}

interface LotteryDrawsEnvelope {
  game?: LotteryGame;
  draw_count?: number;
  history?: LotteryDrawRow[];
}

interface LotteryFrequencyResponse {
  game?: LotteryGame;
  draw_count?: number;
  number_frequency?: NumberMetricRow[];
  overdue?: NumberMetricRow[];
  decade_groups?: { bucket: string; count: number }[];
  bonus_frequency?: NumberMetricRow[];
  bonus_overdue?: NumberMetricRow[];
}

interface PairFrequencyResponse {
  game?: LotteryGame;
  draw_count?: number;
  pairs?: { pair: number[]; count: number }[];
}

interface TripletFrequencyResponse {
  game?: LotteryGame;
  draw_count?: number;
  triplets?: { triplet: number[]; count: number }[];
}

interface PatternRow {
  pattern: string;
  count: number;
}

interface SumDistributionResponse {
  summary?: {
    min_sum?: number | null;
    max_sum?: number | null;
    avg_sum?: number | null;
    median_sum?: number | null;
  };
  distribution?: { sum: number; count: number }[];
}

interface DistributionsResponse {
  game?: LotteryGame;
  sum_distribution?: SumDistributionResponse;
  odd_even_distribution?: { patterns?: PatternRow[] };
  low_high_distribution?: { threshold?: number; patterns?: PatternRow[] };
  consecutive?: {
    draws_with_consecutive?: number;
    draws_with_consecutive_pct?: number;
    average_consecutive_pairs?: number;
    max_consecutive_streak?: number;
    pair_breakdown?: { consecutive_pairs: number; count: number }[];
  };
  repeat_from_last_draw?: {
    comparisons?: number;
    average_repeat_count?: number;
    distribution?: { repeat_count: number; count: number }[];
    recent_overlap?: { draw_date: string; previous_draw_date: string; repeat_count: number }[];
  };
}

interface SumRangeResponse {
  game?: LotteryGame;
  draw_count?: number;
  min_sum?: number | null;
  max_sum?: number | null;
  p_low?: number | null;
  p_high?: number | null;
  recommended_min?: number | null;
  recommended_max?: number | null;
  coverage_pct?: number | null;
}

interface PositionalResponse {
  game?: LotteryGame;
  draw_count?: number;
  positions?: {
    position: number;
    min?: number | null;
    max?: number | null;
    mean?: number | null;
    median?: number | null;
    p10?: number | null;
    p25?: number | null;
    p75?: number | null;
    p90?: number | null;
  }[];
}

interface RollingFrequencyResponse {
  game?: LotteryGame;
  draw_count?: number;
  window_used?: number;
  chi2_stat?: number | null;
  chi2_p_value?: number | null;
  chi2_interpretation?: string | null;
  numbers?: NumberMetricRow[];
}

interface PairLiftResponse {
  game?: LotteryGame;
  draw_count?: number;
  pairs?: {
    pair: number[];
    observed: number;
    freq_a: number;
    freq_b: number;
    expected: number;
    lift: number;
    confidence_ab: number;
    confidence_ba: number;
  }[];
}

interface AnomaliesResponse {
  game?: LotteryGame;
  draw_count?: number;
  z_threshold?: number;
  anomalous_count?: number;
  anomalous_draws?: {
    draw_date: string;
    numbers: number[];
    bonus?: number | null;
    flags?: string[];
    z_scores?: Record<string, number>;
  }[];
  metric_stats?: Record<string, { mean?: number | null; stddev?: number | null }>;
}

interface EntropyResponse {
  game?: LotteryGame;
  eligible?: boolean;
  draw_count?: number;
  pool_size?: number;
  h_max_bits?: number | null;
  per_ball?: {
    entropy_bits?: number | null;
    entropy_ratio?: number | null;
    total_observations?: number;
  } | null;
  rolling?: { window: number; draws_used: number; entropy_bits?: number | null; entropy_ratio?: number | null }[];
  rolling_trend?: string;
  positional?: { position: number; distinct_values?: number; entropy_bits?: number | null; entropy_ratio?: number | null }[];
  fairness?: string;
}

interface BonusIntelligenceResponse {
  game?: LotteryGame;
  has_bonus?: boolean;
  draw_count?: number;
  window_used?: number;
  bonus_min?: number;
  bonus_max?: number;
  frequency?: NumberMetricRow[];
  overdue?: NumberMetricRow[];
  rolling?: NumberMetricRow[];
  top_picks?: NumberMetricRow[];
  most_recent_bonus?: number | null;
  last_draw_date?: string | null;
}

interface LotteryTicket {
  numbers: number[];
  bonus?: number | null;
  score?: number;
  labels?: string[];
  pair_strength?: number;
  frequency_score?: number;
  overdue_score?: number;
}

interface LotteryGenerateResponse {
  game?: LotteryGame;
  mode?: string;
  ticket_count?: number;
  include_bonus?: boolean;
  tickets?: LotteryTicket[];
  fallback_tickets_used?: number;
  per_ticket_retry_cap?: number;
}

interface ScoreTicketResponse extends LotteryTicket {
  game?: LotteryGame;
}

interface WheelResponse {
  game?: LotteryGame;
  chosen_numbers?: number[];
  guarantee_if?: number;
  main_count?: number;
  ticket_count?: number;
  tickets?: number[][];
  coverage_verified?: boolean;
  note?: string;
  is_minimal?: boolean;
}

interface SimulationResponse {
  game?: LotteryGame;
  mode?: string;
  draws_simulated?: number;
  tickets_generated?: number;
  total_cost?: number;
  total_return?: number;
  roi?: number | null;
  ev_per_ticket?: number;
  ev_net_per_ticket?: number | null;
  hit_distribution?: Record<string, number>;
  hit_rates?: Record<string, number>;
  best_hit?: {
    main_hits?: number;
    bonus_hit?: boolean;
    ticket?: number[] | null;
    bonus?: number | null;
    draw_date?: string | null;
  };
  average_hits_per_draw?: number;
  mode_summary?: {
    label_distribution?: Record<string, number>;
    filters?: Record<string, unknown>;
    ticket_cost?: number;
    payout_table_used?: boolean;
  };
}

interface CompareModesResponse {
  game?: LotteryGame;
  tickets_per_draw?: number;
  include_bonus?: boolean;
  modes?: {
    mode: string;
    draws_simulated?: number;
    tickets_generated?: number;
    total_cost?: number;
    total_return?: number;
    roi?: number | null;
    average_hits_per_draw?: number;
    best_hit?: SimulationResponse['best_hit'];
  }[];
}

interface LotteryAiResponse {
  analysis?: Record<string, unknown>;
  raw_analysis_text?: string;
  model?: string;
  recommended_pool?: number[];
  avoid_numbers?: number[];
  generator_mode?: string;
  bonus_picks?: number[];
  sum_filter?: Record<string, unknown>;
  entropy_assessment?: Record<string, unknown>;
  confidence?: string;
  reasoning?: Record<string, unknown>;
}

function toBoundedInt(value: string | number, fallback: number, min: number, max: number) {
  const parsed = typeof value === 'number' ? value : parseInt(String(value || ''), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

function optionalInt(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = parseInt(trimmed, 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function optionalFloat(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = parseFloat(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function parseNumberList(value: string) {
  return value
    .split(/[,;\s]+/)
    .map((part) => parseInt(part.trim(), 10))
    .filter((number) => Number.isFinite(number));
}

function formatValue(value: unknown, digits = 2) {
  if (value === null || value === undefined || value === '') return 'n/a';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(digits);
  return String(value);
}

function formatPercent(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'n/a';
  return `${(value * 100).toFixed(2)}%`;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error || 'Request failed');
}

function MetricCard({
  label,
  value,
  icon,
  detail,
}: {
  label: string;
  value: string | number;
  icon?: React.ReactNode;
  detail?: string;
}) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[10px] font-medium uppercase text-muted-foreground">{label}</p>
          {icon}
        </div>
        <p className="mt-1 text-xl font-mono font-bold">{value}</p>
        {detail && <p className="mt-1 text-[11px] text-muted-foreground">{detail}</p>}
      </CardContent>
    </Card>
  );
}

function EmptyState({ message }: { message: string }) {
  return <div className="py-10 text-center text-sm text-muted-foreground">{message}</div>;
}

function LoadingRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
      ))}
    </div>
  );
}

function NumberPills({
  numbers,
  bonus,
  size = 'md',
}: {
  numbers?: number[];
  bonus?: number | null;
  size?: 'sm' | 'md' | 'lg';
}) {
  const boxClass = size === 'lg' ? 'h-10 w-10 text-sm' : size === 'sm' ? 'h-6 w-6 text-[10px]' : 'h-8 w-8 text-xs';
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {(numbers || []).map((number) => (
        <span
          key={`${number}-${size}`}
          className={`${boxClass} inline-flex shrink-0 items-center justify-center rounded-full bg-primary/15 font-mono font-bold text-primary`}
        >
          {number}
        </span>
      ))}
      {bonus !== null && bonus !== undefined && (
        <span
          className={`${boxClass} inline-flex shrink-0 items-center justify-center rounded-full bg-amber-500/15 font-mono font-bold text-amber-400`}
        >
          {bonus}
        </span>
      )}
    </div>
  );
}

function PatternBadges({ rows, empty }: { rows?: PatternRow[]; empty: string }) {
  if (!rows?.length) return <EmptyState message={empty} />;
  return (
    <div className="flex flex-wrap gap-1.5">
      {rows.slice(0, 12).map((row) => (
        <Badge key={row.pattern} variant="outline" className="font-mono text-[10px]">
          {row.pattern}: {row.count}
        </Badge>
      ))}
    </div>
  );
}

function validateMainNumbers(numbers: number[], config: GameConfig) {
  if (numbers.length !== config.mainCount) {
    return `Expected ${config.mainCount} main numbers for ${config.label}`;
  }
  if (new Set(numbers).size !== numbers.length) {
    return 'Main numbers must not contain duplicates';
  }
  const outOfRange = numbers.filter((number) => number < config.mainMin || number > config.mainMax);
  if (outOfRange.length) {
    return `Main numbers must be between ${config.mainMin} and ${config.mainMax}`;
  }
  return null;
}

function validateBonusNumber(value: number | undefined, config: GameConfig) {
  if (!config.hasBonus) return null;
  if (value === undefined) return 'Bonus is required for this game';
  if (config.bonusMin !== undefined && config.bonusMax !== undefined && (value < config.bonusMin || value > config.bonusMax)) {
    return `Bonus must be between ${config.bonusMin} and ${config.bonusMax}`;
  }
  return null;
}

export default function LotteryLabPanel() {
  const { showToast } = useStore();
  const [activeTab, setActiveTab] = useState('overview');
  const [game, setGame] = useState<LotteryGame>('lotto');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [includeBonus, setIncludeBonus] = useState(true);
  const [limit, setLimit] = useState('200');
  const [windowSize, setWindowSize] = useState('50');
  const [zThreshold, setZThreshold] = useState('2.5');

  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [drawDate, setDrawDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [newDraw, setNewDraw] = useState('');
  const [optionalBonus, setOptionalBonus] = useState('');
  const [importing, setImporting] = useState(false);
  const [addDrawLoading, setAddDrawLoading] = useState(false);

  const [generatorMode, setGeneratorMode] = useState<GeneratorMode>('balanced_mix');
  const [ticketCount, setTicketCount] = useState('5');
  const [maxConsecutiveRun, setMaxConsecutiveRun] = useState('4');
  const [oddEvenTarget, setOddEvenTarget] = useState('');
  const [lowHighTarget, setLowHighTarget] = useState('');
  const [minSum, setMinSum] = useState('');
  const [maxSum, setMaxSum] = useState('');
  const [recentOverlapCap, setRecentOverlapCap] = useState('');
  const [generatedTickets, setGeneratedTickets] = useState<LotteryTicket[]>([]);
  const [generateMeta, setGenerateMeta] = useState<LotteryGenerateResponse | null>(null);
  const [generateLoading, setGenerateLoading] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  const [scoreInput, setScoreInput] = useState('');
  const [scoreBonus, setScoreBonus] = useState('');
  const [scoredTicket, setScoredTicket] = useState<ScoreTicketResponse | null>(null);
  const [scoreLoading, setScoreLoading] = useState(false);
  const [scoreError, setScoreError] = useState<string | null>(null);

  const [wheelNumbers, setWheelNumbers] = useState('');
  const [guaranteeIf, setGuaranteeIf] = useState('3');
  const [wheelResult, setWheelResult] = useState<WheelResponse | null>(null);
  const [wheelLoading, setWheelLoading] = useState(false);
  const [wheelError, setWheelError] = useState<string | null>(null);

  const [ticketsPerDraw, setTicketsPerDraw] = useState('3');
  const [ticketCost, setTicketCost] = useState('5');
  const [seed, setSeed] = useState('');
  const [payoutTable, setPayoutTable] = useState(DEFAULT_PAYOUT_TABLE);
  const [simulation, setSimulation] = useState<SimulationResponse | null>(null);
  const [simulationLoading, setSimulationLoading] = useState(false);
  const [simulationError, setSimulationError] = useState<string | null>(null);

  const [compareModes, setCompareModes] = useState(DEFAULT_COMPARE_MODES);
  const [comparison, setComparison] = useState<CompareModesResponse | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);

  const [aiAnalysis, setAiAnalysis] = useState<LotteryAiResponse | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

  const config = GAME_BY_VALUE[game];
  const effectiveIncludeBonus = includeBonus && config.hasBonus;
  const safeLimit = toBoundedInt(limit, 200, 1, 2000);
  const safeWindow = toBoundedInt(windowSize, 50, 1, 500);
  const safeZThreshold = optionalFloat(zThreshold) ?? 2.5;

  const baseQuery = useMemo(() => {
    const params = new URLSearchParams();
    params.set('game', game);
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    params.set('include_bonus', String(effectiveIncludeBonus));
    params.set('limit', String(safeLimit));
    return `?${params.toString()}`;
  }, [endDate, effectiveIncludeBonus, game, safeLimit, startDate]);

  const windowQuery = useMemo(() => {
    const params = new URLSearchParams(baseQuery.slice(1));
    params.set('window', String(safeWindow));
    return `?${params.toString()}`;
  }, [baseQuery, safeWindow]);

  const anomalyQuery = useMemo(() => {
    const params = new URLSearchParams(baseQuery.slice(1));
    params.set('z_threshold', String(safeZThreshold));
    return `?${params.toString()}`;
  }, [baseQuery, safeZThreshold]);

  const {
    data: dashboard,
    loading: dashLoading,
    error: dashError,
    refresh: refreshDashboard,
  } = useApiPoll<LotteryDashboard>(`/api/lottery/dashboard${baseQuery}`, 0);
  const {
    data: freqRaw,
    loading: freqLoading,
    error: freqError,
    refresh: refreshFrequency,
  } = useApiPoll<LotteryFrequencyResponse>(`/api/lottery/frequency${baseQuery}`, 0);
  const {
    data: drawsEnvelope,
    loading: drawsLoading,
    error: drawsError,
    refresh: refreshDraws,
  } = useApiPoll<LotteryDrawsEnvelope | LotteryDrawRow[]>(`/api/lottery/draws${baseQuery}`, 0);
  const { data: pairs, loading: pairsLoading, error: pairsError, refresh: refreshPairs } =
    useApiPoll<PairFrequencyResponse>(`/api/lottery/pairs${baseQuery}`, 0);
  const { data: triplets, loading: tripletsLoading, error: tripletsError, refresh: refreshTriplets } =
    useApiPoll<TripletFrequencyResponse>(`/api/lottery/triplets${baseQuery}`, 0);
  const { data: distributions, loading: distributionsLoading, error: distributionsError, refresh: refreshDistributions } =
    useApiPoll<DistributionsResponse>(`/api/lottery/distributions${baseQuery}`, 0);
  const { data: sumRange, loading: sumRangeLoading, error: sumRangeError, refresh: refreshSumRange } =
    useApiPoll<SumRangeResponse>(`/api/lottery/sum-range${baseQuery}`, 0);
  const { data: positional, loading: positionalLoading, error: positionalError, refresh: refreshPositional } =
    useApiPoll<PositionalResponse>(`/api/lottery/positional${baseQuery}`, 0);
  const { data: rolling, loading: rollingLoading, error: rollingError, refresh: refreshRolling } =
    useApiPoll<RollingFrequencyResponse>(`/api/lottery/rolling-frequency${windowQuery}`, 0);
  const { data: pairLift, loading: pairLiftLoading, error: pairLiftError, refresh: refreshPairLift } =
    useApiPoll<PairLiftResponse>(`/api/lottery/pair-lift${baseQuery}`, 0);
  const { data: anomalies, loading: anomaliesLoading, error: anomaliesError, refresh: refreshAnomalies } =
    useApiPoll<AnomaliesResponse>(`/api/lottery/anomalous-draws${anomalyQuery}`, 0);
  const { data: bonusIntel, loading: bonusLoading, error: bonusError, refresh: refreshBonus } =
    useApiPoll<BonusIntelligenceResponse>(`/api/lottery/bonus-intelligence${windowQuery}`, 0);

  const drawRows: LotteryDrawRow[] = useMemo(() => {
    if (!drawsEnvelope) return [];
    if (Array.isArray(drawsEnvelope)) return drawsEnvelope;
    return drawsEnvelope.history || [];
  }, [drawsEnvelope]);

  const frequencyRows = freqRaw?.number_frequency || dashboard?.number_frequency || [];
  const freqData = frequencyRows.map((row) => ({ name: String(row.number), freq: row.count ?? 0 }));
  const mostCommonPreview = (dashboard?.hot_numbers || []).slice(0, config.mainCount).map((row) => row.number);
  const latestDraw = drawRows[0];
  const sumDistribution = distributions?.sum_distribution?.distribution || dashboard?.sum_distribution || [];

  const refreshReadModels = useCallback(() => {
    void refreshDashboard();
    void refreshFrequency();
    void refreshDraws();
    void refreshPairs();
    void refreshTriplets();
    void refreshDistributions();
    void refreshSumRange();
    void refreshPositional();
    void refreshRolling();
    void refreshPairLift();
    void refreshAnomalies();
    void refreshBonus();
  }, [
    refreshAnomalies,
    refreshBonus,
    refreshDashboard,
    refreshDistributions,
    refreshDraws,
    refreshFrequency,
    refreshPairLift,
    refreshPairs,
    refreshPositional,
    refreshRolling,
    refreshSumRange,
    refreshTriplets,
  ]);

  const buildFilters = useCallback(() => {
    const filters: Record<string, unknown> = {};
    const maxRun = optionalInt(maxConsecutiveRun);
    const minTotal = optionalInt(minSum);
    const maxTotal = optionalInt(maxSum);
    const overlapCap = optionalInt(recentOverlapCap);
    if (maxRun !== undefined) filters.max_consecutive_run = maxRun;
    if (oddEvenTarget.trim()) filters.odd_even_target = oddEvenTarget.trim();
    if (lowHighTarget.trim()) filters.low_high_target = lowHighTarget.trim();
    if (minTotal !== undefined) filters.min_sum = minTotal;
    if (maxTotal !== undefined) filters.max_sum = maxTotal;
    if (overlapCap !== undefined) filters.avoid_recent_overlap_above = overlapCap;
    return filters;
  }, [lowHighTarget, maxConsecutiveRun, maxSum, minSum, oddEvenTarget, recentOverlapCap]);

  const postLottery = useCallback(async <T,>(
    endpoint: string,
    payload: Record<string, unknown>,
    setLoading: (value: boolean) => void,
    setError: (value: string | null) => void,
  ): Promise<T | null> => {
    setLoading(true);
    setError(null);
    try {
      return await apiClient.post<T>(endpoint, payload);
    } catch (error) {
      const message = errorMessage(error);
      setError(message);
      showToast(message, 'error');
      return null;
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  const handleImport = useCallback(async () => {
    if (!csvFile) return;
    setImporting(true);
    const formData = new FormData();
    formData.append('file', csvFile);
    formData.append('game', game);
    formData.append('mode', 'append');

    try {
      const res = await fetch('/api/lottery/import', { method: 'POST', body: formData });
      const data = await res.json() as { imported_rows?: number; duplicates_skipped?: number; error?: string };
      if (!res.ok || data.error) {
        showToast(data.error || `HTTP ${res.status}`, 'error');
        return;
      }
      showToast(`Imported ${data.imported_rows ?? 0} draws`, 'success');
      setCsvFile(null);
      refreshReadModels();
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setImporting(false);
    }
  }, [csvFile, game, refreshReadModels, showToast]);

  const handleAddDraw = useCallback(async () => {
    const numbers = parseNumberList(newDraw);
    const validation = validateMainNumbers(numbers, config);
    const bonusValue = optionalInt(optionalBonus);
    const bonusValidation = validateBonusNumber(bonusValue, config);
    if (!drawDate) {
      showToast('Draw date required', 'error');
      return;
    }
    if (validation) {
      showToast(validation, 'error');
      return;
    }
    if (bonusValidation) {
      showToast(bonusValidation, 'error');
      return;
    }

    setAddDrawLoading(true);
    try {
      const body: Record<string, unknown> = { game, draw_date: drawDate, numbers };
      if (config.hasBonus) body.bonus = bonusValue;
      const res = await fetch('/api/lottery/add-draw', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json() as { inserted?: boolean; duplicate?: boolean; error?: string };
      if (!res.ok && res.status !== 409) {
        showToast(data.error || `HTTP ${res.status}`, 'error');
        return;
      }
      if (data.inserted) {
        showToast('Draw added', 'success');
        setNewDraw('');
        setOptionalBonus('');
        refreshReadModels();
      } else if (data.duplicate) {
        showToast('Draw already exists for this date', 'info');
      } else {
        showToast(data.error || 'Add draw failed', 'error');
      }
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setAddDrawLoading(false);
    }
  }, [config, drawDate, game, newDraw, optionalBonus, refreshReadModels, showToast]);

  const handleGenerate = useCallback(async () => {
    const result = await postLottery<LotteryGenerateResponse>(
      '/api/lottery/generate',
      {
        game,
        mode: generatorMode,
        ticket_count: toBoundedInt(ticketCount, 5, 1, 200),
        include_bonus: effectiveIncludeBonus,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        filters: buildFilters(),
      },
      setGenerateLoading,
      setGenerateError,
    );
    if (result?.tickets?.length) {
      setGenerateMeta(result);
      setGeneratedTickets(result.tickets);
      showToast(`Generated ${result.tickets.length} tickets`, 'success');
    }
  }, [buildFilters, effectiveIncludeBonus, endDate, game, generatorMode, postLottery, showToast, startDate, ticketCount]);

  const handleScoreTicket = useCallback(async () => {
    const numbers = parseNumberList(scoreInput);
    const validation = validateMainNumbers(numbers, config);
    const bonusValue = optionalInt(scoreBonus);
    const bonusValidation = effectiveIncludeBonus ? validateBonusNumber(bonusValue, config) : null;
    if (validation) {
      showToast(validation, 'error');
      return;
    }
    if (bonusValidation) {
      showToast(bonusValidation, 'error');
      return;
    }
    const ticket: Record<string, unknown> = { numbers };
    if (effectiveIncludeBonus) ticket.bonus = bonusValue;
    const result = await postLottery<ScoreTicketResponse>(
      '/api/lottery/score-ticket',
      {
        game,
        ticket,
        include_bonus: effectiveIncludeBonus,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
      },
      setScoreLoading,
      setScoreError,
    );
    if (result) setScoredTicket(result);
  }, [config, effectiveIncludeBonus, endDate, game, postLottery, scoreBonus, scoreInput, showToast, startDate]);

  const handleWheel = useCallback(async () => {
    const chosenNumbers = parseNumberList(wheelNumbers);
    if (new Set(chosenNumbers).size !== chosenNumbers.length) {
      showToast('Wheel pool must not contain duplicates', 'error');
      return;
    }
    if (chosenNumbers.length < config.mainCount) {
      showToast(`Wheel pool needs at least ${config.mainCount} numbers`, 'error');
      return;
    }
    const outOfRange = chosenNumbers.filter((number) => number < config.mainMin || number > config.mainMax);
    if (outOfRange.length) {
      showToast(`Wheel numbers must be between ${config.mainMin} and ${config.mainMax}`, 'error');
      return;
    }
    const result = await postLottery<WheelResponse>(
      '/api/lottery/wheel',
      {
        game,
        chosen_numbers: chosenNumbers,
        guarantee_if: toBoundedInt(guaranteeIf, 3, 2, config.mainCount),
      },
      setWheelLoading,
      setWheelError,
    );
    if (result) setWheelResult(result);
  }, [config, game, guaranteeIf, postLottery, showToast, wheelNumbers]);

  const parsePayoutTable = useCallback(() => {
    const trimmed = payoutTable.trim();
    if (!trimmed) return {};
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
      showToast('Payout table must be a JSON object', 'error');
    } catch (error) {
      showToast(`Payout table JSON error: ${errorMessage(error)}`, 'error');
    }
    return null;
  }, [payoutTable, showToast]);

  const handleSimulate = useCallback(async () => {
    const parsedPayout = parsePayoutTable();
    if (parsedPayout === null) return;
    const result = await postLottery<SimulationResponse>(
      '/api/lottery/simulate',
      {
        game,
        mode: generatorMode,
        tickets_per_draw: toBoundedInt(ticketsPerDraw, 3, 1, 100),
        include_bonus: effectiveIncludeBonus,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        filters: buildFilters(),
        ticket_cost: optionalFloat(ticketCost),
        payout_table: parsedPayout,
        seed: optionalInt(seed),
      },
      setSimulationLoading,
      setSimulationError,
    );
    if (result) setSimulation(result);
  }, [
    buildFilters,
    effectiveIncludeBonus,
    endDate,
    game,
    generatorMode,
    parsePayoutTable,
    postLottery,
    seed,
    startDate,
    ticketCost,
    ticketsPerDraw,
  ]);

  const handleCompareModes = useCallback(async () => {
    const parsedPayout = parsePayoutTable();
    if (parsedPayout === null) return;
    const modes = compareModes
      .split(/[,;\s]+/)
      .map((mode) => mode.trim().toLowerCase())
      .filter(Boolean);
    const result = await postLottery<CompareModesResponse>(
      '/api/lottery/compare-modes',
      {
        game,
        modes,
        tickets_per_draw: toBoundedInt(ticketsPerDraw, 3, 1, 100),
        include_bonus: effectiveIncludeBonus,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        filters: buildFilters(),
        ticket_cost: optionalFloat(ticketCost),
        payout_table: parsedPayout,
        seed: optionalInt(seed),
      },
      setCompareLoading,
      setCompareError,
    );
    if (result) setComparison(result);
  }, [
    buildFilters,
    compareModes,
    effectiveIncludeBonus,
    endDate,
    game,
    parsePayoutTable,
    postLottery,
    seed,
    startDate,
    ticketCost,
    ticketsPerDraw,
  ]);

  const handleAi = useCallback(async () => {
    const result = await postLottery<LotteryAiResponse>(
      '/api/lottery/ai-analysis',
      {
        game,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        window: safeWindow,
        z_threshold: safeZThreshold,
      },
      setAiLoading,
      setAiError,
    );
    if (result) setAiAnalysis(result);
  }, [endDate, game, postLottery, safeWindow, safeZThreshold, startDate]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label className="text-[10px] uppercase text-muted-foreground">Game</Label>
          <Select value={game} onValueChange={(value) => setGame(value as LotteryGame)}>
            <SelectTrigger className="h-8 w-[160px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {GAMES.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-[10px] uppercase text-muted-foreground">Start</Label>
          <Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="h-8 w-[140px] text-xs" />
        </div>
        <div className="space-y-1">
          <Label className="text-[10px] uppercase text-muted-foreground">End</Label>
          <Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="h-8 w-[140px] text-xs" />
        </div>
        <div className="space-y-1">
          <Label className="text-[10px] uppercase text-muted-foreground">Limit</Label>
          <Input value={limit} onChange={(event) => setLimit(event.target.value)} className="h-8 w-20 text-xs font-mono" />
        </div>
        <div className="space-y-1">
          <Label className="text-[10px] uppercase text-muted-foreground">Window</Label>
          <Input value={windowSize} onChange={(event) => setWindowSize(event.target.value)} className="h-8 w-20 text-xs font-mono" />
        </div>
        <div className="flex h-8 items-center gap-2 px-2">
          <Checkbox
            id="lottery-include-bonus"
            checked={effectiveIncludeBonus}
            disabled={!config.hasBonus}
            onCheckedChange={(checked) => setIncludeBonus(Boolean(checked))}
          />
          <Label htmlFor="lottery-include-bonus" className="text-xs text-muted-foreground">
            Bonus
          </Label>
        </div>
        <Button size="sm" variant="outline" className="h-8 gap-1 text-xs" onClick={refreshReadModels}>
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="flex h-auto flex-wrap items-center justify-start gap-1 bg-muted/40 p-1">
          <TabsTrigger value="overview" className="text-xs">Overview</TabsTrigger>
          <TabsTrigger value="frequency" className="text-xs">Frequency</TabsTrigger>
          <TabsTrigger value="history" className="text-xs">History</TabsTrigger>
          <TabsTrigger value="analytics" className="text-xs">Analytics</TabsTrigger>
          <TabsTrigger value="tools" className="text-xs">Tools</TabsTrigger>
          <TabsTrigger value="ai" className="text-xs">AI Analysis</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-3 space-y-4">
          {dashError && <ErrorBanner message={dashError} />}
          <div className="grid gap-3 md:grid-cols-4">
            <MetricCard
              label="Total Draws"
              value={dashLoading ? '...' : dashboard?.total_draws ?? 0}
              icon={<History className="h-4 w-4 text-primary" />}
              detail={dashboard?.first_draw_date && dashboard?.last_draw_date ? `${dashboard.first_draw_date} to ${dashboard.last_draw_date}` : undefined}
            />
            <MetricCard
              label="Latest Draw"
              value={latestDraw?.draw_date || 'n/a'}
              icon={<CheckCircle2 className="h-4 w-4 text-emerald-400" />}
              detail={latestDraw?.numbers?.join(', ')}
            />
            <MetricCard
              label="Recommended Sum"
              value={`${formatValue(sumRange?.recommended_min ?? dashboard?.recommended_sum_range?.min, 0)} - ${formatValue(sumRange?.recommended_max ?? dashboard?.recommended_sum_range?.max, 0)}`}
              icon={<Calculator className="h-4 w-4 text-amber-400" />}
              detail={`${formatValue(sumRange?.coverage_pct ?? dashboard?.recommended_sum_range?.coverage_pct, 1)}% coverage`}
            />
            <MetricCard
              label="Entropy"
              value={dashboard?.entropy?.fairness || 'n/a'}
              icon={<Activity className="h-4 w-4 text-cyan-400" />}
              detail={dashboard?.entropy?.per_ball?.entropy_ratio != null ? `ratio ${formatValue(dashboard.entropy.per_ball.entropy_ratio, 4)}` : undefined}
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <TrendingUp className="h-4 w-4 text-primary" />
                  Signal Summary
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {dashLoading ? (
                  <LoadingRows rows={4} />
                ) : (
                  <>
                    <div>
                      <p className="mb-2 text-[10px] uppercase text-muted-foreground">Hot picks</p>
                      <NumberPills numbers={mostCommonPreview} />
                    </div>
                    <div className="grid gap-3 md:grid-cols-2">
                      <div>
                        <p className="mb-2 text-[10px] uppercase text-muted-foreground">Hot numbers</p>
                        <div className="flex flex-wrap gap-1">
                          {(dashboard?.hot_numbers || []).slice(0, 12).map((row) => (
                            <Badge key={row.number} variant="outline" className="bg-emerald-500/10 font-mono text-[10px] text-emerald-400">
                              {row.number} ({row.count ?? 0})
                            </Badge>
                          ))}
                        </div>
                      </div>
                      <div>
                        <p className="mb-2 text-[10px] uppercase text-muted-foreground">Cold numbers</p>
                        <div className="flex flex-wrap gap-1">
                          {(dashboard?.cold_numbers || []).slice(0, 12).map((row) => (
                            <Badge key={row.number} variant="outline" className="bg-sky-500/10 font-mono text-[10px] text-sky-400">
                              {row.number} ({row.count ?? 0})
                            </Badge>
                          ))}
                        </div>
                      </div>
                    </div>
                    <div>
                      <p className="mb-2 text-[10px] uppercase text-muted-foreground">Overdue numbers</p>
                      <div className="flex flex-wrap gap-1">
                        {(dashboard?.overdue_numbers || freqRaw?.overdue || []).slice(0, 15).map((row) => (
                          <Badge key={row.number} variant="outline" className="font-mono text-[10px]">
                            {row.number}: {row.draws_since_seen ?? 'n/a'}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <ListChecks className="h-4 w-4 text-primary" />
                  Shape Checks
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <p className="mb-2 text-[10px] uppercase text-muted-foreground">Odd / even</p>
                  <PatternBadges rows={dashboard?.odd_even_distribution} empty="No odd/even data" />
                </div>
                <div>
                  <p className="mb-2 text-[10px] uppercase text-muted-foreground">Low / high</p>
                  <PatternBadges rows={dashboard?.low_high_distribution} empty="No low/high data" />
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="rounded-md bg-muted/30 p-2">
                    <p className="text-[10px] uppercase text-muted-foreground">Consecutive</p>
                    <p className="font-mono font-bold">{formatValue(dashboard?.consecutive_summary?.draws_with_consecutive, 0)}</p>
                  </div>
                  <div className="rounded-md bg-muted/30 p-2">
                    <p className="text-[10px] uppercase text-muted-foreground">Max streak</p>
                    <p className="font-mono font-bold">{formatValue(dashboard?.consecutive_summary?.max_consecutive_streak, 0)}</p>
                  </div>
                  <div className="rounded-md bg-muted/30 p-2">
                    <p className="text-[10px] uppercase text-muted-foreground">Avg repeat</p>
                    <p className="font-mono font-bold">{formatValue(dashboard?.repeat_from_last_draw_summary?.average_repeat_count, 2)}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="frequency" className="mt-3 space-y-4">
          {freqError && <ErrorBanner message={freqError} />}
          {rollingError && <ErrorBanner message={rollingError} />}
          {bonusError && <ErrorBanner message={bonusError} />}
          <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <BarChart3 className="h-4 w-4 text-primary" />
                  Number Frequency
                </CardTitle>
              </CardHeader>
              <CardContent>
                {freqLoading ? (
                  <Skeleton className="h-[320px] w-full" />
                ) : freqData.length ? (
                  <div className="h-[320px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={freqData}>
                        <XAxis dataKey="name" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: 'hsl(var(--card))',
                            border: '1px solid hsl(var(--border))',
                            borderRadius: '6px',
                            fontSize: '11px',
                          }}
                        />
                        <Bar dataKey="freq" radius={[3, 3, 0, 0]}>
                          {freqData.map((_, index) => (
                            <Cell key={index} fill="hsl(var(--primary))" />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState message="No frequency data" />
                )}
              </CardContent>
            </Card>

            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Search className="h-4 w-4 text-primary" />
                  Overdue Queue
                </CardTitle>
              </CardHeader>
              <CardContent>
                {freqLoading ? (
                  <LoadingRows rows={8} />
                ) : (freqRaw?.overdue || []).length ? (
                  <ScrollArea className="h-[320px] pr-2">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>No.</TableHead>
                          <TableHead className="text-right">Gap</TableHead>
                          <TableHead>Last</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(freqRaw?.overdue || []).slice(0, 30).map((row) => (
                          <TableRow key={row.number}>
                            <TableCell className="font-mono">{row.number}</TableCell>
                            <TableCell className="text-right font-mono">{row.draws_since_seen ?? 'n/a'}</TableCell>
                            <TableCell className="text-xs text-muted-foreground">{row.last_seen_date || 'never'}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </ScrollArea>
                ) : (
                  <EmptyState message="No overdue data" />
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Activity className="h-4 w-4 text-primary" />
                  Rolling Frequency
                </CardTitle>
              </CardHeader>
              <CardContent>
                {rollingLoading ? (
                  <LoadingRows rows={6} />
                ) : (rolling?.numbers || []).length ? (
                  <div className="space-y-3">
                    <div className="grid grid-cols-3 gap-2 text-xs">
                      <div className="rounded-md bg-muted/30 p-2">
                        <p className="text-[10px] uppercase text-muted-foreground">Window</p>
                        <p className="font-mono font-bold">{rolling?.window_used ?? 0}</p>
                      </div>
                      <div className="rounded-md bg-muted/30 p-2">
                        <p className="text-[10px] uppercase text-muted-foreground">Chi2</p>
                        <p className="font-mono font-bold">{formatValue(rolling?.chi2_stat, 3)}</p>
                      </div>
                      <div className="rounded-md bg-muted/30 p-2">
                        <p className="text-[10px] uppercase text-muted-foreground">p-value</p>
                        <p className="font-mono font-bold">{formatValue(rolling?.chi2_p_value, 4)}</p>
                      </div>
                    </div>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>No.</TableHead>
                          <TableHead className="text-right">Recent</TableHead>
                          <TableHead className="text-right">All</TableHead>
                          <TableHead className="text-right">Z</TableHead>
                          <TableHead>Trend</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(rolling?.numbers || []).slice(0, 16).map((row) => (
                          <TableRow key={row.number}>
                            <TableCell className="font-mono">{row.number}</TableCell>
                            <TableCell className="text-right font-mono">{row.recent_count ?? 0}</TableCell>
                            <TableCell className="text-right font-mono">{row.alltime_count ?? 0}</TableCell>
                            <TableCell className="text-right font-mono">{formatValue(row.z_score, 2)}</TableCell>
                            <TableCell>
                              <Badge variant="outline" className="text-[10px]">{row.trend || 'neutral'}</Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                ) : (
                  <EmptyState message="No rolling data" />
                )}
              </CardContent>
            </Card>

            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Target className="h-4 w-4 text-primary" />
                  Bonus Intelligence
                </CardTitle>
              </CardHeader>
              <CardContent>
                {bonusLoading ? (
                  <LoadingRows rows={6} />
                ) : !bonusIntel?.has_bonus ? (
                  <EmptyState message="No bonus ball for this game" />
                ) : (
                  <div className="space-y-3">
                    <div className="flex flex-wrap gap-1.5">
                      {(bonusIntel.top_picks || []).map((row) => (
                        <Badge key={row.number} variant="outline" className="bg-amber-500/10 font-mono text-[10px] text-amber-400">
                          {row.number} score {formatValue(row.score, 2)}
                        </Badge>
                      ))}
                    </div>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>No.</TableHead>
                          <TableHead className="text-right">Recent</TableHead>
                          <TableHead className="text-right">Z</TableHead>
                          <TableHead>Trend</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(bonusIntel.rolling || []).slice(0, 12).map((row) => (
                          <TableRow key={row.number}>
                            <TableCell className="font-mono">{row.number}</TableCell>
                            <TableCell className="text-right font-mono">{row.recent_count ?? 0}</TableCell>
                            <TableCell className="text-right font-mono">{formatValue(row.z_score, 2)}</TableCell>
                            <TableCell>
                              <Badge variant="outline" className="text-[10px]">{row.trend || 'neutral'}</Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="history" className="mt-3 space-y-4">
          {drawsError && <ErrorBanner message={drawsError} />}
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <History className="h-4 w-4 text-primary" />
                Draw History
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[420px]">
                {drawsLoading ? (
                  <LoadingRows rows={8} />
                ) : drawRows.length ? (
                  <div className="space-y-2 pr-2">
                    {drawRows.map((draw, index) => (
                      <div key={`${draw.draw_date}-${index}`} className="grid gap-2 rounded-md bg-muted/30 p-2 md:grid-cols-[110px_1fr_160px] md:items-center">
                        <span className="text-xs text-muted-foreground">{draw.draw_date}</span>
                        <NumberPills numbers={draw.numbers} bonus={draw.bonus} size="sm" />
                        <span className="truncate text-right text-[10px] text-muted-foreground">{draw.source_file || 'manual'}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState message="No draw history" />
                )}
              </ScrollArea>
            </CardContent>
          </Card>

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <Upload className="h-4 w-4 text-primary" />
                Add Draw / Import CSV
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-2 lg:grid-cols-[150px_1fr_110px_90px]">
                <Input type="date" value={drawDate} onChange={(event) => setDrawDate(event.target.value)} className="h-8 text-xs font-mono" />
                <Input
                  value={newDraw}
                  onChange={(event) => setNewDraw(event.target.value)}
                  className="h-8 text-xs font-mono"
                  placeholder={`${config.mainCount} main numbers, ${config.mainMin}-${config.mainMax}`}
                />
                <Input
                  value={optionalBonus}
                  onChange={(event) => setOptionalBonus(event.target.value)}
                  className="h-8 text-xs font-mono"
                  placeholder={config.hasBonus ? `Bonus ${config.bonusMin}-${config.bonusMax}` : 'No bonus'}
                  disabled={!config.hasBonus}
                />
                <Button size="sm" className="h-8 text-xs" onClick={handleAddDraw} disabled={addDrawLoading}>
                  {addDrawLoading ? 'Adding...' : 'Add'}
                </Button>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Input type="file" accept=".csv" className="h-8 max-w-[260px] text-xs" onChange={(event) => setCsvFile(event.target.files?.[0] || null)} />
                <Button size="sm" className="h-8 gap-1 text-xs" onClick={handleImport} disabled={importing || !csvFile}>
                  <Upload className="h-3.5 w-3.5" />
                  {importing ? 'Importing...' : 'Import'}
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="analytics" className="mt-3 space-y-4">
          {(pairsError || tripletsError || distributionsError || sumRangeError || positionalError || pairLiftError || anomaliesError) && (
            <div className="space-y-2">
              {pairsError && <ErrorBanner message={pairsError} />}
              {tripletsError && <ErrorBanner message={tripletsError} />}
              {distributionsError && <ErrorBanner message={distributionsError} />}
              {sumRangeError && <ErrorBanner message={sumRangeError} />}
              {positionalError && <ErrorBanner message={positionalError} />}
              {pairLiftError && <ErrorBanner message={pairLiftError} />}
              {anomaliesError && <ErrorBanner message={anomaliesError} />}
            </div>
          )}

          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <GitCompare className="h-4 w-4 text-primary" />
                  Pair Frequency
                </CardTitle>
              </CardHeader>
              <CardContent>
                {pairsLoading ? (
                  <LoadingRows rows={6} />
                ) : (pairs?.pairs || []).length ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Pair</TableHead>
                        <TableHead className="text-right">Count</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(pairs?.pairs || []).slice(0, 12).map((row) => (
                        <TableRow key={row.pair.join('-')}>
                          <TableCell className="font-mono">{row.pair.join(' - ')}</TableCell>
                          <TableCell className="text-right font-mono">{row.count}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <EmptyState message="No pair data" />
                )}
              </CardContent>
            </Card>

            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Table2 className="h-4 w-4 text-primary" />
                  Triplet Frequency
                </CardTitle>
              </CardHeader>
              <CardContent>
                {tripletsLoading ? (
                  <LoadingRows rows={6} />
                ) : (triplets?.triplets || []).length ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Triplet</TableHead>
                        <TableHead className="text-right">Count</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(triplets?.triplets || []).slice(0, 12).map((row) => (
                        <TableRow key={row.triplet.join('-')}>
                          <TableCell className="font-mono">{row.triplet.join(' - ')}</TableCell>
                          <TableCell className="text-right font-mono">{row.count}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <EmptyState message="No triplet data" />
                )}
              </CardContent>
            </Card>

            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <TrendingUp className="h-4 w-4 text-primary" />
                  Pair Lift
                </CardTitle>
              </CardHeader>
              <CardContent>
                {pairLiftLoading ? (
                  <LoadingRows rows={6} />
                ) : (pairLift?.pairs || []).length ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Pair</TableHead>
                        <TableHead className="text-right">Lift</TableHead>
                        <TableHead className="text-right">Observed</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(pairLift?.pairs || []).slice(0, 10).map((row) => (
                        <TableRow key={row.pair.join('-')}>
                          <TableCell className="font-mono">{row.pair.join(' - ')}</TableCell>
                          <TableCell className="text-right font-mono">{formatValue(row.lift, 2)}</TableCell>
                          <TableCell className="text-right font-mono">{row.observed}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <EmptyState message="No lift data" />
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1fr_1fr_1fr]">
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Calculator className="h-4 w-4 text-primary" />
                  Sum Range
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {sumRangeLoading ? (
                  <LoadingRows rows={4} />
                ) : (
                  <>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded-md bg-muted/30 p-2">
                        <p className="text-[10px] uppercase text-muted-foreground">Recommended</p>
                        <p className="font-mono font-bold">{formatValue(sumRange?.recommended_min, 0)} - {formatValue(sumRange?.recommended_max, 0)}</p>
                      </div>
                      <div className="rounded-md bg-muted/30 p-2">
                        <p className="text-[10px] uppercase text-muted-foreground">Observed</p>
                        <p className="font-mono font-bold">{formatValue(sumRange?.min_sum, 0)} - {formatValue(sumRange?.max_sum, 0)}</p>
                      </div>
                    </div>
                    <ScrollArea className="h-[180px]">
                      <div className="space-y-1 pr-2">
                        {sumDistribution.slice(0, 28).map((row) => (
                          <div key={row.sum} className="flex items-center justify-between rounded-md bg-muted/30 px-2 py-1 text-xs">
                            <span className="font-mono">{row.sum}</span>
                            <span className="font-mono">{row.count}</span>
                          </div>
                        ))}
                      </div>
                    </ScrollArea>
                  </>
                )}
              </CardContent>
            </Card>

            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Table2 className="h-4 w-4 text-primary" />
                  Positional Bands
                </CardTitle>
              </CardHeader>
              <CardContent>
                {positionalLoading ? (
                  <LoadingRows rows={6} />
                ) : (positional?.positions || []).length ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Pos</TableHead>
                        <TableHead className="text-right">P25</TableHead>
                        <TableHead className="text-right">Median</TableHead>
                        <TableHead className="text-right">P75</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(positional?.positions || []).map((row) => (
                        <TableRow key={row.position}>
                          <TableCell className="font-mono">{row.position}</TableCell>
                          <TableCell className="text-right font-mono">{formatValue(row.p25, 1)}</TableCell>
                          <TableCell className="text-right font-mono">{formatValue(row.median, 1)}</TableCell>
                          <TableCell className="text-right font-mono">{formatValue(row.p75, 1)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <EmptyState message="No positional data" />
                )}
              </CardContent>
            </Card>

            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <AlertTriangle className="h-4 w-4 text-primary" />
                  Anomalous Draws
                </CardTitle>
              </CardHeader>
              <CardContent>
                {anomaliesLoading ? (
                  <LoadingRows rows={6} />
                ) : (anomalies?.anomalous_draws || []).length ? (
                  <ScrollArea className="h-[260px]">
                    <div className="space-y-2 pr-2">
                      {(anomalies?.anomalous_draws || []).slice(0, 20).map((draw) => (
                        <div key={draw.draw_date} className="rounded-md bg-muted/30 p-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs text-muted-foreground">{draw.draw_date}</span>
                            <Badge variant="outline" className="text-[10px]">{draw.flags?.join(', ') || 'flagged'}</Badge>
                          </div>
                          <div className="mt-2">
                            <NumberPills numbers={draw.numbers} bonus={draw.bonus} size="sm" />
                          </div>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                ) : (
                  <EmptyState message="No anomalies at this threshold" />
                )}
              </CardContent>
            </Card>
          </div>

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <ListChecks className="h-4 w-4 text-primary" />
                Distribution Profiles
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-3">
              <div>
                <p className="mb-2 text-[10px] uppercase text-muted-foreground">Odd / even</p>
                <PatternBadges rows={distributions?.odd_even_distribution?.patterns} empty="No odd/even data" />
              </div>
              <div>
                <p className="mb-2 text-[10px] uppercase text-muted-foreground">Low / high</p>
                <PatternBadges rows={distributions?.low_high_distribution?.patterns} empty="No low/high data" />
              </div>
              <div>
                <p className="mb-2 text-[10px] uppercase text-muted-foreground">Repeat count</p>
                <div className="flex flex-wrap gap-1.5">
                  {(distributions?.repeat_from_last_draw?.distribution || []).map((row) => (
                    <Badge key={row.repeat_count} variant="outline" className="font-mono text-[10px]">
                      {row.repeat_count}: {row.count}
                    </Badge>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="tools" className="mt-3 space-y-4">
          <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Dices className="h-4 w-4 text-primary" />
                  Generate Tickets
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {generateError && <ErrorBanner message={generateError} />}
                <div className="grid gap-2 md:grid-cols-[170px_90px_1fr]">
                  <Select value={generatorMode} onValueChange={(value) => setGeneratorMode(value as GeneratorMode)}>
                    <SelectTrigger className="h-8 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {GENERATOR_MODES.map((mode) => (
                        <SelectItem key={mode.value} value={mode.value}>{mode.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input value={ticketCount} onChange={(event) => setTicketCount(event.target.value)} className="h-8 text-xs font-mono" placeholder="Tickets" />
                  <Button size="sm" className="h-8 gap-1 text-xs" onClick={handleGenerate} disabled={generateLoading}>
                    <Wand2 className="h-3.5 w-3.5" />
                    {generateLoading ? 'Generating...' : 'Generate'}
                  </Button>
                </div>
                <div className="grid gap-2 md:grid-cols-5">
                  <Input value={maxConsecutiveRun} onChange={(event) => setMaxConsecutiveRun(event.target.value)} className="h-8 text-xs font-mono" placeholder="Max run" />
                  <Input value={oddEvenTarget} onChange={(event) => setOddEvenTarget(event.target.value)} className="h-8 text-xs font-mono" placeholder="Odd:even" />
                  <Input value={lowHighTarget} onChange={(event) => setLowHighTarget(event.target.value)} className="h-8 text-xs font-mono" placeholder="Low:high" />
                  <Input value={minSum} onChange={(event) => setMinSum(event.target.value)} className="h-8 text-xs font-mono" placeholder="Min sum" />
                  <Input value={maxSum} onChange={(event) => setMaxSum(event.target.value)} className="h-8 text-xs font-mono" placeholder="Max sum" />
                </div>
                <Input
                  value={recentOverlapCap}
                  onChange={(event) => setRecentOverlapCap(event.target.value)}
                  className="h-8 text-xs font-mono"
                  placeholder="Avoid recent overlap above"
                />
                {generatedTickets.length ? (
                  <div className="space-y-2">
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="outline" className="text-[10px]">mode {generateMeta?.mode || generatorMode}</Badge>
                      <Badge variant="outline" className="text-[10px]">fallbacks {generateMeta?.fallback_tickets_used ?? 0}</Badge>
                    </div>
                    {generatedTickets.map((ticket, index) => (
                      <div key={`${ticket.numbers.join('-')}-${index}`} className="rounded-md bg-muted/30 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <NumberPills numbers={ticket.numbers} bonus={ticket.bonus} />
                          <div className="flex flex-wrap gap-1">
                            {(ticket.labels || []).slice(0, 4).map((label) => (
                              <Badge key={label} variant="outline" className="text-[10px]">{label}</Badge>
                            ))}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState message="No generated tickets yet" />
                )}
              </CardContent>
            </Card>

            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Calculator className="h-4 w-4 text-primary" />
                  Score Ticket
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {scoreError && <ErrorBanner message={scoreError} />}
                <div className="grid gap-2 md:grid-cols-[1fr_100px_110px]">
                  <Input value={scoreInput} onChange={(event) => setScoreInput(event.target.value)} className="h-8 text-xs font-mono" placeholder={`${config.mainCount} main numbers`} />
                  <Input value={scoreBonus} onChange={(event) => setScoreBonus(event.target.value)} className="h-8 text-xs font-mono" placeholder="Bonus" disabled={!effectiveIncludeBonus} />
                  <Button size="sm" className="h-8 text-xs" onClick={handleScoreTicket} disabled={scoreLoading}>
                    {scoreLoading ? 'Scoring...' : 'Score'}
                  </Button>
                </div>
                {scoredTicket ? (
                  <div className="rounded-md bg-muted/30 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <NumberPills numbers={scoredTicket.numbers} bonus={scoredTicket.bonus} />
                      <div className="text-right text-xs">
                        <p className="font-mono font-bold">{formatValue(scoredTicket.score, 2)}</p>
                        <p className="text-muted-foreground">score</p>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-1">
                      {(scoredTicket.labels || []).map((label) => (
                        <Badge key={label} variant="outline" className="text-[10px]">{label}</Badge>
                      ))}
                    </div>
                  </div>
                ) : (
                  <EmptyState message="No scored ticket yet" />
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Target className="h-4 w-4 text-primary" />
                  Wheel
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {wheelError && <ErrorBanner message={wheelError} />}
                <div className="grid gap-2 md:grid-cols-[1fr_100px_100px]">
                  <Input value={wheelNumbers} onChange={(event) => setWheelNumbers(event.target.value)} className="h-8 text-xs font-mono" placeholder="Pool numbers" />
                  <Input value={guaranteeIf} onChange={(event) => setGuaranteeIf(event.target.value)} className="h-8 text-xs font-mono" placeholder="Guarantee" />
                  <Button size="sm" className="h-8 text-xs" onClick={handleWheel} disabled={wheelLoading}>
                    {wheelLoading ? 'Running...' : 'Wheel'}
                  </Button>
                </div>
                {wheelResult ? (
                  <div className="space-y-2">
                    <div className="flex flex-wrap gap-2">
                      <Badge variant={wheelResult.coverage_verified ? 'default' : 'destructive'} className="text-[10px]">
                        coverage {String(Boolean(wheelResult.coverage_verified))}
                      </Badge>
                      <Badge variant="outline" className="text-[10px]">{wheelResult.ticket_count ?? 0} tickets</Badge>
                    </div>
                    <ScrollArea className="h-[220px]">
                      <div className="space-y-2 pr-2">
                        {(wheelResult.tickets || []).map((ticket, index) => (
                          <div key={`${ticket.join('-')}-${index}`} className="rounded-md bg-muted/30 p-2">
                            <NumberPills numbers={ticket} size="sm" />
                          </div>
                        ))}
                      </div>
                    </ScrollArea>
                  </div>
                ) : (
                  <EmptyState message="No wheel generated yet" />
                )}
              </CardContent>
            </Card>

            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <Activity className="h-4 w-4 text-primary" />
                  Simulation
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {simulationError && <ErrorBanner message={simulationError} />}
                <div className="grid gap-2 md:grid-cols-4">
                  <Input value={ticketsPerDraw} onChange={(event) => setTicketsPerDraw(event.target.value)} className="h-8 text-xs font-mono" placeholder="Tickets/draw" />
                  <Input value={ticketCost} onChange={(event) => setTicketCost(event.target.value)} className="h-8 text-xs font-mono" placeholder="Cost" />
                  <Input value={seed} onChange={(event) => setSeed(event.target.value)} className="h-8 text-xs font-mono" placeholder="Seed" />
                  <Button size="sm" className="h-8 text-xs" onClick={handleSimulate} disabled={simulationLoading}>
                    {simulationLoading ? 'Running...' : 'Simulate'}
                  </Button>
                </div>
                <Textarea value={payoutTable} onChange={(event) => setPayoutTable(event.target.value)} className="min-h-[130px] font-mono text-xs" />
                {simulation ? (
                  <div className="grid gap-2 md:grid-cols-4">
                    <MetricCard label="Draws" value={simulation.draws_simulated ?? 0} />
                    <MetricCard label="Tickets" value={simulation.tickets_generated ?? 0} />
                    <MetricCard label="ROI" value={simulation.roi == null ? 'n/a' : formatPercent(simulation.roi)} />
                    <MetricCard label="Avg Hit" value={formatValue(simulation.average_hits_per_draw, 3)} />
                  </div>
                ) : (
                  <EmptyState message="No simulation run yet" />
                )}
              </CardContent>
            </Card>
          </div>

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <GitCompare className="h-4 w-4 text-primary" />
                Compare Modes
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {compareError && <ErrorBanner message={compareError} />}
              <div className="grid gap-2 md:grid-cols-[1fr_130px]">
                <Input value={compareModes} onChange={(event) => setCompareModes(event.target.value)} className="h-8 text-xs font-mono" />
                <Button size="sm" className="h-8 text-xs" onClick={handleCompareModes} disabled={compareLoading}>
                  {compareLoading ? 'Comparing...' : 'Compare'}
                </Button>
              </div>
              {comparison?.modes?.length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Mode</TableHead>
                      <TableHead className="text-right">Draws</TableHead>
                      <TableHead className="text-right">Tickets</TableHead>
                      <TableHead className="text-right">ROI</TableHead>
                      <TableHead className="text-right">Avg hit</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {comparison.modes.map((row) => (
                      <TableRow key={row.mode}>
                        <TableCell className="font-mono">{row.mode}</TableCell>
                        <TableCell className="text-right font-mono">{row.draws_simulated ?? 0}</TableCell>
                        <TableCell className="text-right font-mono">{row.tickets_generated ?? 0}</TableCell>
                        <TableCell className="text-right font-mono">{row.roi == null ? 'n/a' : formatPercent(row.roi)}</TableCell>
                        <TableCell className="text-right font-mono">{formatValue(row.average_hits_per_draw, 3)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <EmptyState message="No mode comparison yet" />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="ai" className="mt-3">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <Brain className="h-4 w-4 text-primary" />
                AI Pattern Analysis
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {aiError && <ErrorBanner message={aiError} />}
              <div className="flex flex-wrap items-center gap-2">
                <Input value={zThreshold} onChange={(event) => setZThreshold(event.target.value)} className="h-8 w-28 text-xs font-mono" placeholder="Z" />
                <Button size="sm" className="h-8 gap-1 text-xs" onClick={handleAi} disabled={aiLoading}>
                  <Sparkles className="h-3.5 w-3.5" />
                  {aiLoading ? 'Analyzing...' : 'Run AI Analysis'}
                </Button>
                {aiAnalysis?.model && <Badge variant="outline" className="text-[10px]">{aiAnalysis.model}</Badge>}
              </div>

              {aiAnalysis ? (
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-3">
                    <div className="rounded-md bg-muted/30 p-3">
                      <p className="mb-2 text-[10px] uppercase text-muted-foreground">Recommended pool</p>
                      <NumberPills numbers={aiAnalysis.recommended_pool} size="sm" />
                    </div>
                    <div className="rounded-md bg-muted/30 p-3">
                      <p className="mb-2 text-[10px] uppercase text-muted-foreground">Avoid</p>
                      <NumberPills numbers={aiAnalysis.avoid_numbers} size="sm" />
                    </div>
                    <div className="rounded-md bg-muted/30 p-3">
                      <p className="mb-2 text-[10px] uppercase text-muted-foreground">Mode / Confidence</p>
                      <p className="font-mono text-sm font-bold">{aiAnalysis.generator_mode || 'n/a'}</p>
                      <p className="text-xs text-muted-foreground">{aiAnalysis.confidence || 'n/a'}</p>
                    </div>
                  </div>
                  <div className="max-h-[520px] overflow-y-auto rounded-md bg-muted/30 p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap">
                    {aiAnalysis.raw_analysis_text || JSON.stringify(aiAnalysis.analysis || aiAnalysis, null, 2)}
                  </div>
                </div>
              ) : (
                <EmptyState message="No AI analysis yet" />
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
