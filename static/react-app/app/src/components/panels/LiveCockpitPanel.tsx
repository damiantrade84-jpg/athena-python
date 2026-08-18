import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Checkbox } from '@/components/ui/checkbox';
import { ErrorBanner, RefreshButton } from '@/components/shared';
import { resolveChartIntentSymbol } from '@/lib/chartIdentity';
import AITradingAgentPanel from '@/components/ai/AITradingAgentPanel';
import ConfidenceCalibrationPanel from '@/components/athena/ConfidenceCalibrationPanel';
import SessionHeatChart, { SessionHeatPill } from '@/components/panels/SessionHeatChart';
import {
  Radio,
  Bot,
  Activity,
  Zap,
  Layers,
  GitMerge,
  TrendingUp,
  Eye,
  Play,
  AlertTriangle,
  Check,
  X,
  Info,
  ChevronDown,
  Plus,
  Search,
  LineChart,
  Flame,
} from 'lucide-react';
import { cn, fmtNum, toNum } from '@/lib/utils';
import { engineBScoreBreakdown, fmtPrice } from '@/lib/athenaFormat';
import { readEngineBCanonicalGatesFromRow } from '@/lib/engineBCanonicalGates';
import type {
  LdSnapshot,
  LdSymbolRow,
  LdEngineARow,
  LdEngineBRow,
  LdEngineCRow,
  LdEngineDRow,
  LdAiReview,
  LdSessionHeat,
  SessionHeatIndicator,
  AiTradeChatSignalPayload,
} from '@/types/athena';

const DEFAULT_SYMBOLS = 'EUR/USD,GBP/USD,XAU/USD,BTCUSDT,ETHUSDT,NVDA,AAPL,MSFT';

/**
 * Case/separator-insensitive symbol key. Mirrors scanner._normalize_scan_symbol
 * and api_scan_naked._norm_sym so that "BTCUSDT" (cockpit default) and
 * "BTC/USDT" (/api/pairs label) resolve to the same pair everywhere: checkbox
 * state, group toggles, and the scan scope the backend receives.
 */
const normSym = (s: string) => String(s || '').toUpperCase().replace(/[/_\s]/g, '').trim();
const PAIR_GROUP_ORDER = ['Forex', 'Crypto', 'Commodities', 'Indices', 'US Stocks', 'ETFs'];
// Snapshot builds fetch candles per symbol; keep this above observed endpoint
// latency so the browser does not stack overlapping read-only requests.
const POLL_MS = 15000;

