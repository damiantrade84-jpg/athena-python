import { useEffect, useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { ChevronDown, ChevronRight, Copy, Play, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import {
  G8_PAIRS,
  getForexTradingStatus,
  getLatestForexScan,
  postForexExecuteCandidate,
  postForexScan,
  type ExecuteCandidateData,
  type ForexScanData,
  type ForexTradeCandidate,
  type ForexTradingStatusData,
  parseSymbolList,
} from '@/lib/forexFactorApi';
import {
  DiagnosticsList,
  TabEmpty,
  TabShell,
  actionBadgeClass,
} from './shared';

function fmtNum(value?: number | null, digits = 4): string {
  if (value == null || !Number.isFinite(Number(value))) return '--';
  return Number(value).toFixed(digits);
}

function fmtPct(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return '--';
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function candidateScore(row: ForexTradeCandidate): number {
  return Object.values(row.family_scores || {}).reduce(
    (sum, score) => sum + Math.abs(Number(score?.points || 0)),
    0,
  );
}

function actionRowClass(row: ForexTradeCandidate): string {
  if (row.tradable_now && row.action === 'BUY') return 'border-l-2 border-l-emerald-500/80 bg-emerald-500/5';
  if (row.tradable_now && row.action === 'SELL') return 'border-l-2 border-l-red-500/80 bg-red-500/5';
  if (row.action === 'REDUCE' || row.action === 'FLATTEN') return 'border-l-2 border-l-amber-500/80 bg-amber-500/5';
  if (row.factor_eligible && !row.tradable_now) return 'border-l-2 border-l-amber-500/80 bg-amber-500/5';
  return 'opacity-70';
}

function familyGlyphClass(direction?: string | null): string {
  switch ((direction || '').toUpperCase()) {
    case 'LONG':
      return 'text-emerald-400';
    case 'SHORT':
      return 'text-red-400';
    default:
      return 'text-muted-foreground/50';
  }
}

function FamilyGlyphs({ row }: { row: ForexTradeCandidate }) {
  const families: Array<[string, string]> = [
    ['C', row.family_scores?.carry?.direction || ''],
    ['M', row.family_scores?.momentum?.direction || ''],
    ['V', row.family_scores?.value?.direction || ''],
  ];
  return (
    <span className="font-mono text-xs tracking-widest">
      {families.map(([letter, dir]) => (
        <span key={letter} className={familyGlyphClass(dir)} title={`${letter}: ${dir || 'neutral'}`}>
          {letter}
        </span>
      ))}
    </span>
  );
}

function GateDot({ label, result }: { label: string; result?: string }) {
  const r = (result || '').toLowerCase();
  const color = r === 'pass' ? 'bg-emerald-400' : r === 'fail' ? 'bg-red-400' : r === 'reduced' ? 'bg-amber-400' : 'bg-muted-foreground/40';
  return (
    <span className="inline-flex items-center gap-1" title={`${label}: ${result || 'unknown'}`}>
      <span className={`h-2 w-2 rounded-full ${color}`} />
      <span className="text-[10px] text-muted-foreground">{label}</span>
    </span>
  );
}

export default function ScannerTab() {
  const [symbolsText, setSymbolsText] = useState(G8_PAIRS.join(', '));
  const [includeBlocked, setIncludeBlocked] = useState(true);
  const [refreshDecisions, setRefreshDecisions] = useState(true);
  const [strictFactorData, setStrictFactorData] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [intervalMs, setIntervalMs] = useState(300000);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [scan, setScan] = useState<ForexScanData | null>(null);
  const [status, setStatus] = useState<ForexTradingStatusData | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [lastExecution, setLastExecution] = useState<ExecuteCandidateData | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const symbols = useMemo(() => parseSymbolList(symbolsText), [symbolsText]);
  const candidates = scan?.candidates || [];
  const selected = candidates.find((row) => row.symbol === selectedSymbol) || null;

  const refreshStatus = async () => {
    const statusResp = await getForexTradingStatus();
    setStatus(statusResp.data || null);
  };

  const loadLatest = async () => {
    setLoading(true);
    setError(null);
    try {
      await refreshStatus();
      const latest = await getLatestForexScan();
      if (latest.data && 'candidates' in latest.data) {
        setScan(latest.data as ForexScanData);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const runScan = async () => {
    setBusy(true);
    setError(null);
    try {
      await refreshStatus();
      const resp = await postForexScan({
        symbols,
        refreshDecisions,
        includeBlocked,
        strictFactorData,
        executionMode: 'paper',
      });
      setScan(resp.data || null);
      setSelectedSymbol(null);
      setLastExecution(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setLoading(false);
    }
  };

  const executeCandidate = async (candidate: ForexTradeCandidate, dryRun: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const resp = await postForexExecuteCandidate({ candidate, dryRun, confirm: true });
      setLastExecution(resp.data || null);
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const copyCandidate = async (candidate: ForexTradeCandidate) => {
    await navigator.clipboard?.writeText(JSON.stringify(candidate, null, 2));
  };

  useEffect(() => {
    void loadLatest();
  }, []);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = window.setInterval(() => void runScan(), intervalMs);
    return () => window.clearInterval(id);
  }, [autoRefresh, intervalMs, symbolsText, includeBlocked, refreshDecisions, strictFactorData]);

  const diagnostics = scan?.diagnostics || [];
  const warnings = [
    ...(scan?.warnings || []),
    ...((status?.block_reasons || []).map((reason) => `execution:${reason}`)),
  ];

  return (
    <TabShell loading={loading} error={error} diagnostics={diagnostics} warnings={warnings} onRefresh={loadLatest}>
      <div className="space-y-4">
        <div data-testid="fx-scanner-controls" className="flex flex-col gap-3 rounded-md border border-border/60 p-3">
          <div data-testid="fx-scanner-actions" className="flex w-full flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
            <Button className="w-full sm:w-auto" size="sm" disabled={busy || !symbols.length} onClick={runScan}>
              <Search className="h-3.5 w-3.5 mr-1" /> Scan Forex
            </Button>
            <Button className="w-full sm:w-auto" variant="outline" size="sm" disabled={busy} onClick={loadLatest}>
              <RefreshCw className="h-3.5 w-3.5 mr-1" /> Refresh latest scan
            </Button>
            <Badge variant="outline" className="text-[10px]">
              {status?.can_demo_execute ? 'Demo execution ready' : 'Execution off'}
            </Badge>
            <Badge variant="outline" className="text-[10px]">Mode {status?.executor_mode || 'paper'}</Badge>
            <Badge variant="outline" className="text-[10px]">{symbols.length} symbols</Badge>
            <button
              type="button"
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground sm:ml-auto"
              onClick={() => setSettingsOpen((v) => !v)}
            >
              {settingsOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              Scan settings
            </button>
          </div>

          {settingsOpen && (
            <div className="space-y-3 border-t border-border/60 pt-3">
              <label className="min-w-0 space-y-1 text-xs block">
                <span className="text-muted-foreground">Symbols</span>
                <textarea
                  className="min-h-20 w-full max-w-full resize-y rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
                  value={symbolsText}
                  onChange={(event) => setSymbolsText(event.target.value)}
                />
              </label>
              <div className="flex flex-wrap gap-4 text-xs items-center">
                <Button className="w-full sm:w-auto" variant="outline" size="sm" onClick={() => setSymbolsText(G8_PAIRS.join(', '))}>G8 preset</Button>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <Checkbox checked={includeBlocked} onCheckedChange={(v) => setIncludeBlocked(v === true)} />
                  Include blocked
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <Checkbox checked={refreshDecisions} onCheckedChange={(v) => setRefreshDecisions(v === true)} />
                  Refresh decisions before scan
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <Checkbox checked={strictFactorData} onCheckedChange={(v) => setStrictFactorData(v === true)} />
                  Strict factor data
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <Checkbox checked={autoRefresh} onCheckedChange={(v) => setAutoRefresh(v === true)} />
                  Auto-refresh
                </label>
                <select
                  className="rounded-md border border-input bg-background px-2 py-1 text-xs"
                  value={intervalMs}
                  onChange={(event) => setIntervalMs(Number(event.target.value))}
                  disabled={!autoRefresh}
                >
                  <option value={60000}>1 min</option>
                  <option value={300000}>5 min</option>
                  <option value={900000}>15 min</option>
                </select>
              </div>
              <div className="rounded-md border border-border/60 px-3 py-2 text-[11px] text-muted-foreground">
                <div className="font-medium text-foreground mb-1">Hard Gates</div>
                These gates are not score components. A failed volatility or cost gate blocks, reduces, or flattens exposure even when Carry/Momentum/Value are aligned.
                Factor decision = weekly/daily Carry, Momentum, and Value bias. Execution defaults to dry-run; demo orders require config gates.
              </div>
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2">
          {[
            ['Scanned', scan?.summary?.symbols_scanned],
            ['Tradable', scan?.summary?.tradable_count],
            ['BUY', scan?.summary?.buy_count],
            ['SELL', scan?.summary?.sell_count],
            ['Blocked', scan?.summary?.blocked_count],
            ['No trade', scan?.summary?.no_trade_count],
            ['Open positions', status?.open_positions?.length || 0],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-md border border-border/60 px-3 py-1.5">
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
              <div className="text-base font-mono leading-tight">{value ?? '--'}</div>
            </div>
          ))}
        </div>

        {!candidates.length ? (
          <TabEmpty message="No scan candidates yet. Click Scan Forex to generate factor-first trade candidates." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>Symbol</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Actions</TableHead>
                <TableHead className="text-right">Score</TableHead>
                <TableHead title="Carry / Momentum / Value alignment">Factors</TableHead>
                <TableHead>Gates</TableHead>
                <TableHead className="text-right">Entry</TableHead>
                <TableHead className="text-right">SL / TP1</TableHead>
                <TableHead className="text-right">RR</TableHead>
                <TableHead className="text-right">Spread / ATR</TableHead>
                <TableHead className="text-right">Weight</TableHead>
                <TableHead>Reason</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {candidates.map((row, index) => {
                const isSelected = selectedSymbol === row.symbol;
                return (
                  <TableRow
                    key={`${row.scan_id}-${row.symbol}-${index}`}
                    className={`${actionRowClass(row)} cursor-pointer ${isSelected ? 'ring-1 ring-inset ring-border' : ''}`}
                    onClick={() => setSelectedSymbol(isSelected ? null : (row.symbol || null))}
                  >
                    <TableCell className="font-mono text-xs">{index + 1}</TableCell>
                    <TableCell className="font-mono text-xs font-semibold">{row.symbol}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <Badge className={actionBadgeClass(row.action)}>{row.action}</Badge>
                        {row.direction && <span className="text-[10px] text-muted-foreground">{row.direction}</span>}
                      </div>
                    </TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <div className="flex flex-nowrap gap-1">
                        <Button variant="secondary" size="sm" disabled={busy || row.action === 'NO_TRADE'} onClick={() => executeCandidate(row, true)}>
                          <Play className="h-3.5 w-3.5 mr-1" /> Dry-run
                        </Button>
                        <Button size="sm" disabled={busy || !status?.can_demo_execute || !row.tradable_now} onClick={() => executeCandidate(row, false)}>
                          <ShieldCheck className="h-3.5 w-3.5 mr-1" /> Demo
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => copyCandidate(row)} title="Copy signal JSON">
                          <Copy className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">{fmtNum(candidateScore(row), 1)}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <FamilyGlyphs row={row} />
                        {row.cot_overlay?.action ? (
                          <span className="text-[10px] text-muted-foreground" title={`COT: ${String(row.cot_overlay.action)}`}>
                            COT
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-0.5">
                        <GateDot label="Vol" result={row.gate_results?.volatility?.result} />
                        <GateDot label="Cost" result={row.gate_results?.cost?.result} />
                      </div>
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">{fmtNum(row.entry)}</TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      <div>{fmtNum(row.sl)}</div>
                      <div className="text-muted-foreground">{fmtNum(row.tp1)}</div>
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">{fmtNum(row.rr1, 2)}</TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      <div>{fmtNum(row.spread_pips, 1)}</div>
                      <div className="text-muted-foreground">{fmtNum(row.atr_pips, 1)}</div>
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">{fmtPct(row.target_weight)}</TableCell>
                    <TableCell className="max-w-[220px] truncate text-xs text-muted-foreground" title={row.reason}>{row.reason || '--'}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}

        {selected ? (
          <div className="rounded-md border border-border/60 p-3 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm">{selected.symbol}</span>
              <Badge className={actionBadgeClass(selected.action)}>{selected.action}</Badge>
              <Badge variant="outline" className="text-[10px]">decision {selected.last_factor_decision_date || '--'}</Badge>
              <Badge variant="outline" className="text-[10px]">scan {scan?.generated_at || '--'}</Badge>
            </div>
            <div className="grid gap-3 md:grid-cols-3 text-[11px]">
              <div>
                <div className="font-medium text-foreground">Factor alignment</div>
                <div className="text-muted-foreground">
                  {selected.aligned_families?.length ? selected.aligned_families.join(' + ') : 'No primary family alignment'}
                </div>
              </div>
              <div>
                <div className="font-medium text-foreground">Hard gate result</div>
                <div className="text-muted-foreground">{selected.gate_summary || 'No gate summary'}</div>
              </div>
              <div>
                <div className="font-medium text-foreground">Final action</div>
                <div className="text-muted-foreground">{selected.action}: {selected.reason}</div>
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2 text-[11px]">
              <pre className="overflow-auto rounded-md bg-muted/40 p-2">{JSON.stringify({
                family_scores: selected.family_scores,
                cot_overlay: selected.cot_overlay,
                cost_diagnostics: selected.cost_diagnostics,
              }, null, 2)}</pre>
              <pre className="overflow-auto rounded-md bg-muted/40 p-2">{JSON.stringify({
                gate_results: selected.gate_results,
                existing_position: selected.existing_position,
                execution_preflight: selected.execution_preflight,
              }, null, 2)}</pre>
            </div>
            <DiagnosticsList items={selected.diagnostics} />
          </div>
        ) : null}

        {lastExecution ? (
          <div className="rounded-md border border-border/60 p-3 text-xs">
            <div className="font-medium mb-1">Last execution attempt</div>
            <pre className="overflow-auto bg-muted/40 p-2 rounded-md">{JSON.stringify(lastExecution, null, 2)}</pre>
          </div>
        ) : null}
      </div>
    </TabShell>
  );
}
