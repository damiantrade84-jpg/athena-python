import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import CompactSuggestedWatchStatus from '@/components/athena/CompactSuggestedWatchStatus';
import { ErrorBanner, RefreshButton } from '@/components/shared';
import { useStore } from '@/hooks/useStore';
import { useLivePrices } from '@/hooks/useLivePrices';
import apiClient from '@/lib/apiClient';
import { fmtPrice } from '@/lib/athenaFormat';
import { cn, fmtNum, toNum } from '@/lib/utils';
import {
  ArrowLeftRight,
  Bot,
  Eye,
  Flag,
  Layers,
  Lock,
  Radar,
  RefreshCw,
  Unlock,
} from 'lucide-react';
import type {
  EngineBHotBenchCardState,
  EngineBHotlistAlternate,
  EngineBHotlistCandidate,
  EngineBHotlistResponse,
  EngineBHotlistSlot,
  LdSnapshot,
  LdSymbolRow,
  SuggestedTradePlan,
  SuggestedTradeWatch,
} from '@/types/athena';

const GROUPS = [
  { key: 'forex', label: 'Forex' },
  { key: 'crypto', label: 'Crypto' },
  { key: 'commodity', label: 'Commodity' },
  { key: 'index', label: 'Index / Stock / ETF' },
] as const;

const HOTLIST_POLL_MS = 45_000;
const SNAPSHOT_POLL_MS = 10_000;
const WATCH_POLL_MS = 15_000;