export default function LiveCockpitPanel() {
  const { showToast, setTvChartIntent, setActivePanel } = useStore();
  const [symbolsInput, setSymbolsInput] = useState(DEFAULT_SYMBOLS);
  const [activeSymbols, setActiveSymbols] = useState(DEFAULT_SYMBOLS);
  const [tf, setTf] = useState<'H1' | 'H4' | 'D1'>('H4');
  const [autoPoll, setAutoPoll] = useState(true);
  const [filter, setFilter] = useState<string>('tradeable');
  const [selected, setSelected] = useState<string | null>(null);
  const [forexBMode, setForexBMode] = useState(false);
  const [snapshot, setSnapshot] = useState<LdSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pairGroups, setPairGroups] = useState<Record<string, string[]>>({});
  const [pairSearch, setPairSearch] = useState('');
  const [pairsDropdownOpen, setPairsDropdownOpen] = useState(false);
  const [showSessionHeat, setShowSessionHeat] = useState(false);
  const [scanEngine, setScanEngine] = useState<'A' | 'B' | 'AB'>('AB');
  const [scanScope, setScanScope] = useState<string>('selected');
  const [scanning, setScanning] = useState(false);
  const [scanNote, setScanNote] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef(false);
  const hasSnapshotRef = useRef(false);
  const selectedRef = useRef<string | null>(null);
  const { post: postPaperExec, loading: papering } = useApiPost<{ ok?: boolean; error?: string; ticket?: string }>();

  // Keep ref in sync so fetchSnap can read current selection without being a dep
  useEffect(() => { selectedRef.current = selected; }, [selected]);

  const fetchSnap = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!hasSnapshotRef.current) setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      const syms = activeSymbols.trim();
      if (syms) params.set('symbols', syms);
      params.set('timeframe', tf);
      const res = await fetch(`/api/live-dashboard/snapshot?${params.toString()}`);
      const data = (await res.json()) as LdSnapshot;
      if (!res.ok || data?.error) {
        setError(data?.error || `HTTP ${res.status}`);
      } else {
        hasSnapshotRef.current = true;
        setSnapshot(data);
        if (!selectedRef.current && data.symbols && data.symbols.length > 0) {
          setSelected(data.symbols[0].symbol);
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, [activeSymbols, tf]);

  useEffect(() => {
    fetchSnap();
  }, [fetchSnap]);

  // Fetch available pairs for the dropdown, preserving group structure from /api/pairs
  useEffect(() => {
    fetch('/api/pairs')
      .then((r) => r.json())
      .then((data) => {
        const groups: Record<string, string[]> = {};
        const raw = data?.groups && typeof data.groups === 'object'
          ? data.groups as Record<string, unknown[]>
          : null;
        if (raw) {
          for (const [groupName, pairs] of Object.entries(raw)) {
            const labels = (pairs as Array<{ label?: string; sym?: string; enabled?: boolean }>)
              .filter((p) => p.enabled !== false)
              .map((p) => p.label || p.sym || '')
              .filter(Boolean)
              .sort();
            if (labels.length > 0) groups[groupName] = labels;
          }
        } else {
          // Fallback: flat array without group info
          const flat: string[] = [];
          const src = Array.isArray(data?.pairs) ? data.pairs : Array.isArray(data) ? data : [];
          for (const p of src as Array<{ label?: string; sym?: string; display?: string; enabled?: boolean }>) {
            if (p.enabled !== false) {
              const lbl = p.label || p.display || p.sym || '';
              if (lbl) flat.push(lbl);
            }
          }
          if (flat.length > 0) groups['All'] = flat.sort();
        }
        setPairGroups(groups);
      })
      .catch(() => setPairGroups({}));
  }, []);

  const activeSymbolsSet = useMemo(() => {
    return new Set(activeSymbols.split(',').map((s) => s.trim()).filter(Boolean));
  }, [activeSymbols]);

  /** Normalised view of the selection, for membership checks against pair labels. */
  const activeSymbolsNorm = useMemo(() => {
    return new Set(Array.from(activeSymbolsSet).map(normSym));
  }, [activeSymbolsSet]);

  const filteredPairGroups = useMemo(() => {
    const q = pairSearch.toLowerCase().trim();
    const orderedKeys = [
      ...PAIR_GROUP_ORDER.filter((g) => pairGroups[g]),
      ...Object.keys(pairGroups).filter((g) => !PAIR_GROUP_ORDER.includes(g)),
    ];
    const result: [string, string[]][] = [];
    for (const key of orderedKeys) {
      const syms = pairGroups[key] || [];
      const filtered = q ? syms.filter((s) => s.toLowerCase().includes(q)) : syms;
      if (filtered.length > 0) result.push([key, filtered]);
    }
    return result;
  }, [pairGroups, pairSearch]);

  useEffect(() => {
    if (pollRef.current) clearTimeout(pollRef.current);
    if (!autoPoll) return;
    const tick = async () => {
      await fetchSnap();
      if (autoPoll) {
        pollRef.current = setTimeout(tick, POLL_MS);
      }
    };
    pollRef.current = setTimeout(tick, POLL_MS);
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [autoPoll, fetchSnap]);

  const symbols = snapshot?.symbols || [];
  const events = snapshot?.events || [];
  const conn = snapshot?.connections || {};
  const paperMode = snapshot?.paperMode || {};
  const filtered = useMemo(() => {
    const rows = symbols.filter((s) => {
      // Only signals that actually reached a tradeable decision. Backed by the
      // snapshot's tradeGrade so this agrees with the execute gate by
      // construction; falls back to finalState on an older payload.
      if (filter === 'tradeable') {
        return s.tradeGrade
          ? s.tradeGrade.isTradeGrade
          : s.finalState === 'PAPER CANDIDATE';
      }
      if (filter === 'aligned') return s.engineC?.decisionState === 'ALIGNED';
      if (filter === 'watchlist') return s.finalState === 'WATCHLIST';
      if (filter === 'paper') return s.finalState === 'PAPER CANDIDATE';
      if (filter === 'blocked') return s.finalState === 'BLOCKED';
      if (filter === 'bcandidate')
        return Boolean(s.engineB?.confidencePassed || s.engineB?.structuralVerdict === 'CLEAR');
      return true;
    });
    // Engine B leads forex: rank passing/CLEAR structures first, then by score.
    if (!forexBMode) return rows;
    const rank = (r: LdSymbolRow) => {
      let v = toNum(r.engineB?.gateScore ?? r.engineB?.score) || 0;
      if (r.engineB?.confidencePassed) v += 1000;
      if (r.engineB?.structuralVerdict === 'CLEAR') v += 100;
      return v;
    };
    return [...rows].sort((a, b) => rank(b) - rank(a));
  }, [symbols, filter, forexBMode]);

  const selectedRow = symbols.find((s) => s.symbol === selected) || null;

  const toggleSymbol = useCallback((display: string) => {
    const current = activeSymbols.split(',').map((s) => s.trim()).filter(Boolean);
    const key = normSym(display);
    const next = current.some((s) => normSym(s) === key)
      ? current.filter((s) => normSym(s) !== key)
      : [...current, display];
    const joined = next.join(',');
    setSymbolsInput(joined);
    setActiveSymbols(joined);
  }, [activeSymbols]);

  /** Add or remove a whole group of symbols in one action. */
  const toggleGroup = useCallback((groupSymbols: string[], select: boolean) => {
    const current = activeSymbols.split(',').map((s) => s.trim()).filter(Boolean);
    const keys = new Set(groupSymbols.map(normSym));
    // Drop every spelling of the group's pairs, then re-add if selecting, so a
    // pair can never end up in the selection twice under two spellings.
    const kept = current.filter((s) => !keys.has(normSym(s)));
    const joined = (select ? [...kept, ...groupSymbols] : kept).join(',');
    setSymbolsInput(joined);
    setActiveSymbols(joined);
  }, [activeSymbols]);

  const onPaperExecute = useCallback(
    async (row: LdSymbolRow) => {
      const direction = row.engineA?.direction || row.engineB?.direction;
      if (!direction) {
        showToast('No direction available — Engine A/B did not produce a signal.', 'error');
        return;
      }
      const r = await postPaperExec('/api/live-dashboard/paper-execute', {
        symbol: row.symbol,
        direction,
        entry: row.levels?.entry,
        sl: row.levels?.sl,
        tp: row.levels?.tp,
        rr: row.levels?.rr,
      });
      if (r?.ok) showToast(`Paper execute logged for ${row.symbol}`, 'success');
      else showToast(`Paper execute blocked: ${r?.error || 'unknown'}`, 'error');
    },
    [postPaperExec, showToast],
  );

  // Unified scan runner. Engine A (/api/scan) and Engine B (/api/scan-naked)
  // both accept the same explicit `symbols` scope, so one operator selection
  // resolves to the same pairs on either engine. Scope 'all' sends no symbols
  // and no asset class, which is each engine's whole-universe scan; an asset
  // class scope sends assetClass only. Results warm the caches the snapshot
  // reads, then the cockpit is repopulated with the symbols that produced
  // signals so a broad scan is actually visible.
  const runScan = useCallback(async () => {
    const selected = activeSymbols.trim();
    if (scanScope === 'selected' && !selected) {
      showToast('No symbols selected — pick some, or switch the scope to Everything.', 'info');
      return;
    }

    const body: Record<string, unknown> = { style: 'auto' };
    if (scanScope === 'selected') body.symbols = selected;
    else if (scanScope !== 'all') body.asset_class = scanScope;

    const engines: Array<'A' | 'B'> = scanEngine === 'AB' ? ['A', 'B'] : [scanEngine];
    const scopeLabel =
      scanScope === 'selected' ? `${activeSymbolsSet.size} selected`
        : scanScope === 'all' ? 'all pairs'
          : scanScope;

    setScanning(true);
    setScanNote(`Scanning ${scopeLabel} — Engine ${engines.join(' + ')}…`);
    const found: string[] = [];
    const counts: string[] = [];
    const failures: string[] = [];

    try {
      for (const eng of engines) {
        // Engine B reads `assetClass`; Engine A reads `asset_class`.
        const payload = eng === 'B' && body.asset_class
          ? { ...body, assetClass: body.asset_class }
          : body;
        const url = eng === 'A' ? '/api/scan' : '/api/scan-naked';
        let res: { signals?: Array<Record<string, unknown>>; error?: string } | null = null;
        try {
          const raw = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          res = await raw.json();
        } catch (e) {
          failures.push(`Engine ${eng}: ${String(e)}`);
          continue;
        }
        if (res?.error) {
          // "Scan already in progress" is the common one — surface it verbatim.
          failures.push(`Engine ${eng}: ${res.error}`);
          continue;
        }
        // Engine A separates tradeSignals from the watchlist; prefer it so a
        // broad scan loads only what actually reached TRADE. Engine B's
        // `signals` are already its passing structures.
        const raw = res as { tradeSignals?: Array<Record<string, unknown>>; signals?: Array<Record<string, unknown>> };
        const sigs = Array.isArray(raw.tradeSignals)
          ? raw.tradeSignals
          : Array.isArray(raw.signals) ? raw.signals : [];
        counts.push(`Engine ${eng}: ${sigs.length}`);
        for (const s of sigs) {
          const label = (s.display || s.pair || s.symbol) as string | undefined;
          if (label && !found.includes(label)) found.push(label);
        }
      }

      // Repopulate the cockpit with what the scan actually found, capped at the
      // server's MAX_SYMBOLS so the snapshot is not silently truncated.
      const cap = snapshot?.maxSymbols ?? 16;
      if (found.length > 0 && scanScope !== 'selected') {
        const next = found.slice(0, cap).join(',');
        setSymbolsInput(next);
        setActiveSymbols(next);
        if (found.length > cap) {
          setScanNote(`${found.length} signals — showing the first ${cap} (server MAX_SYMBOLS).`);
        } else {
          setScanNote(null);
        }
      } else {
        setScanNote(null);
      }

      setForexBMode(engines.includes('B'));
      // Stay on tradeable-only: the scan just loaded the trade-tier symbols and
      // the operator asked to see what made it to trade, not the rejects.
      setFilter('tradeable');
      await fetchSnap();

      if (failures.length) {
        showToast(failures.join(' · '), 'error');
      } else if (found.length === 0) {
        showToast(`No candidates in ${scopeLabel} (${counts.join(', ')})`, 'info');
      } else {
        showToast(`${counts.join(', ')} — ${found.length} symbol${found.length === 1 ? '' : 's'} with signals`, 'success');
      }
    } finally {
      setScanning(false);
    }
  }, [
    activeSymbols, activeSymbolsSet.size, scanScope, scanEngine,
    snapshot?.maxSymbols, showToast, fetchSnap,
  ]);

  // Open a cockpit symbol on the TV Chart with Engine B context for AI review.
  // Advisory only - mirrors SignalsPanel's setTvChartIntent handoff; no execution.
  const openOnChart = useCallback(
    (row: LdSymbolRow) => {
      const symbol = resolveChartIntentSymbol({
        symbol: row.symbol,
        display: row.symbol,
      });
      if (!symbol) {
        showToast('Cannot open chart: missing symbol', 'error');
        return;
      }
      const eb = row.engineB;
      const signal = {
        symbol,
        pair: symbol,
        display: symbol,
        type: row.asset_type,
        direction: eb?.direction || row.engineA?.direction || null,
        engine: 'engine_b',
        engine_source: 'engine_b',
        timeframe: tf,
        structureTf: eb?.sourceTimeframes?.zone_tf || null,
        triggerTf: eb?.sourceTimeframes?.trigger_tf || null,
        executionTf: eb?.sourceTimeframes?.entry_tf || null,
        score: eb?.score ?? null,
        threshold: eb?.threshold ?? null,
        entry: row.levels?.entry ?? eb?.entry ?? null,
        sl: row.levels?.sl ?? eb?.sl ?? null,
        tp1: row.levels?.tp1 ?? row.levels?.tp ?? eb?.tp ?? null,
        rr1: row.levels?.rr ?? eb?.rr ?? null,
        structuralVerdict: eb?.structuralVerdict ?? null,
      };
      setTvChartIntent({
        id: `tv-${symbol}-${Date.now()}`,
        source: 'engine_b',
        symbol,
        display: row.symbol,
        signal,
        preferredTf: tf,
        autoReview: true,
        createdAt: new Date().toISOString(),
      });
      setActivePanel('tvChart');
      showToast(`Opening ${row.symbol} on TV Chart for AI review`, 'info');
    },
    [tf, setTvChartIntent, setActivePanel, showToast],
  );

  const fresh = snapshot?.freshnessAllOk !== false;

  // Session heat: the snapshot carries the current session plus a per-row cue.
  // The engine-level cues below are identical across rows of the same score
  // group, so the header badge reads them off the first available row.
  const sessionHeatSummary = snapshot?.sessionHeat;
  const sessionHeatCurrentKey = sessionHeatSummary?.currentSession?.key;
  const sessionHeatEngineA = symbols.find((s) => s.sessionHeat?.engineA)?.sessionHeat?.engineA;
  const sessionHeatEngineB = symbols.find((s) => s.sessionHeat?.engineB)?.sessionHeat?.engineB;
  const selectedScoreGroup = symbols.find((s) => s.symbol === selected)?.sessionHeat?.scoreGroup ?? null;

  return (
    <div className="flex flex-col h-[calc(100vh-120px)] gap-3 overflow-hidden">
      {/* Command deck — status + controls in one cohesive header */}
      <Card className="border-border/60 bg-gradient-to-b from-card/70 to-card/40 shrink-0 overflow-hidden">
        <CardContent className="p-0">
          {/* Status row */}
          <div className="flex items-center gap-2 flex-wrap px-3 py-2 border-b border-border/40">
            <div className="flex items-center gap-1.5">
              <span className="relative flex h-2 w-2">
                {autoPoll && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary/60" />
                )}
                <span className={cn('relative inline-flex rounded-full h-2 w-2', autoPoll ? 'bg-primary' : 'bg-muted-foreground')} />
              </span>
              <span className="text-sm font-semibold tracking-wide">
                Live Cockpit
              </span>
              <Badge variant="outline" className="text-[9px] ml-0.5">{snapshot?.payloadVersion || 'v3'}</Badge>
            </div>
            <div className="h-4 w-px bg-border/60 mx-1" />
            <Badge className={cn('text-[10px] gap-1', connBg(conn.mt5))}>MT5 · {(conn.mt5 || 'unknown').toUpperCase()}</Badge>
            <Badge className={cn('text-[10px] gap-1', connBg(conn.binanceWs === 'live' ? 'connected' : conn.binanceWs))}>
              BINANCE · {(conn.binanceWs || 'unknown').toUpperCase()}
            </Badge>
            <Badge className={cn('text-[10px]', fresh ? 'bg-long/20 text-long' : 'bg-warning/20 text-warning')}>
              {fresh ? '✓ FRESHNESS OK' : '⚠ FRESHNESS ISSUE'}
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              Paper {paperMode.enabled ? 'ON' : 'OFF'} · Orders {paperMode.realOrdersAllowed ? 'LIVE' : 'BLOCKED'}
            </Badge>
            {sessionHeatSummary?.enabled && (
              <button
                type="button"
                onClick={() => setShowSessionHeat((v) => !v)}
                title={
                  sessionHeatSummary.available
                    ? `${sessionHeatSummary.currentSession?.label} ${sessionHeatSummary.currentSession?.windowUtc} · ${sessionHeatSummary.currentSession?.killzone}. Click for the full session heat chart.`
                    : 'Session heat aggregate is still warming — click for details.'
                }
                className={cn(
                  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-mono transition-colors',
                  showSessionHeat
                    ? 'border-primary/60 bg-primary/15 text-primary'
                    : 'border-border/60 bg-muted/30 text-muted-foreground hover:text-foreground',
                )}
              >
                <Flame className="w-3 h-3" />
                {sessionHeatSummary.currentSession?.label || 'Session'}
                {sessionHeatSummary.available ? (
                  <>
                    <span className="opacity-60">A</span>
                    <span className={heatWordClass(sessionHeatEngineA?.heat)}>
                      {heatWord(sessionHeatEngineA?.heat)}
                    </span>
                    <span className="opacity-60">B</span>
                    <span className={heatWordClass(sessionHeatEngineB?.heat)}>
                      {heatWord(sessionHeatEngineB?.heat)}
                    </span>
                  </>
                ) : (
                  <span className="opacity-60">{sessionHeatSummary.status === 'WARMING' ? 'warming…' : 'n/a'}</span>
                )}
              </button>
            )}

            <div className="flex items-center gap-2 ml-auto">
              <span className="text-[10px] text-muted-foreground uppercase tracking-wider">Auto</span>
              <Switch checked={autoPoll} onCheckedChange={setAutoPoll} />
              <RefreshButton onClick={fetchSnap} loading={loading} />
            </div>
          </div>

          {/* Control row */}
          <div className="flex items-center gap-2 flex-wrap px-3 py-2">
            <Popover open={pairsDropdownOpen} onOpenChange={setPairsDropdownOpen}>
              <PopoverTrigger asChild>
                <Button variant="outline" className="h-8 text-xs flex items-center gap-1">
                  <Plus className="w-3.5 h-3.5" />
                  Symbols
                  <ChevronDown className="w-3 h-3" />
                  {activeSymbolsSet.size > 0 && (
                    <Badge variant="secondary" className="text-[9px] ml-1">
                      {activeSymbolsSet.size}
                    </Badge>
                  )}
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-[280px] p-0" align="start">
                <div className="p-2 border-b flex items-center gap-1.5">
                  <Search className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                  <input
                    autoFocus
                    placeholder="Search symbols…"
                    value={pairSearch}
                    onChange={(e) => setPairSearch(e.target.value)}
                    className="flex-1 text-xs bg-transparent outline-none placeholder:text-muted-foreground"
                  />
                  {pairSearch && (
                    <button onClick={() => setPairSearch('')} className="text-muted-foreground hover:text-foreground">
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </div>
                <ScrollArea className="h-[320px]">
                  {filteredPairGroups.length === 0 && (
                    <p className="text-[11px] text-muted-foreground p-3">No pairs found.</p>
                  )}
                  {filteredPairGroups.map(([group, syms]) => {
                    const selectedInGroup = syms.filter((s) => activeSymbolsNorm.has(normSym(s))).length;
                    const allSelected = selectedInGroup === syms.length && syms.length > 0;
                    return (
                    <div key={group}>
                      <div
                        className="px-3 py-1.5 flex items-center gap-2 bg-muted/30 border-b border-border/40 cursor-pointer hover:bg-muted/50 transition-colors"
                        onClick={() => toggleGroup(syms, !allSelected)}
                        title={allSelected ? `Deselect all ${syms.length} in ${group}` : `Select all ${syms.length} in ${group}`}
                      >
                        <Checkbox
                          checked={allSelected}
                          className="h-3.5 w-3.5"
                          onClick={(e) => { e.stopPropagation(); toggleGroup(syms, !allSelected); }}
                        />
                        <span className="text-[9px] uppercase font-semibold text-muted-foreground tracking-widest flex-1">
                          {group} <span className="opacity-60">({syms.length})</span>
                        </span>
                        {selectedInGroup > 0 && (
                          <span className="text-[9px] font-mono text-primary">{selectedInGroup} on</span>
                        )}
                      </div>
                      {syms.map((sym) => {
                        const checked = activeSymbolsNorm.has(normSym(sym));
                        return (
                          <div
                            key={sym}
                            className={cn(
                              'flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-muted/50 transition-colors',
                              checked && 'bg-primary/5',
                            )}
                            onClick={() => toggleSymbol(sym)}
                          >
                            <Checkbox checked={checked} className="h-3.5 w-3.5" />
                            <span className="text-xs font-mono flex-1">{sym}</span>
                            {checked && <Check className="w-3 h-3 text-primary shrink-0" />}
                          </div>
                        );
                      })}
                    </div>
                    );
                  })}
                </ScrollArea>
                <div className="p-2 border-t flex items-center justify-between">
                  <span className="text-[10px] text-muted-foreground">
                    {activeSymbolsSet.size} selected
                  </span>
                  <div className="flex gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 text-[10px]"
                      title="Select every symbol currently listed"
                      onClick={() => toggleGroup(filteredPairGroups.flatMap(([, s]) => s), true)}
                    >
                      All
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 text-[10px]"
                      title="Clear the whole selection"
                      onClick={() => { setSymbolsInput(''); setActiveSymbols(''); }}
                    >
                      None
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 text-[10px]"
                      onClick={() => {
                        setSymbolsInput(DEFAULT_SYMBOLS);
                        setActiveSymbols(DEFAULT_SYMBOLS);
                      }}
                    >
                      Reset
                    </Button>
                    <Button
                      size="sm"
                      className="h-6 text-[10px]"
                      onClick={() => { setPairsDropdownOpen(false); setPairSearch(''); }}
                    >
                      Done
                    </Button>
                  </div>
                </div>
              </PopoverContent>
            </Popover>

            <Select value={tf} onValueChange={(v) => setTf(v as 'H1' | 'H4' | 'D1')}>
              <SelectTrigger className="w-[72px] h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="H1">H1</SelectItem>
                <SelectItem value="H4">H4</SelectItem>
                <SelectItem value="D1">D1</SelectItem>
              </SelectContent>
            </Select>
            <Select value={filter} onValueChange={setFilter}>
              <SelectTrigger className="w-[150px] h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tradeable">Tradeable only</SelectItem>
                <SelectItem value="all">All states</SelectItem>
                <SelectItem value="bcandidate">Engine B candidate</SelectItem>
                <SelectItem value="paper">Paper candidate</SelectItem>
                <SelectItem value="aligned">Engine C aligned</SelectItem>
                <SelectItem value="watchlist">Watchlist</SelectItem>
                <SelectItem value="blocked">Blocked</SelectItem>
              </SelectContent>
            </Select>
            {/* Scan control: engine x scope x go. Both engines take the same
                symbol scope, so one selection drives either. */}
            <div className="flex items-center gap-1 rounded-md border border-border/60 bg-muted/20 pl-1.5 pr-1 py-0.5">
              <Select value={scanEngine} onValueChange={(v) => setScanEngine(v as 'A' | 'B' | 'AB')}>
                <SelectTrigger className="w-[104px] h-7 text-[11px] border-0 bg-transparent shadow-none">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="AB">Engine A + B</SelectItem>
                  <SelectItem value="A">Engine A</SelectItem>
                  <SelectItem value="B">Engine B</SelectItem>
                </SelectContent>
              </Select>
              <span className="text-[10px] text-muted-foreground">on</span>
              <Select value={scanScope} onValueChange={setScanScope}>
                <SelectTrigger className="w-[132px] h-7 text-[11px] border-0 bg-transparent shadow-none">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="selected">Selected ({activeSymbolsSet.size})</SelectItem>
                  <SelectItem value="all">Everything</SelectItem>
                  <SelectItem value="forex">Forex</SelectItem>
                  <SelectItem value="crypto">Crypto</SelectItem>
                  <SelectItem value="commodity">Commodities</SelectItem>
                  <SelectItem value="index">Indices</SelectItem>
                  <SelectItem value="stock">Stocks</SelectItem>
                  <SelectItem value="etf">ETFs</SelectItem>
                </SelectContent>
              </Select>
              <Button
                size="sm"
                className="h-7 text-[11px] gap-1 px-2"
                onClick={runScan}
                disabled={scanning || (scanScope === 'selected' && activeSymbolsSet.size === 0)}
                title={
                  scanScope === 'selected'
                    ? 'Scan the symbols you selected'
                    : scanScope === 'all'
                      ? 'Scan every enabled pair, then load the results into the cockpit'
                      : `Scan every enabled ${scanScope} pair, then load the results into the cockpit`
                }
              >
                <Search className="w-3.5 h-3.5" />
                {scanning ? 'Scanning…' : 'Scan'}
              </Button>
            </div>
            {forexBMode && (
              <Button
                size="sm"
                variant="ghost"
                className="h-8 text-[10px] text-muted-foreground"
                onClick={() => { setForexBMode(false); setFilter('all'); }}
                title="Clear Engine B ranking and return to default state ordering"
              >
                Clear B rank
              </Button>
            )}

            {/* Selected symbols as removable badges */}
            <div className="flex items-center gap-1 flex-nowrap overflow-x-auto flex-1 min-w-0 ml-1 py-0.5">
              {Array.from(activeSymbolsSet).map((sym) => (
                <Badge
                  key={sym}
                  variant="secondary"
                  className="text-[10px] font-mono cursor-pointer hover:bg-destructive/20 shrink-0"
                  onClick={() => toggleSymbol(sym)}
                >
                  {sym} <X className="w-2.5 h-2.5 inline ml-0.5" />
                </Badge>
              ))}
              {activeSymbolsSet.size === 0 && (
                <span className="text-[11px] text-muted-foreground">No symbols selected</span>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {(scanning || scanNote) && (
        <div className="flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-1.5 text-[11px] text-muted-foreground shrink-0">
          {scanning && <span className="inline-block h-2 w-2 rounded-full bg-primary animate-pulse shrink-0" />}
          <span>{scanNote || 'Scanning…'}</span>
          {scanning && (
            <span className="opacity-70">
              A full-universe scan takes a while — the cockpit refreshes when it lands.
            </span>
          )}
        </div>
      )}

      {!paperMode.realOrdersAllowed && (
        <div className="flex items-start gap-2 rounded-md border border-border/50 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground leading-relaxed shrink-0">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            <strong className="text-foreground">Real orders: BLOCKED</strong> is expected when <code className="text-[10px]">REAL_ORDERS_ALLOWED</code> is false (paper soak safety).
            Cards showing <span className="font-mono text-foreground">BLOCKED</span> are separate (freshness, Engine C, or no setup) — open a card for details, or use the &quot;All states&quot; filter.
          </span>
        </div>
      )}

      {error && <ErrorBanner message={error} onRetry={fetchSnap} />}

      <ConfidenceCalibrationPanel surface="marcus" lookbackDays={30} />

      {showSessionHeat && sessionHeatSummary?.enabled && (
        <div className="shrink-0">
          <SessionHeatChart
            currentSessionKey={sessionHeatCurrentKey}
            defaultScoreGroup={selectedScoreGroup}
          />
        </div>
      )}

      {/* Two-column layout */}
      <div className="flex-1 grid grid-cols-7 gap-3 overflow-hidden min-h-0">
        {/* Left: card grid + events */}
        <div className="col-span-3 flex flex-col gap-2 overflow-hidden min-h-0 h-full">
          <div className="flex items-center justify-between px-1 shrink-0">
            <span className="text-[10px] uppercase tracking-widest text-muted-foreground font-semibold">
              {filter === 'tradeable' ? 'Tradeable' : 'Signals'}
              <span className="ml-1 text-foreground">{filtered.length}</span>
              {filtered.length !== symbols.length && (
                <span className="ml-1 opacity-60">/ {symbols.length}</span>
              )}
            </span>
            {forexBMode && (
              <Badge className="text-[9px] bg-primary/15 text-primary gap-1">
                <Layers className="w-3 h-3" /> Engine B ranked
              </Badge>
            )}
          </div>
          <ScrollArea className="flex-1 min-h-0 pr-2">
            <div className="grid grid-cols-1 gap-2">
              {filtered.length === 0 ? (
                <Card className="border-border/60 bg-card/50 border-dashed">
                  <CardContent className="p-6 text-center text-xs text-muted-foreground space-y-2">
                    {loading ? (
                      'Loading…'
                    ) : filter === 'tradeable' ? (
                      <>
                        <p className="text-foreground">
                          None of the {symbols.length} loaded symbol{symbols.length === 1 ? '' : 's'} reached a
                          tradeable decision.
                        </p>
                        <p>
                          Run a scan to find candidates, or switch to{' '}
                          <button
                            type="button"
                            className="underline underline-offset-2 hover:text-foreground"
                            onClick={() => setFilter('all')}
                          >
                            All states
                          </button>{' '}
                          to see why each one was rejected.
                        </p>
                      </>
                    ) : (
                      'No symbols match this filter.'
                    )}
                  </CardContent>
                </Card>
              ) : (
                filtered.map((row, idx) => (
                  <CockpitCard
                    key={row.symbol}
                    row={row}
                    rank={forexBMode ? idx + 1 : undefined}
                    active={selected === row.symbol}
                    preferB={forexBMode}
                    onClick={() => setSelected(row.symbol)}
                    onOpenChart={() => openOnChart(row)}
                  />
                ))
              )}
            </div>
          </ScrollArea>
          {/* Event feed */}
          <Card className="border-border/60 bg-card/50 shrink min-h-0 max-h-[38%] flex flex-col gap-0 py-3">
            <CardHeader className="pb-2 shrink-0">
              <CardTitle className="panel-title flex items-center gap-2">
                <Activity className="w-3.5 h-3.5 text-primary" /> Event Feed
                {events.length > 0 && <span className="text-[9px] text-muted-foreground font-normal">({events.length})</span>}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-3 pt-0 flex-1 min-h-0">
              <ScrollArea className="h-full">
                {events.length === 0 ? (
                  <p className="text-[11px] text-muted-foreground">No events yet.</p>
                ) : (
                  <div className="space-y-1">
                    {events
                      .slice()
                      .reverse()
                      .slice(0, 30)
                      .map((e, i) => (
                        <div
                          key={`${e.timestamp}-${i}`}
                          className={cn(
                            'p-2 rounded-md text-[10px] font-mono flex items-start gap-2',
                            e.severity === 'pass'
                              ? 'bg-long/10 text-long'
                              : e.severity === 'block'
                                ? 'bg-short/10 text-short'
                                : 'bg-warning/10 text-warning',
                          )}
                        >
                          <span className="opacity-70">{new Date(e.timestamp).toLocaleTimeString()}</span>
                          <span className="font-bold">{e.symbol}</span>
                          <span className="flex-1">{e.message}</span>
                        </div>
                      ))}
                  </div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        {/* Right: detail tabs */}
        <Card className="col-span-4 border-border/60 bg-card/50 flex flex-col overflow-hidden min-h-0 h-full">
          {selectedRow ? (
            <CockpitDetail row={selectedRow} onPaperExecute={onPaperExecute} executing={papering} onOpenChart={() => openOnChart(selectedRow)} />
          ) : (
            <CardContent className="flex flex-col items-center justify-center h-full p-12 text-center text-muted-foreground gap-2">
              <Radio className="w-8 h-8 opacity-30" />
              <span className="text-sm">Select a signal to inspect</span>
              <span className="text-[11px] opacity-70">Engine A/B/C/D breakdown, levels, AI review, and the paper-execute gate appear here.</span>
            </CardContent>
          )}
        </Card>
      </div>
    </div>
  );
}

function buildAgentSignalPayload(row: LdSymbolRow): AiTradeChatSignalPayload {
  const direction = row.engineA?.direction || row.engineB?.direction || null;
  const engine = row.engineA?.passed
    ? 'engine_a'
    : row.engineB?.confidencePassed
      ? 'engine_b'
      : row.engineC?.decisionState === 'ALIGNED'
        ? 'engine_c'
        : row.engineD?.gateResult === 'PASS'
          ? 'engine_d'
          : 'engine_unknown';
  const score = row.engineA?.score ?? row.engineB?.score ?? row.engineD?.score ?? null;
  const threshold = row.engineA?.threshold ?? row.engineB?.threshold ?? null;
  const rr = row.levels?.rr ?? row.engineA?.rr ?? row.engineD?.rr ?? null;
  const entry = row.levels?.entry ?? row.engineA?.entry ?? row.engineB?.entry ?? null;
  const sl = row.levels?.sl ?? row.engineA?.sl ?? row.engineB?.sl ?? null;
  const tp = row.levels?.tp ?? row.levels?.tp1 ?? row.engineA?.tp ?? row.engineB?.tp ?? null;
  return {
    trace_id: row.traceId ?? null,
    symbol: row.symbol,
    pair: row.symbol,
    display: row.symbol,
    type: row.asset_type ?? null,
    asset_type: row.asset_type ?? null,
    direction,
    engine,
    engine_source: engine,
    style: row.engineD?.setupType ?? null,
    timeframe: row.timeframe ?? null,
    score,
    threshold,
    rr,
    rr1: rr,
    min_rr: null,
    entry,
    price: entry,
    sl,
    tp,
    tp1: tp,
    tp2: row.levels?.tp2 ?? null,
    latest_price: row.latest_price ?? null,
    spread: row.spread ?? null,
    spread_pips: row.spread ?? null,
    state: row.finalState ?? null,
    finalState: row.finalState ?? null,
    mainReason: row.mainReason ?? null,
    blockReason: row.blockReason ?? null,
    engine_a: row.engineA ?? null,
    engine_b: row.engineB ?? null,
    engine_c: row.engineC ?? null,
    engine_d: row.engineD ?? null,
    vision: row.aiReview?.chartVision ?? null,
    ai_review: row.aiReview ?? null,
    dataFreshness: row.freshness ?? null,
    freshness_status: row.freshness?.consistencyStatus ?? row.freshness?.policyStatus ?? null,
    levels: row.levels ?? null,
  };
}

function connBg(state?: string): string {
  if (state === 'connected' || state === 'live') return 'bg-long/20 text-long';
  if (state === 'error') return 'bg-short/20 text-short';
  return 'bg-muted/40 text-muted-foreground';
}

function CockpitCard({
  row,
  rank,
  active,
  preferB,
  onClick,
  onOpenChart,
}: {
  row: LdSymbolRow;
  rank?: number;
  active: boolean;
  preferB?: boolean;
  onClick: () => void;
  onOpenChart: () => void;
}) {
  const finalBg =
    row.finalState === 'PAPER CANDIDATE'
      ? 'bg-long/15 text-long'
      : row.finalState === 'WATCHLIST'
        ? 'bg-warning/15 text-warning'
        : row.finalState === 'BLOCKED'
          ? 'bg-short/15 text-short'
          : 'bg-muted/40 text-muted-foreground';
  const dir = preferB
    ? row.engineB?.direction || row.engineA?.direction
    : row.engineA?.direction || row.engineB?.direction;
  const dirBg = dir === 'LONG' ? 'bg-long/20 text-long' : dir === 'SHORT' ? 'bg-short/20 text-short' : 'bg-muted/40 text-muted-foreground';
  const bPass = Boolean(row.engineB?.confidencePassed || row.engineB?.structuralVerdict === 'CLEAR');
  return (
    <Card
      className={cn(
        'border-border/60 bg-card/50 hover:border-primary/30 transition-colors cursor-pointer relative overflow-hidden',
        active && 'border-primary/60 ring-1 ring-primary/30',
      )}
      onClick={onClick}
    >
      {preferB && bPass && <span className="absolute left-0 top-0 bottom-0 w-0.5 bg-primary" />}
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            {rank != null && (
              <span className="text-[10px] font-mono text-muted-foreground tabular-nums w-4 shrink-0">#{rank}</span>
            )}
            <span className="text-sm font-mono font-bold truncate">{row.symbol}</span>
            {dir && <Badge className={cn('text-[10px]', dirBg)}>{dir}</Badge>}
            {row.asset_type && (
              <Badge variant="outline" className="text-[9px] uppercase">
                {row.asset_type}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1">
            <Badge className={cn('text-[10px]', finalBg)}>{row.finalState}</Badge>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-1.5 text-[10px] gap-1"
              title="Open on TV Chart for AI review"
              onClick={(e) => {
                e.stopPropagation();
                onOpenChart();
              }}
            >
              <LineChart className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 text-xs">
          <Tile label="Price" value={fmtPrice(row.latest_price, row.symbol, row.asset_type || undefined)} />
          <Tile
            label="Engine A"
            title="Factor score vs threshold only. Scan trade tier does not require Engine B; see Engine C for consensus."
            value={
              row.engineA?.score != null
                ? `${fmtNum(row.engineA.score, 2)} / ${fmtNum(row.engineA.maxScore, 2)}`
                : '—'
            }
            accent={row.engineA?.passed ? 'long' : 'muted'}
          />
          <Tile
            label="Engine B"
            value={row.engineB?.confidencePassed ? 'PASS' : row.engineB?.structuralVerdict || '—'}
            accent={row.engineB?.confidencePassed ? 'long' : 'muted'}
          />
        </div>

        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>
            Engine C:{' '}
            <span className="text-foreground font-mono">{row.engineC?.decisionState || '—'}</span>
            {row.engineC?.tier && <span className="ml-1">[{row.engineC.tier}]</span>}
          </span>
          <span>
            Engine D: <span className="text-foreground font-mono">{row.engineD?.gateResult || '—'}</span>
            {row.engineD?.grade && <span className="ml-1">[{row.engineD.grade}]</span>}
          </span>
        </div>

        {row.sessionHeat?.available && row.sessionHeat.primary && (
          <div className="flex items-center gap-1.5">
            <SessionHeatPill
              label={`${row.sessionHeat.primary.label} · ${row.sessionHeat.primaryEngine}`}
              tone={row.sessionHeat.primary.tone}
              title={row.sessionHeat.primary.tooltip}
            />
            {row.sessionHeat.primary.scope === 'engine' && (
              <span
                className="text-[9px] text-muted-foreground"
                title="This score group has too few recorded trades in this session; the engine-wide read is shown instead."
              >
                engine-wide
              </span>
            )}
          </div>
        )}

        {row.freshness?.gateDecision !== 'ALLOW' && (
          <div className="flex items-center gap-1 text-[10px] text-warning">
            <AlertTriangle className="w-3 h-3" />
            Freshness: {row.freshness?.consistencyStatus || row.freshness?.blockReason || 'BLOCK'}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CockpitDetail({
  row,
  onPaperExecute,
  executing,
  onOpenChart,
}: {
  row: LdSymbolRow;
  onPaperExecute: (row: LdSymbolRow) => void;
  executing: boolean;
  onOpenChart: () => void;
}) {
  const [activeTab, setActiveTab] = useState('overview');

  return (
    <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col overflow-hidden">
      <CardHeader className="pb-1 shrink-0">
        <div className="flex items-center justify-between">
          <CardTitle className="panel-title flex items-center gap-2">
            <Radio className="w-4 h-4 text-primary" /> {row.symbol}
            <Badge variant="outline" className="text-[10px]">
              {row.asset_type || '—'}
            </Badge>
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono">{fmtPrice(row.latest_price, row.symbol, row.asset_type || undefined)}</span>
            {row.spread != null && (
              <span className="text-[10px] text-muted-foreground">spread {fmtNum(row.spread, 4)}</span>
            )}
            <Button type="button" size="sm" variant="outline" className="h-7 gap-1 text-[10px]" onClick={onOpenChart}>
              <LineChart className="w-3.5 h-3.5" />
              Open &amp; Review
            </Button>
            <Button type="button" size="sm" variant="outline" className="h-7 gap-1 text-[10px]" onClick={() => setActiveTab('agent')}>
              <Bot className="w-3.5 h-3.5" />
              Discuss with AI
            </Button>
          </div>
        </div>
        <TabsList className="w-full justify-start mt-2">
          <TabsTrigger value="overview" className="text-[11px]">Overview</TabsTrigger>
          <TabsTrigger value="levels" className="text-[11px]">Levels</TabsTrigger>
          <TabsTrigger value="engineA" className="text-[11px]">Engine A</TabsTrigger>
          <TabsTrigger value="engineB" className="text-[11px]">Engine B</TabsTrigger>
          <TabsTrigger value="engineC" className="text-[11px]">Engine C</TabsTrigger>
          <TabsTrigger value="engineD" className="text-[11px]">Engine D</TabsTrigger>
          <TabsTrigger value="ai" className="text-[11px]">AI</TabsTrigger>
          <TabsTrigger value="agent" className="text-[11px]">Agent</TabsTrigger>
          <TabsTrigger value="execute" className="text-[11px]">Execute</TabsTrigger>
          <TabsTrigger value="diagnostics" className="text-[11px]">Diagnostics</TabsTrigger>
        </TabsList>
      </CardHeader>

      <ScrollArea className="flex-1 min-h-0 px-4 pb-4">
        <TabsContent value="overview" className="m-0 mt-2 space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Tile label="Final state" value={row.finalState} />
            <Tile label="Main reason" value={row.mainReason || '—'} />
            <Tile label="Bid" value={fmtPrice(row.bid, row.symbol, row.asset_type || undefined)} />
            <Tile label="Ask" value={fmtPrice(row.ask, row.symbol, row.asset_type || undefined)} />
            <Tile label="Change %" value={row.change_pct != null ? `${fmtNum(row.change_pct, 2)}%` : '—'} />
            <Tile label="Source" value={row.source || '—'} />
            <Tile label="Timeframe" value={row.timeframe} />
            <Tile label="Engine C state" value={row.engineC?.decisionState || '—'} />
          </div>

          <SessionContextCard heat={row.sessionHeat} />

          <FreshnessCard freshness={row.freshness} />
        </TabsContent>

        <TabsContent value="levels" className="m-0 mt-2 space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <Tile label="Entry" value={fmtPrice(row.levels?.entry, row.symbol, row.asset_type || undefined)} />
            <Tile label="SL" value={fmtPrice(row.levels?.sl, row.symbol, row.asset_type || undefined)} accent="short" />
            <Tile label="TP1" value={fmtPrice(row.levels?.tp1 ?? row.levels?.tp, row.symbol, row.asset_type || undefined)} accent="long" />
            <Tile label="TP2" value={fmtPrice(row.levels?.tp2, row.symbol, row.asset_type || undefined)} accent="long" />
            <Tile label="R:R" value={fmtNum(row.levels?.rr, 2)} />
            <Tile label="Source" value={row.levels?.source || '—'} />
          </div>
          {row.engineD?.vp && (
            <div className="grid grid-cols-3 gap-2">
              <Tile label="VP VAH" value={fmtPrice(row.engineD.vp.vah, row.symbol, row.asset_type || undefined)} />
              <Tile label="VP POC" value={fmtPrice(row.engineD.vp.poc, row.symbol, row.asset_type || undefined)} />
              <Tile label="VP VAL" value={fmtPrice(row.engineD.vp.val, row.symbol, row.asset_type || undefined)} />
            </div>
          )}
        </TabsContent>

        <TabsContent value="engineA" className="m-0 mt-2">
          <EngineARowCard row={row.engineA} pair={row.symbol} type={row.asset_type || undefined} />
        </TabsContent>

        <TabsContent value="engineB" className="m-0 mt-2">
          <EngineBRowCard row={row.engineB} pair={row.symbol} type={row.asset_type || undefined} />
        </TabsContent>

        <TabsContent value="engineC" className="m-0 mt-2">
          <EngineCRowCard row={row.engineC} />
        </TabsContent>

        <TabsContent value="engineD" className="m-0 mt-2">
          <EngineDRowCard row={row.engineD} pair={row.symbol} type={row.asset_type || undefined} />
        </TabsContent>

        <TabsContent value="ai" className="m-0 mt-2">
          <AiReviewCard ai={row.aiReview} />
        </TabsContent>

        <TabsContent value="agent" className="m-0 mt-2">
          <AITradingAgentPanel
            symbol={row.symbol}
            traceId={row.traceId}
            signal={buildAgentSignalPayload(row)}
            seedMessage="Review this trade. What supports it, what argues against it, and what would confirm or invalidate it?"
          />
        </TabsContent>

        <TabsContent value="execute" className="m-0 mt-2 space-y-3">
          <ExecuteCard row={row} onPaperExecute={onPaperExecute} executing={executing} />
        </TabsContent>

        <TabsContent value="diagnostics" className="m-0 mt-2 space-y-3">
          <Card className="border-border/60 bg-card/50">
            <CardContent className="p-3 space-y-2">
              <p className="text-[10px] uppercase text-muted-foreground">Executable State</p>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <Detail label="Can paper execute" value={row.executableState?.canPaperExecute ? 'YES' : 'NO'} />
                <Detail label="Can real execute" value={row.executableState?.canRealExecute ? 'YES' : 'NO'} />
                <Detail label="Disabled reason" value={row.executableState?.disabledReason || '—'} />
                <Detail label="Risk status" value={row.executableState?.riskStatus || '—'} />
                <Detail label="Freshness status" value={row.executableState?.freshnessStatus || '—'} />
                <Detail label="Paper mode" value={row.executableState?.paperMode ? 'ON' : 'OFF'} />
              </div>
            </CardContent>
          </Card>
          <Card className="border-border/60 bg-card/50">
            <CardContent className="p-3 space-y-2">
              <p className="text-[10px] uppercase text-muted-foreground">Final State</p>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <Detail label="Final" value={row.finalState} />
                <Detail label="Main reason" value={row.mainReason || '—'} />
                <Detail label="Block reason" value={row.blockReason || '—'} />
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </ScrollArea>
    </Tabs>
  );
}

function FreshnessCard({ freshness }: { freshness: LdSymbolRow['freshness'] }) {
  if (!freshness) return null;
  const ok = freshness.gateDecision === 'ALLOW';
  return (
    <Card className={cn('border', ok ? 'border-long/40 bg-long/5' : 'border-warning/40 bg-warning/5')}>
      <CardContent className="p-3 space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            {ok ? <Check className="w-3.5 h-3.5 text-long" /> : <AlertTriangle className="w-3.5 h-3.5 text-warning" />}
            Data freshness
          </span>
          <Badge variant="outline" className="text-[10px]">
            {freshness.gateDecision || '—'}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs mt-1">
          <Detail label="Consistency" value={freshness.consistencyStatus || '—'} />
          <Detail label="Policy" value={freshness.policyStatus || '—'} />
          {freshness.blockReason && <Detail label="Block reason" value={freshness.blockReason} />}
        </div>
      </CardContent>
    </Card>
  );
}

function EngineARowCard({ row, pair, type }: { row: LdEngineARow; pair?: string; type?: string }) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            <Zap className="w-3.5 h-3.5 text-primary" /> Engine A
          </span>
          <Badge className={row.passed ? 'badge-long' : 'badge-short'}>{row.passed ? 'PASS' : 'FAIL'}</Badge>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail label="Score" value={`${fmtNum(row.score, 2)} / ${fmtNum(row.maxScore, 2)}`} />
          <Detail label="Threshold" value={fmtNum(row.threshold, 2)} />
          <Detail label="Direction" value={row.direction || '—'} />
          <Detail label="Trend" value={fmtNum(row.trendScore, 2)} />
          <Detail label="Momentum" value={fmtNum(row.momentumScore, 2)} />
          <Detail label="Addon" value={fmtNum(row.addonScore, 2)} />
          <Detail label="ADX" value={fmtNum(row.adxValue, 1)} />
          <Detail label="ADX gate" value={row.adxGate || '—'} />
          <Detail label="Session ×" value={fmtNum(row.sessionMultiplier, 2)} />
          <Detail label="Conviction" value={fmtNum(row.conviction, 2)} />
          <Detail label="Entry" value={fmtPrice(row.entry, pair, type)} />
          <Detail label="SL" value={fmtPrice(row.sl, pair, type)} accent="short" />
          <Detail label="TP" value={fmtPrice(row.tp ?? row.tp1, pair, type)} accent="long" />
          <Detail label="R:R" value={fmtNum(row.rr, 2)} />
        </div>
        {row.failReasons && row.failReasons.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {row.failReasons.slice(0, 8).map((r) => (
              <Badge key={r} variant="outline" className="text-[9px] text-warning border-warning/40">
                {r}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function EngineBRowCard({ row, pair, type }: { row: LdEngineBRow; pair?: string; type?: string }) {
  const gates = readEngineBCanonicalGatesFromRow(row)!;
  const bBreakdown = engineBScoreBreakdown(row as unknown as Record<string, unknown>);
  const confidenceBadgeClass =
    gates.confidenceDisplayLabel === 'CONFIDENCE PASSED'
      ? 'badge-long'
      : gates.confidenceDisplayLabel === 'SCORE PASSED / GATE FAILED'
        ? 'bg-warning/20 text-warning'
        : 'badge-short';

  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <span className="text-xs font-semibold flex items-center gap-1">
            <Layers className="w-3.5 h-3.5 text-primary" /> Engine B
          </span>
          <div className="flex items-center gap-1">
            <Badge className={confidenceBadgeClass}>
              {gates.confidenceDisplayLabel}
            </Badge>
            {!gates.canonicalTradeOk && (
              <Badge variant="outline" className="text-[10px] bg-short/10 text-short border-short/30">
                NO ENTRY
              </Badge>
            )}
            {gates.canonicalStatus && (
              <Badge
                variant="outline"
                className={cn(
                  'text-[10px]',
                  gates.canonicalTradeOk ? 'bg-long/10 text-long border-long/30' : 'bg-short/10 text-short border-short/30',
                )}
              >
                {gates.canonicalStatus}
              </Badge>
            )}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Detail
            label="Gate"
            value={
              bBreakdown?.gateScore != null && bBreakdown?.gateMax != null
                ? `${fmtNum(bBreakdown.gateScore, 2)} / ${fmtNum(bBreakdown.gateMax, 2)} (min ${fmtNum(bBreakdown.minScore, 2)})`
                : `${fmtNum(row.score, 2)} / ${fmtNum(row.maxScore, 2)}`
            }
          />
          <Detail label="Total score" value={`${fmtNum(row.totalScore ?? row.score, 2)} / ${fmtNum(row.maxScore, 2)}`} />
          <Detail label="Direction" value={row.direction || '—'} />
          <Detail label="Verdict" value={row.structuralVerdict || '—'} />
          <Detail label="Data valid" value={row.structuralDataValid ? 'YES' : 'NO'} />
          <Detail label="No-trigger class" value={row.noTriggerClassification || '—'} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Gate label="Structure" ok={gates.canonicalStructureOk} />
          <Gate label="Location" ok={gates.canonicalLocationOk} />
          <Gate label="Entry" ok={gates.canonicalTriggerOk} />
          <Gate label="Room / RR" ok={gates.canonicalRoomRrOk} />
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail
            label="Entry"
            value={gates.suggestedLevelsExecutable ? fmtPrice(row.entry, pair, type) : '—'}
          />
          <Detail
            label="SL"
            value={gates.suggestedLevelsExecutable ? fmtPrice(row.sl, pair, type) : '—'}
            accent="short"
          />
          <Detail
            label="TP"
            value={gates.suggestedLevelsExecutable ? fmtPrice(row.tp, pair, type) : '—'}
            accent="long"
          />
        </div>
        {!gates.suggestedLevelsExecutable
          && (row.entry != null || row.sl != null || row.tp != null) && (
          <div className="text-[10px] text-muted-foreground border border-border/40 rounded-md p-2">
            <p className="uppercase font-semibold text-warning mb-1">Rejected diagnostic levels — not executable</p>
            <p className="font-mono">
              Entry {fmtPrice(row.entry, pair, type)}
              {' · '}SL {fmtPrice(row.sl, pair, type)}
              {' · '}TP {fmtPrice(row.tp, pair, type)}
            </p>
          </div>
        )}
        {gates.canonicalPrimaryRejectReason && (
          <ReasonList
            items={[gates.canonicalPrimaryRejectReason]}
            className="text-short bg-short/10"
            label="Primary reject"
            icon={<X className="w-3 h-3" />}
          />
        )}
        {row.hardFailReasons.length > 0 && (
          <ReasonList items={row.hardFailReasons} className="text-short bg-short/10" label="Hard fail" icon={<X className="w-3 h-3" />} />
        )}
        {row.softWarnings.length > 0 && (
          <ReasonList items={row.softWarnings} className="text-warning bg-warning/10" label="Soft warning" icon={<AlertTriangle className="w-3 h-3" />} />
        )}
        {row.diagnosticNotes.length > 0 && (
          <ReasonList items={row.diagnosticNotes} className="text-muted-foreground bg-muted/30" label="Diagnostic" icon={<Info className="w-3 h-3" />} />
        )}
      </CardContent>
    </Card>
  );
}

function EngineCRowCard({ row }: { row: LdEngineCRow }) {
  const stateBg =
    row.decisionState === 'ALIGNED'
      ? 'bg-long/20 text-long'
      : row.decisionState === 'CONFLICT' || row.decisionState === 'BLOCKED'
        ? 'bg-short/20 text-short'
        : row.decisionState === 'A_ONLY' || row.decisionState === 'B_ONLY' || row.decisionState === 'WATCHLIST'
          ? 'bg-warning/20 text-warning'
          : 'bg-muted/40 text-muted-foreground';
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            <GitMerge className="w-3.5 h-3.5 text-primary" /> Engine C Consensus
          </span>
          <Badge className={cn('text-[10px]', stateBg)}>{row.decisionState}</Badge>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail label="Consensus" value={row.consensusType || '—'} />
          <Detail label="Tier" value={row.tier || '—'} />
          <Detail label="Conviction" value={fmtNum(row.conviction, 2)} />
          <Detail label="Engine A score" value={fmtNum(row.engineAContribution, 2)} />
          <Detail label="Engine B score" value={fmtNum(row.engineBContribution, 2)} />
          <Detail label="B passed" value={row.engineBChecklistPassed ? 'YES' : 'NO'} />
          <Detail label="Trade" value={row.trade ? 'YES' : 'NO'} />
          <Detail label="Watchlist reason" value={row.watchlistReason || '—'} />
          <Detail label="Block reason" value={row.blockReason || '—'} />
        </div>
        {row.reason && <p className="text-[11px] text-muted-foreground">{row.reason}</p>}
      </CardContent>
    </Card>
  );
}

function EngineDRowCard({ row, pair, type }: { row: LdEngineDRow; pair?: string; type?: string }) {
  const gateBg =
    row.gateResult === 'PASS'
      ? 'bg-long/20 text-long'
      : row.gateResult === 'WATCHLIST'
        ? 'bg-warning/20 text-warning'
        : row.gateResult === 'BLOCKED'
          ? 'bg-short/20 text-short'
          : 'bg-muted/40 text-muted-foreground';
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            <TrendingUp className="w-3.5 h-3.5 text-primary" /> Engine D (Scalp Lab)
          </span>
          <div className="flex items-center gap-1">
            <Badge className={cn('text-[10px]', gateBg)}>{row.gateResult}</Badge>
            {row.grade && <Badge variant="outline" className="text-[10px]">Grade {row.grade}</Badge>}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail label="Score" value={fmtNum(row.score, 0)} />
          <Detail label="Setup" value={row.setupType || '—'} />
          <Detail label="Direction" value={row.direction || '—'} />
          <Detail label="Spread" value={fmtNum(row.spread, 2)} />
          <Detail label="R:R" value={fmtNum(row.rr, 2)} />
          <Detail label="CVD bias" value={row.cvdBias || '—'} />
          <Detail label="Absorption" value={row.absorptionDetected ? 'YES' : 'NO'} />
          <Detail label="VP VAH" value={fmtPrice(row.vp.vah, pair, type)} />
          <Detail label="VP POC" value={fmtPrice(row.vp.poc, pair, type)} />
          <Detail label="VP VAL" value={fmtPrice(row.vp.val, pair, type)} />
        </div>
        {row.failReasons.length > 0 && (
          <ReasonList items={row.failReasons} className="text-short bg-short/10" label="Fail" icon={<X className="w-3 h-3" />} />
        )}
        {row.missingData.length > 0 && (
          <ReasonList items={row.missingData} className="text-muted-foreground bg-muted/30" label="Missing data" icon={<Info className="w-3 h-3" />} />
        )}
      </CardContent>
    </Card>
  );
}

function AiReviewCard({ ai }: { ai: LdAiReview | undefined }) {
  if (!ai) return null;
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            <Eye className="w-3.5 h-3.5 text-primary" /> AI Review
          </span>
          <Badge variant="outline" className="text-[10px]">
            {ai.reviewState || 'NOT_VERIFIED'}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Detail label="Confidence" value={ai.confidence != null ? `${toNum(ai.confidence).toFixed(0)}%` : '—'} />
          <Detail label="Downgrade only" value={ai.downgradeOnly ? 'YES' : 'NO'} />
          <Detail label="Marcus Reid" value={ai.marcusReid != null ? 'present' : '—'} />
          <Detail label="Engine B AI" value={ai.engineBAI != null ? 'present' : '—'} />
          <Detail label="Signal Debate" value={ai.signalDebate != null ? 'present' : '—'} />
          <Detail label="Chart Vision" value={ai.chartVision != null ? 'present' : '—'} />
        </div>
        {Array.isArray(ai.contradictions) && ai.contradictions.length > 0 && (
          <ReasonList items={ai.contradictions} className="text-warning bg-warning/10" label="Contradictions" icon={<AlertTriangle className="w-3 h-3" />} />
        )}
        {Array.isArray(ai.missingInformation) && ai.missingInformation.length > 0 && (
          <ReasonList items={ai.missingInformation} className="text-muted-foreground bg-muted/30" label="Missing info" icon={<Info className="w-3 h-3" />} />
        )}
      </CardContent>
    </Card>
  );
}

function ExecuteCard({
  row,
  onPaperExecute,
  executing,
}: {
  row: LdSymbolRow;
  onPaperExecute: (row: LdSymbolRow) => void;
  executing: boolean;
}) {
  const ex = row.executableState;
  const canPaper = !!ex?.canPaperExecute;

  const freshnessOk = ex?.freshnessStatus === 'ALLOW';
  const paperEnabled = !!ex?.paperMode;
  const cState = row.engineC?.decisionState || 'NO_SETUP';
  const dGate = row.engineD?.gateResult || 'DATA_MISSING';
  const engineOk = cState === 'ALIGNED' || dGate === 'PASS';
  const hasLevels = !!(
    row.levels?.entry && row.levels?.sl && (row.levels?.tp || row.levels?.tp1) && (row.levels?.rr ?? 0) > 0
  );

  const GateRow = ({ label, pass, detail }: { label: string; pass: boolean; detail?: string }) => (
    <div className="flex items-center gap-2 text-[11px]">
      {pass
        ? <Check className="w-3.5 h-3.5 text-long shrink-0" />
        : <X className="w-3.5 h-3.5 text-short shrink-0" />}
      <span className={pass ? 'text-foreground' : 'text-muted-foreground'}>{label}</span>
      {detail && <span className="ml-auto font-mono text-[10px] text-muted-foreground">{detail}</span>}
    </div>
  );

  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-4 space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold flex items-center gap-2">
            <Play className="w-4 h-4 text-primary" /> Paper Execute
          </span>
          <Badge
            variant="outline"
            className={cn('text-[10px]', canPaper ? 'border-long/50 text-long' : 'border-border/40 text-muted-foreground')}
          >
            {row.finalState}
          </Badge>
        </div>

        {/* Gate checklist — shows exactly what's blocking */}
        <div className="space-y-1.5 rounded-md border border-border/40 p-3 bg-muted/10">
          <p className="text-[9px] uppercase font-semibold text-muted-foreground tracking-widest mb-2">Gate checklist</p>
          <GateRow label="Paper mode enabled" pass={paperEnabled} detail={paperEnabled ? 'ON' : 'OFF'} />
          <GateRow label="Freshness gate" pass={freshnessOk} detail={ex?.freshnessStatus || 'BLOCK'} />
          <GateRow
            label="Engine C aligned or Engine D pass"
            pass={engineOk}
            detail={cState === 'ALIGNED' ? 'C:ALIGNED' : dGate === 'PASS' ? 'D:PASS' : `C:${cState} D:${dGate}`}
          />
          <GateRow label="Valid entry / SL / TP / R:R" pass={hasLevels} detail={hasLevels ? 'OK' : 'MISSING'} />
        </div>

        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail label="Direction" value={row.engineA?.direction || row.engineB?.direction || '—'} />
          <Detail label="Entry" value={fmtPrice(row.levels?.entry, row.symbol, row.asset_type || undefined)} />
          <Detail label="SL" value={fmtPrice(row.levels?.sl, row.symbol, row.asset_type || undefined)} accent="short" />
          <Detail label="TP" value={fmtPrice(row.levels?.tp ?? row.levels?.tp1, row.symbol, row.asset_type || undefined)} accent="long" />
          <Detail label="R:R" value={fmtNum(row.levels?.rr, 2)} />
          <Detail label="Source" value={row.levels?.source || '—'} />
        </div>

        {!canPaper && ex?.disabledReason && (
          <div className="text-[11px] text-warning flex items-center gap-1.5 bg-warning/5 rounded px-2 py-1.5">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            <span className="font-mono">{ex.disabledReason}</span>
          </div>
        )}

        <Button size="sm" onClick={() => onPaperExecute(row)} disabled={!canPaper || executing} className="w-full">
          {executing ? 'Logging…' : canPaper ? 'Paper Execute' : 'Blocked — gates not met'}
        </Button>
      </CardContent>
    </Card>
  );
}

function ReasonList({
  items,
  className,
  label,
  icon,
}: {
  items: string[];
  className: string;
  label: string;
  icon: React.ReactNode;
}) {
  return (
    <div className={cn('p-2 rounded-md', className)}>
      <div className="flex items-center gap-1 text-[10px] uppercase font-semibold mb-1">
        {icon} {label}
      </div>
      <ul className="text-[10px] font-mono leading-relaxed list-disc pl-4">
        {items.slice(0, 6).map((it) => (
          <li key={it}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

/** Short word for a heat level, used in the compact header badge. */
function heatWord(heat?: string): string {
  if (heat === 'STRONG') return 'Strong';
  if (heat === 'WEAK') return 'Weak';
  if (heat === 'NEUTRAL') return 'Neutral';
  return 'n/a';
}

function heatWordClass(heat?: string): string {
  if (heat === 'STRONG') return 'text-long';
  if (heat === 'WEAK') return 'text-short';
  return 'text-muted-foreground';
}

/**
 * Session context for the selected symbol: how each engine has historically
 * performed in the session that is running right now, plus that engine's best
 * and worst session. Advisory — it never changes the execute gate.
 */
function SessionContextCard({ heat }: { heat?: LdSessionHeat }) {
  if (!heat) return null;

  if (!heat.available) {
    return (
      <div className="rounded-md border border-border/50 bg-muted/20 p-3">
        <p className="text-[10px] uppercase tracking-widest text-muted-foreground mb-1">Session context</p>
        <p className="text-[11px] text-muted-foreground">
          {heat.reason === 'disabled'
            ? 'Session Heat is disabled in config.'
            : 'Historical session data is still loading — it will appear on the next refresh.'}
        </p>
      </div>
    );
  }

  const rows: Array<[string, SessionHeatIndicator | undefined]> = [
    ['Engine A', heat.engineA],
    ['Engine B', heat.engineB],
  ];

  return (
    <div className="rounded-md border border-border/50 bg-muted/20 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] uppercase tracking-widest text-muted-foreground">
          Session context · {heat.sessionLabel}
        </p>
        {heat.scoreGroup && (
          <Badge variant="outline" className="text-[9px] font-mono">
            {heat.scoreGroup}
          </Badge>
        )}
      </div>

      {rows.map(([label, ind]) => (
        <div key={label} className="flex items-start justify-between gap-2">
          <span className="text-[11px] text-muted-foreground w-16 shrink-0">{label}</span>
          <div className="flex-1 min-w-0">
            {!ind || ind.heat === 'INSUFFICIENT' ? (
              <span className="text-[11px] text-muted-foreground" title={ind?.tooltip}>
                Insufficient sample ({ind?.trades ?? 0} trades) — no call
              </span>
            ) : (
              <>
                <div className="flex items-center gap-1.5 flex-wrap">
                  <SessionHeatPill label={ind.label} tone={ind.tone} title={ind.tooltip} />
                  <span className="text-[10px] font-mono text-muted-foreground">
                    n={ind.trades} · exp {ind.expectancyR != null ? `${ind.expectancyR >= 0 ? '+' : ''}${ind.expectancyR.toFixed(3)}R` : '—'}
                    {ind.profitFactor != null && ` · PF ${ind.profitFactor.toFixed(2)}`}
                    {ind.winRate != null && ` · WR ${(ind.winRate * 100).toFixed(1)}%`}
                  </span>
                </div>
                <p className="text-[9px] text-muted-foreground mt-0.5">
                  measured over {ind.scopeLabel}
                  {ind.bestSession && ` · best ${ind.bestSession}`}
                  {ind.worstSession && ` · worst ${ind.worstSession}`}
                </p>
              </>
            )}
          </div>
        </div>
      ))}

      <p className="text-[9px] text-muted-foreground border-t border-border/40 pt-1.5">
        From recorded backtest trade outcomes. Advisory context only — it does not gate, size, or approve
        execution.
      </p>
    </div>
  );
}

function Tile({
  label,
  value,
  accent,
  title,
}: {
  label: string;
  value: string;
  accent?: 'long' | 'short' | 'muted';
  title?: string;
}) {
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : 'text-foreground';
  return (
    <div className="p-2 rounded-md bg-muted/30" title={title}>
      <p className="text-[10px] uppercase text-muted-foreground">{label}</p>
      <p className={cn('text-xs font-mono font-bold truncate', fg)}>{value}</p>
    </div>
  );
}

function Detail({ label, value, accent }: { label: string; value: string; accent?: 'long' | 'short' }) {
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : 'text-foreground';
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[10px] text-muted-foreground capitalize truncate">{label}</span>
      <span className={cn('text-xs font-mono', fg)}>{value}</span>
    </div>
  );
}

function Gate({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className={cn('flex items-center gap-2 p-2 rounded-md', ok ? 'bg-long/10 text-long' : 'bg-short/10 text-short')}>
      {ok ? <Check className="w-3.5 h-3.5" /> : <X className="w-3.5 h-3.5" />}
      <span className="text-xs font-medium flex-1">{label}</span>
      <span className="text-[10px] font-mono">{ok ? 'OK' : 'FAIL'}</span>
    </div>
  );
}