function symbolKey(value: unknown): string {
  return String(value ?? '')
    .trim()
    .toUpperCase()
    .replace(/=X$/, '')
    .replace(/[^A-Z0-9]/g, '');
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() !== '') {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

/** Render a price, or an explicit fallback ("missing"/"N/A") when unavailable —
 *  never 0.00, which reads like a real (zero) level when none exists. */
function priceOrFallback(value: unknown, display: string, type: string, fallback: string): string {
  const n = firstNumber(value);
  return n == null ? fallback : fmtPrice(n, display, type);
}

function directionForRow(row: LdSymbolRow | null): 'LONG' | 'SHORT' | null {
  const raw = String(row?.engineB?.direction || row?.engineA?.direction || '').trim().toUpperCase();
  if (raw === 'LONG' || raw === 'BUY') return 'LONG';
  if (raw === 'SHORT' || raw === 'SELL') return 'SHORT';
  return null;
}

function matchSnapshotRow(rows: LdSymbolRow[], candidate: EngineBHotlistCandidate | null): LdSymbolRow | null {
  if (!candidate) return null;
  const candidateKeys = new Set([symbolKey(candidate.symbol), symbolKey(candidate.display)]);
  return rows.find((row) => {
    const rowKeys = [symbolKey(row.symbol), symbolKey((row as { display?: string }).display)];
    return rowKeys.some((key) => candidateKeys.has(key));
  }) || null;
}

function relevantWatches(watches: SuggestedTradeWatch[], candidate: EngineBHotlistCandidate | null): SuggestedTradeWatch[] {
  if (!candidate) return [];
  const key = symbolKey(candidate.symbol || candidate.display);
  const preferred = watches.filter((watch) => {
    return symbolKey(watch.symbol || watch.display) === key
      && (watch.source === 'engine_b_hotbench' || watch.source === 'engine_b_candidate');
  });
  const pool = preferred.length ? preferred : watches.filter((watch) => symbolKey(watch.symbol || watch.display) === key);
  return [...pool].sort((left, right) => {
    const l = Date.parse(String(left.created_at || left.createdAt || '')) || 0;
    const r = Date.parse(String(right.created_at || right.createdAt || '')) || 0;
    return r - l;
  });
}

function actionableZone(row: LdSymbolRow | null, direction: 'LONG' | 'SHORT' | null) {
  if (!row || !direction) return null;
  const zone = direction === 'LONG' ? row.engineB.nearestSupportZone : row.engineB.nearestResistanceZone;
  const lower = firstNumber(zone?.lower);
  const upper = firstNumber(zone?.upper);
  if (lower == null || upper == null) return null;
  return {
    lower: Math.min(lower, upper),
    upper: Math.max(lower, upper),
    type: zone?.type || null,
  };
}

function priceInsideZone(price: number | null, zone: { lower: number; upper: number } | null): boolean {
  if (price == null || !zone) return false;
  return price >= zone.lower && price <= zone.upper;
}

function buildWatchPlan(
  candidate: EngineBHotlistCandidate | null,
  row: LdSymbolRow | null,
  timeframe: string,
): { plan: SuggestedTradePlan | null; reason: string } {
  if (!candidate || !row) {
    return { plan: null, reason: 'Prime Engine B first' };
  }
  const direction = directionForRow(row);
  if (!direction) {
    return { plan: null, reason: 'Direction missing' };
  }
  const zone = actionableZone(row, direction);
  if (!zone) {
    return { plan: null, reason: 'No Engine B zone available' };
  }
  const plan: SuggestedTradePlan = {
    armable: true,
    source: 'engine_b_hotbench',
    symbol: candidate.symbol,
    direction,
    action: 'WAIT_FOR_ZONE',
    triggerType: 'PULLBACK_TO_ZONE',
    zoneLow: zone.lower,
    zoneHigh: zone.upper,
    contextTf: row.engineB.sourceTimeframes?.zone_tf || row.timeframe || timeframe,
    entryTf: row.engineB.sourceTimeframes?.entry_tf || row.timeframe || timeframe,
    executionTf: row.engineB.sourceTimeframes?.trigger_tf || row.timeframe || timeframe,
    reason: `Wait for ${direction === 'LONG' ? 'support' : 'resistance'} zone retest`,
  };
  const sl = firstNumber(row.levels?.sl, row.engineB.sl);
  if (direction === 'LONG') {
    plan.invalidateBelow = firstNumber(sl, zone.lower) ?? undefined;
  } else {
    plan.invalidateAbove = firstNumber(sl, zone.upper) ?? undefined;
  }
  return { plan, reason: 'Zone available' };
}

function deriveCardState(args: {
  candidate: EngineBHotlistCandidate | null;
  row: LdSymbolRow | null;
  watch: SuggestedTradeWatch | null;
  outcome: { status: 'AI_ENTRY_NOW' | 'AI_WAIT' | 'AI_SKIP' } | null;
  livePrice?: number;
}): EngineBHotBenchCardState {
  const { candidate, row, watch, outcome, livePrice } = args;
  if (!candidate) {
    return { status: 'MISSING', reason: 'Hotlist did not return a winner for this group' };
  }
  if (watch?.status === 'INVALIDATED') {
    return { status: 'INVALIDATED', reason: watch.notes || 'Watch invalidated', watchStatus: watch.status };
  }
  if (watch?.status === 'EXPIRED') {
    return { status: 'EXPIRED', reason: watch.notes || 'Watch expired', watchStatus: watch.status };
  }
  if (outcome) {
    return { status: outcome.status, reason: 'Fresh Engine B chart review stored', reviewStatus: outcome.status };
  }
  if (watch?.status === 'READY_FOR_REVIEW') {
    return { status: 'READY_FOR_AI_REVIEW', reason: watch.notes || 'Watch reached review zone', watchStatus: watch.status };
  }
  if (watch && ['FLAGGED', 'WATCHING', 'LEVEL_REACHED'].includes(String(watch.status || '').toUpperCase())) {
    return { status: 'WATCH_ARMED', reason: watch.notes || 'Alert-only watch armed', watchStatus: watch.status };
  }
  if (!row) {
    return { status: 'SCOUTING', reason: candidate.reason };
  }
  const direction = directionForRow(row);
  const zone = actionableZone(row, direction);
  const latestPrice = firstNumber(livePrice, row.latest_price, candidate.latest_price);
  if (priceInsideZone(latestPrice, zone)) {
    return { status: 'NEAR_ZONE', reason: 'Live price is inside the current Engine B zone' };
  }
  if (row.engineB.confidencePassed || row.engineB.structuralVerdict === 'CLEAR') {
    return { status: 'ENGINE_B_CLEAR', reason: row.engineB.structuralVerdict || 'Engine B confidence gate passed' };
  }
  const hasPrimeData = Boolean(
    row.engineB.structuralVerdict
      || row.engineB.direction
      || row.engineB.score != null
      || row.engineB.entry != null
      || row.levels?.source === 'engineB',
  );
  if (hasPrimeData) {
    return { status: 'PRIMED', reason: 'Engine B snapshot present for this symbol' };
  }
  return { status: 'SCOUTING', reason: candidate.reason };
}

function statusTone(status: EngineBHotBenchCardState['status']): string {
  if (status === 'AI_ENTRY_NOW' || status === 'READY_FOR_AI_REVIEW' || status === 'ENGINE_B_CLEAR') {
    return 'bg-long/15 text-long border-long/30';
  }
  if (status === 'AI_SKIP' || status === 'INVALIDATED' || status === 'EXPIRED' || status === 'MISSING') {
    return 'bg-short/15 text-short border-short/30';
  }
  if (status === 'WATCH_ARMED' || status === 'NEAR_ZONE' || status === 'AI_WAIT') {
    return 'bg-warning/15 text-warning border-warning/30';
  }
  return 'bg-muted/40 text-muted-foreground border-border/40';
}

function openChartIntentPayload(candidate: EngineBHotlistCandidate, row: LdSymbolRow | null, timeframe: string) {
  const direction = directionForRow(row);
  return {
    id: `tv-engine-b-${candidate.symbol}-${Date.now()}`,
    source: 'engine_b' as const,
    symbol: candidate.symbol,
    display: candidate.display,
    signal: {
      symbol: candidate.symbol,
      pair: candidate.symbol,
      display: candidate.display,
      type: candidate.asset_type,
      direction,
      engine: 'engine_b',
      engine_source: 'engine_b',
      timeframe: row?.engineB.sourceTimeframes?.entry_tf || row?.timeframe || timeframe,
      entry: firstNumber(row?.levels?.entry, row?.engineB.entry),
      sl: firstNumber(row?.levels?.sl, row?.engineB.sl),
      tp1: firstNumber(row?.levels?.tp1, row?.levels?.tp, row?.engineB.tp),
      rr1: firstNumber(row?.levels?.rr, row?.engineB.rr),
      structuralVerdict: row?.engineB.structuralVerdict || null,
      score: firstNumber(row?.engineB.score, candidate.strength_score),
      threshold: firstNumber(row?.engineB.threshold),
    },
    preferredTf: row?.engineB.sourceTimeframes?.entry_tf || row?.timeframe || timeframe,
    createdAt: new Date().toISOString(),
  };
}

function Sparkline({
  candles,
}: {
  candles: Array<{ time?: string; open?: number; high?: number; low?: number; close?: number; volume?: number }> | undefined;
}) {
  const closes = (candles || [])
    .map((candle) => toNum(candle.close))
    .filter((value): value is number => Number.isFinite(value));
  if (closes.length < 2) {
    return (
      <div className="h-16 rounded-md border border-dashed border-border/60 bg-muted/20 px-2 text-[10px] text-muted-foreground flex items-center">
        Chart missing
      </div>
    );
  }
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = Math.max(max - min, 1e-9);
  const points = closes.map((value, index) => {
    const x = (index / Math.max(1, closes.length - 1)) * 100;
    const y = 100 - ((value - min) / range) * 100;
    return `${x},${y}`;
  }).join(' ');
  const rising = closes[closes.length - 1] >= closes[0];
  return (
    <div className="h-16 rounded-md border border-border/60 bg-muted/20 p-1">
      <svg viewBox="0 0 100 100" className="h-full w-full">
        <polyline
          fill="none"
          stroke={rising ? 'hsl(var(--long))' : 'hsl(var(--short))'}
          strokeWidth="3"
          strokeLinejoin="round"
          strokeLinecap="round"
          points={points}
        />
      </svg>
    </div>
  );
}

export default function EngineBHotBenchPanel() {
  const { showToast, setActivePanel, setTvChartIntent, chartReviewOutcomes } = useStore();
  const { priceFor, ageSecFor, sourceFor } = useLivePrices();
  const [timeframe, setTimeframe] = useState<'H1' | 'M15'>('H1');
  const [hotlist, setHotlist] = useState<EngineBHotlistResponse | null>(null);
  const [snapshot, setSnapshot] = useState<LdSnapshot | null>(null);
  const [watches, setWatches] = useState<SuggestedTradeWatch[]>([]);
  const [hotlistLoading, setHotlistLoading] = useState(false);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [watchesLoading, setWatchesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoReviewReadyOnly, setAutoReviewReadyOnly] = useState(false);
  const [primingKey, setPrimingKey] = useState<string | null>(null);
  const [armingKey, setArmingKey] = useState<string | null>(null);
  const autoOpenedWatchIdsRef = useRef<Set<string>>(new Set());

  const refreshHotlist = useCallback(async () => {
    setHotlistLoading(true);
    try {
      const params = new URLSearchParams({
        groups: GROUPS.map((group) => group.key).join(','),
        topPerGroup: '1',
        timeframe,
        includeCandidates: 'true',
      });
      const res = await apiClient.get<EngineBHotlistResponse>(`/api/engine-b/hotlist?${params.toString()}`);
      setHotlist(res);
      setError(null);
      return res;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load Engine B hotlist';
      setError(message);
      return null;
    } finally {
      setHotlistLoading(false);
    }
  }, [timeframe]);

  const winnerCandidates = useMemo(() => {
    return GROUPS.map((group) => hotlist?.groups?.[group.key]?.winner ?? null);
  }, [hotlist]);

  const [actionKey, setActionKey] = useState<string | null>(null);

  const refreshSnapshot = useCallback(async (sourceHotlist?: EngineBHotlistResponse | null) => {
    const activeHotlist = sourceHotlist ?? hotlist;
    const displays = GROUPS
      .map((group) => activeHotlist?.groups?.[group.key]?.winner?.display)
      .filter((value): value is string => Boolean(value));
    if (displays.length === 0) {
      setSnapshot(null);
      return;
    }
    setSnapshotLoading(true);
    try {
      const params = new URLSearchParams({
        symbols: displays.join(','),
        timeframe: timeframe === 'M15' ? 'H1' : timeframe,
      });
      const res = await apiClient.get<LdSnapshot>(`/api/live-dashboard/snapshot?${params.toString()}`);
      setSnapshot(res);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load Engine B snapshot';
      setError(message);
    } finally {
      setSnapshotLoading(false);
    }
  }, [hotlist, timeframe]);

  const refreshWatches = useCallback(async () => {
    setWatchesLoading(true);
    try {
      const res = await apiClient.get<{ watches?: SuggestedTradeWatch[] }>('/api/suggested-trades');
      setWatches(Array.isArray(res.watches) ? res.watches : []);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load suggested watches';
      setError(message);
    } finally {
      setWatchesLoading(false);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    const latestHotlist = await refreshHotlist();
    await Promise.all([refreshSnapshot(latestHotlist), refreshWatches()]);
  }, [refreshHotlist, refreshSnapshot, refreshWatches]);

  // Sticky-slot actions (lock / unlock / manual replace) are server-side: send
  // the action as a query param, then re-render from the returned slot state.
  const applyHotlistAction = useCallback(async (key: string, action: Record<string, string>) => {
    setActionKey(key);
    try {
      const params = new URLSearchParams({
        groups: GROUPS.map((group) => group.key).join(','),
        topPerGroup: '1',
        timeframe,
        includeCandidates: 'true',
        ...action,
      });
      const res = await apiClient.get<EngineBHotlistResponse>(`/api/engine-b/hotlist?${params.toString()}`);
      setHotlist(res);
      setError(null);
      await refreshSnapshot(res);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Hotlist action failed', 'error');
    } finally {
      setActionKey(null);
    }
  }, [refreshSnapshot, showToast, timeframe]);

  const toggleLock = useCallback((slot: EngineBHotlistSlot | undefined, group: string) => {
    if (!slot?.symbol) return;
    if (slot.locked) {
      void applyHotlistAction(`lock-${group}`, { unlock: group });
      showToast(`${slot.display || slot.symbol} unlocked`, 'info');
    } else {
      void applyHotlistAction(`lock-${group}`, { lock: `${group}:${slot.symbol}` });
      showToast(`${slot.display || slot.symbol} locked — refreshes update price/score only`, 'success');
    }
  }, [applyHotlistAction, showToast]);

  const replaceSlot = useCallback((group: string, symbol: string, label: string) => {
    void applyHotlistAction(`replace-${group}`, { select: `${group}:${symbol}` });
    showToast(`Switched ${group} card to ${label}`, 'info');
  }, [applyHotlistAction, showToast]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshHotlist().then((res) => {
        void refreshSnapshot(res);
      });
    }, HOTLIST_POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshHotlist, refreshSnapshot]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshSnapshot();
    }, SNAPSHOT_POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshSnapshot]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshWatches();
    }, WATCH_POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshWatches]);

  const cards = useMemo(() => {
    const rows = snapshot?.symbols || [];
    return GROUPS.map((group) => {
      const groupPayload = hotlist?.groups?.[group.key];
      const candidate = groupPayload?.winner ?? null;
      const slot = groupPayload?.selected;
      const alternates = groupPayload?.alternates ?? [];
      const row = matchSnapshotRow(rows, candidate);
      const symbolWatches = relevantWatches(watches, candidate);
      const primaryWatch = symbolWatches[0] || null;
      const outcome = candidate ? chartReviewOutcomes[symbolKey(candidate.symbol)] ?? null : null;
      const livePrice = candidate ? priceFor(candidate) : undefined;
      const livePriceAgeSec = candidate ? ageSecFor(candidate) : undefined;
      const livePriceSource = candidate ? sourceFor(candidate) : undefined;
      const watchPlan = buildWatchPlan(candidate, row, timeframe);
      const state = deriveCardState({
        candidate,
        row,
        watch: primaryWatch,
        outcome,
        livePrice,
      });
      return {
        group,
        candidate,
        slot,
        alternates,
        row,
        symbolWatches,
        primaryWatch,
        watchPlan,
        state,
        livePrice,
        livePriceAgeSec,
        livePriceSource,
      };
    });
  }, [ageSecFor, chartReviewOutcomes, hotlist, priceFor, snapshot?.symbols, sourceFor, timeframe, watches]);

  const primeSymbols = useCallback(async (symbols: string[], key: string) => {
    if (symbols.length === 0) {
      showToast('No hotlist symbols to prime', 'info');
      return;
    }
    setPrimingKey(key);
    try {
      const res = await apiClient.postJson('/api/scan-naked', {
        symbols: symbols.join(','),
        style: 'auto',
      }) as { signals?: Array<Record<string, unknown>> };
      const count = Array.isArray(res?.signals) ? res.signals.length : 0;
      showToast(`Engine B primed ${symbols.join(', ')} (${count} candidate${count === 1 ? '' : 's'})`, 'success');
      await refreshSnapshot();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Engine B prime failed', 'error');
    } finally {
      setPrimingKey(null);
    }
  }, [refreshSnapshot, showToast]);

  const openChart = useCallback((candidate: EngineBHotlistCandidate, row: LdSymbolRow | null, autoReview: boolean) => {
    const intent = {
      ...openChartIntentPayload(candidate, row, timeframe),
      autoReview,
    };
    setTvChartIntent(intent);
    setActivePanel('tvChart');
    showToast(
      autoReview
        ? `Opening ${candidate.display} for Engine B chart review`
        : `Opening ${candidate.display} on TV Chart`,
      'info',
    );
  }, [setActivePanel, setTvChartIntent, showToast, timeframe]);

  const armWatch = useCallback(async (
    candidate: EngineBHotlistCandidate,
    row: LdSymbolRow | null,
    plan: SuggestedTradePlan | null,
  ) => {
    if (!plan) {
      showToast('No Engine B zone available', 'info');
      return;
    }
    setArmingKey(candidate.symbol);
    try {
      const res = await apiClient.postJson('/api/suggested-trades/flag', {
        symbol: candidate.symbol,
        display: candidate.display,
        signal: openChartIntentPayload(candidate, row, timeframe).signal,
        suggestedTradePlan: plan,
        source: 'engine_b_hotbench',
        createdAt: new Date().toISOString(),
      }) as { success?: boolean; error?: string };
      if (res?.success) {
        showToast(`Alert-only watch armed for ${candidate.display}`, 'success');
        await refreshWatches();
      } else {
        showToast(res?.error || 'Failed to arm watch', 'error');
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to arm watch', 'error');
    } finally {
      setArmingKey(null);
    }
  }, [refreshWatches, showToast, timeframe]);

  useEffect(() => {
    if (!autoReviewReadyOnly) return;
    for (const card of cards) {
      if (!card.candidate || !card.primaryWatch?.watch_id) continue;
      if (String(card.primaryWatch.status || '').toUpperCase() !== 'READY_FOR_REVIEW') continue;
      if (autoOpenedWatchIdsRef.current.has(card.primaryWatch.watch_id)) continue;
      autoOpenedWatchIdsRef.current.add(card.primaryWatch.watch_id);
      openChart(card.candidate, card.row, true);
      break;
    }
  }, [autoReviewReadyOnly, cards, openChart]);

  const selectedSymbols = hotlist?.selectedSymbols || winnerCandidates.flatMap((candidate) => candidate?.symbol ? [candidate.symbol] : []);
  // Only group winners above the scout threshold are auto-primable (req 10).
  const primeEligibleSymbols = useMemo(() => GROUPS.flatMap((group) => {
    const slot = hotlist?.groups?.[group.key]?.selected;
    return slot?.symbol && slot.eligible_for_prime ? [slot.symbol] : [];
  }), [hotlist]);
  const headerBusy = hotlistLoading || snapshotLoading || watchesLoading;

  return (
    <div className="space-y-4 pb-6">
      <Card className="border-border/60 bg-gradient-to-b from-card/80 to-card/40">
        <CardHeader className="pb-3">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <CardTitle className="flex items-center gap-2 text-lg">
                  <Radar className="h-5 w-5 text-primary" />
                  Engine B Hot Bench
                </CardTitle>
                <Badge variant="outline" className="text-[10px]">ALERT ONLY</Badge>
                <Badge variant="outline" className="text-[10px]">HOTLIST</Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                Cheap live scan picks one winner per group, then Engine B is primed only on the shortlist.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={timeframe} onValueChange={(value) => setTimeframe(value as 'H1' | 'M15')}>
                <SelectTrigger className="w-[88px] h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="H1">H1</SelectItem>
                  <SelectItem value="M15">M15</SelectItem>
                </SelectContent>
              </Select>
              <div className="flex items-center gap-2 rounded-md border border-border/60 px-3 py-1.5">
                <span className="text-[11px] text-muted-foreground">Auto-review ready only</span>
                <Switch checked={autoReviewReadyOnly} onCheckedChange={setAutoReviewReadyOnly} />
              </div>
              <Button
                size="sm"
                variant="outline"
                className="gap-1 text-xs"
                onClick={() => void primeSymbols(primeEligibleSymbols, 'ALL')}
                disabled={primeEligibleSymbols.length === 0 || primingKey !== null}
                title={primeEligibleSymbols.length === 0 ? 'No group winner is above the scout threshold' : 'Prime Engine B on eligible winners only'}
              >
                <Layers className="h-3.5 w-3.5" />
                {primingKey === 'ALL' ? 'Priming…' : `Prime Winners (${primeEligibleSymbols.length})`}
              </Button>
              <RefreshButton onClick={() => void refreshAll()} loading={headerBusy} />
            </div>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <Badge variant="outline" className="text-[10px]">
              {hotlist?.payloadVersion || 'engine-b-hotbench-v1'}
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              AI disabled in hotlist: {hotlist?.scoring?.usesAi === false ? 'yes' : 'not verified'}
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              Selected: {selectedSymbols.length}
            </Badge>
          </div>
        </CardHeader>
      </Card>

      {error && <ErrorBanner message={error} onRetry={() => void refreshAll()} />}

      <div className="grid gap-4 xl:grid-cols-2">
        {cards.map(({ group, candidate, slot, alternates, row, symbolWatches, primaryWatch, watchPlan, state, livePrice, livePriceAgeSec, livePriceSource }) => {
          const direction = directionForRow(row);
          const zone = actionableZone(row, direction);
          const weakSlot = slot?.status === 'WEAK_SCOUT' || slot?.status === 'NO_HOT_SETUP';
          const locked = Boolean(slot?.locked);
          // Prime allowed for eligible winners; locked symbols may be primed on
          // an explicit click even when weak (req 10).
          const canPrime = Boolean(candidate) && (slot?.eligible_for_prime || locked);
          const canReview = Boolean(
            candidate
            && row
            && slot?.ai_review_enabled !== false
            && ['ENGINE_B_CLEAR', 'NEAR_ZONE', 'WATCH_ARMED', 'READY_FOR_AI_REVIEW', 'AI_WAIT', 'AI_ENTRY_NOW', 'AI_SKIP'].includes(state.status),
          );
          const slotBusy = actionKey === `lock-${group.key}` || actionKey === `replace-${group.key}`;
          return (
            <Card key={group.key} className="border-border/60 bg-card/60">
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <CardTitle className="text-base">{group.label}</CardTitle>
                      {slot?.status && (
                        <Badge variant="outline" className={cn('text-[10px] border', slotStatusTone(slot.status))}>
                          {slot.status}
                        </Badge>
                      )}
                      <Badge variant="outline" className={cn('text-[10px] border', statusTone(state.status))}>
                        {state.status}
                      </Badge>
                      {locked && (
                        <Badge variant="outline" className="text-[10px] border border-primary/40 text-primary">
                          LOCKED
                        </Badge>
                      )}
                    </div>
                    <div className="text-sm text-muted-foreground">
                      {candidate ? `${candidate.display} · ${candidate.asset_type}` : 'missing'}
                    </div>
                  </div>
                  {symbolWatches.length > 0 && <CompactSuggestedWatchStatus watches={symbolWatches} />}
                </div>
                <p className="text-xs text-muted-foreground">{slot?.reason || state.reason}</p>
              </CardHeader>
              <CardContent className="space-y-4">
                {!candidate && (
                  <div className="rounded-md border border-dashed border-border/60 bg-muted/20 p-4 text-sm text-muted-foreground">
                    No hotlist winner returned for this group yet.
                  </div>
                )}

                {candidate && (
                  <>
                    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                      <Metric label="Latest (live)" value={priceOrFallback(firstNumber(livePrice, row?.latest_price, candidate.latest_price), candidate.display, candidate.asset_type, 'missing')} />
                      <Metric label="Bid / Ask" value={`${priceOrFallback(firstNumber(candidate.bid, row?.bid), candidate.display, candidate.asset_type, 'N/A')} / ${priceOrFallback(firstNumber(candidate.ask, row?.ask), candidate.display, candidate.asset_type, 'N/A')}`} />
                      <Metric label="Spread" value={fmtNum(firstNumber(candidate.spread, row?.spread), 5, 'N/A')} />
                      <Metric label="Strength" value={fmtNum(candidate.strength_score, 1, 'missing')} accent="long" />
                      <Metric label="Move %" value={fmtNum(firstNumber(candidate.change_pct, row?.change_pct), 2, 'missing')} />
                      <Metric
                        label="Live quote"
                        value={livePriceAgeSec == null
                          ? 'age unknown'
                          : `${livePriceAgeSec.toFixed(1)}s · ${livePriceSource || 'unknown source'}`}
                        accent={livePriceAgeSec == null ? 'short' : undefined}
                      />
                    </div>

                    <div className="rounded-md border border-border/60 bg-muted/15 p-3">
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Scout reason</div>
                      <div className="mt-1 text-sm">{candidate.reason}</div>
                    </div>

                    <Sparkline candles={row?.chart?.candles} />

                    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                      <Metric label="Verdict" value={row?.engineB.structuralVerdict || 'missing'} />
                      <Metric label="Direction" value={direction || 'missing'} />
                      <Metric
                        label="Engine B"
                        value={state.status === 'SCOUTING' ? 'SCOUTING' : row?.engineB.confidencePassed ? 'PASS' : 'PRIMED'}
                        accent={row?.engineB.confidencePassed ? 'long' : undefined}
                      />
                      <Metric label="Signal entry" value={priceOrFallback(firstNumber(row?.levels?.entry, row?.engineB.entry), candidate.display, candidate.asset_type, 'missing')} />
                      <Metric label="SL" value={priceOrFallback(firstNumber(row?.levels?.sl, row?.engineB.sl), candidate.display, candidate.asset_type, 'missing')} accent="short" />
                      <Metric label="TP" value={priceOrFallback(firstNumber(row?.levels?.tp1, row?.levels?.tp, row?.engineB.tp), candidate.display, candidate.asset_type, 'missing')} accent="long" />
                      <Metric label="R:R" value={fmtNum(firstNumber(row?.levels?.rr, row?.engineB.rr), 2, 'missing')} />
                      <Metric label="Trigger" value={primaryWatch?.status || row?.engineB.noTriggerClassification || 'missing'} />
                      <Metric label="Zone TF" value={row?.engineB.sourceTimeframes?.zone_tf || row?.timeframe || timeframe} />
                    </div>

                    <div className="grid gap-2 sm:grid-cols-2">
                      <Metric
                        label="Support zone"
                        value={zone && direction === 'LONG'
                          ? `${fmtPrice(zone.lower, candidate.display, candidate.asset_type)} → ${fmtPrice(zone.upper, candidate.display, candidate.asset_type)}`
                          : 'missing'}
                      />
                      <Metric
                        label="Resistance zone"
                        value={zone && direction === 'SHORT'
                          ? `${fmtPrice(zone.lower, candidate.display, candidate.asset_type)} → ${fmtPrice(zone.upper, candidate.display, candidate.asset_type)}`
                          : 'missing'}
                      />
                    </div>

                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1 text-xs"
                        onClick={() => void primeSymbols([candidate.symbol], candidate.symbol)}
                        disabled={primingKey !== null || !canPrime}
                        title={canPrime ? 'Prime Engine B on this symbol' : 'Below scout threshold — lock it first to prime manually'}
                      >
                        <Layers className="h-3.5 w-3.5" />
                        {primingKey === candidate.symbol ? 'Priming…' : 'Prime Engine B'}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1 text-xs"
                        onClick={() => toggleLock(slot, group.key)}
                        disabled={slotBusy}
                        title={locked ? 'Unlock — allow auto-rotation again' : 'Lock — keep this symbol on the card across refreshes'}
                      >
                        {locked ? <Unlock className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
                        {locked ? 'Unlock' : 'Lock'}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1 text-xs"
                        onClick={() => openChart(candidate, row, false)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                        Open Chart
                      </Button>
                      <Button
                        size="sm"
                        className="gap-1 text-xs"
                        onClick={() => openChart(candidate, row, true)}
                        disabled={!canReview}
                        title={canReview ? 'Run a fresh Engine B chart review' : weakSlot ? 'No candidate above scout threshold' : 'Prime Engine B first and wait for a usable candidate'}
                      >
                        <Bot className="h-3.5 w-3.5" />
                        AI Review
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1 text-xs"
                        onClick={() => void armWatch(candidate, row, watchPlan.plan)}
                        disabled={!watchPlan.plan || armingKey !== null}
                        title={watchPlan.reason}
                      >
                        <Flag className="h-3.5 w-3.5" />
                        {armingKey === candidate.symbol ? 'Arming…' : 'Arm Watch'}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="gap-1 text-xs"
                        onClick={() => void refreshAll()}
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                        Refresh
                      </Button>
                    </div>

                    {alternates.length > 0 && (
                      <div className="rounded-md border border-border/60 bg-muted/15 p-3">
                        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Alternates</div>
                        <div className="mt-2 space-y-1.5">
                          {alternates.map((alt) => (
                            <AlternateRow
                              key={`${group.key}-${alt.symbol}`}
                              alt={alt}
                              disabled={slotBusy || locked}
                              lockedHint={locked}
                              onReplace={() => replaceSlot(group.key, alt.symbol, alt.display || alt.symbol)}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function slotStatusTone(status: string): string {
  if (status === 'PRIORITY' || status === 'HOT') {
    return 'bg-long/15 text-long border-long/30';
  }
  if (status === 'SCOUT') {
    return 'bg-warning/15 text-warning border-warning/30';
  }
  // WEAK_SCOUT / NO_HOT_SETUP
  return 'bg-muted/40 text-muted-foreground border-border/40';
}

function AlternateRow({
  alt,
  disabled,
  lockedHint,
  onReplace,
}: {
  alt: EngineBHotlistAlternate;
  disabled: boolean;
  lockedHint: boolean;
  onReplace: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-border/40 bg-background/40 px-2 py-1.5">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-mono">{alt.display || alt.symbol}</span>
          <Badge variant="outline" className="text-[10px]">{fmtNum(alt.strength_score, 1, '—')}</Badge>
          {alt.change_pct != null && (
            <span className={cn('text-[10px] font-mono', alt.change_pct >= 0 ? 'text-long' : 'text-short')}>
              {fmtNum(alt.change_pct, 2, '—')}%
            </span>
          )}
        </div>
        <div className="truncate text-[11px] text-muted-foreground">{alt.reason}</div>
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="gap-1 text-xs shrink-0"
        onClick={onReplace}
        disabled={disabled}
        title={lockedHint ? 'Unlock the card first to switch symbols' : `Switch this card to ${alt.symbol}`}
      >
        <ArrowLeftRight className="h-3.5 w-3.5" />
        Replace
      </Button>
    </div>
  );
}

function Metric({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: 'long' | 'short';
}) {
  const tone = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : 'text-foreground';
  return (
    <div className="rounded-md border border-border/50 bg-muted/20 p-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={cn('mt-1 text-sm font-mono', tone)}>{value}</div>
    </div>
  );
}
