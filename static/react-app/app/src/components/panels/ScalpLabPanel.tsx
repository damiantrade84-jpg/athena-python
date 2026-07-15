import { useCallback, useEffect, useMemo, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { useLivePrices } from '@/hooks/useLivePrices';
import VolumeModeField from '@/components/execution/VolumeModeField';
import {
  fetchRiskPreview,
  formatRiskPreviewLine,
  useExecutionVolumeState,
} from '@/hooks/useExecutionVolumeState';
import { buildScalpExecutePayload, type ScalpExecutionMode } from '@/lib/manualExecuteHelpers';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { ErrorBanner } from '@/components/shared';
import { Activity, Play, Zap, AlertTriangle, Layers, Sparkles } from 'lucide-react';
import { fmtNum, toNum } from '@/lib/utils';
import { fmtLiveQuoteMeta, fmtPrice } from '@/lib/athenaFormat';

interface DataFidelity {
  vp_source?: string;
  vp_fidelity?: string;
  vp_is_proxy?: boolean;
  cvd_source?: string;
  cvd_fidelity?: string;
  cvd_is_proxy?: boolean;
  absorption_source?: string;
  absorption_fidelity?: string;
  absorption_is_proxy?: boolean;
  aggression_uses_real_order_flow?: boolean;
  notes?: string[];
}

interface AnchorCandidate {
  valid?: boolean;
  reason?: string;
  bars?: number;
  direction?: string;
  start_time?: string | null;
  end_time?: string | null;
  heuristic?: string;
}

interface ProfileAnchorShadow {
  report_only?: boolean;
  active_anchor?: AnchorCandidate & {
    mode?: string;
    lookback_bars?: number;
    volume_source?: string;
  };
  fixed_lookback_anchor?: AnchorCandidate & {
    mode?: string;
    lookback_bars?: number;
    volume_source?: string;
  };
  candidates?: Record<string, AnchorCandidate>;
}

/**
 * Engine D / Scalp Lab signal — server-normalised in athena.py::_scalp_ui_signal().
 * Note: `symbol` is null for MT5 pairs; key by `display||pair||symbol`.
 */
interface ScalpSignal {
  symbol?: string | null;
  pair?: string;
  display?: string;
  direction?: 'LONG' | 'SHORT' | string;
  type?: string;
  entry?: number;
  price?: number;
  sl?: number;
  tp1?: number;
  tp2?: number;
  rr?: number;
  rr1?: number;
  ai_grade?: 'A' | 'B' | 'C' | 'D' | string;
  ai_score?: number;
  ai_reasons?: string[];
  size_multiplier?: number;
  sl_method?: string;
  zone_type?: string;
  zone_level?: number;
  zone_desc?: string;
  trigger_desc?: string;
  trigger_type?: string;
  momentum_method?: string;
  market_state?: string;
  vp_poc?: number;
  vp_vah?: number;
  vp_val?: number;
  vp_lvn_count?: number;
  vp_volume_source?: string;
  vp_bucket_count?: number;
  vp_fidelity?: string;
  vp_is_proxy?: boolean;
  vp_uses_real_trade_buckets?: boolean;
  absorption_count?: number;
  absorption_source?: string;
  absorption_fidelity?: string;
  absorption_is_proxy?: boolean;
  cvd_direction?: string;
  cvd_slope?: number;
  cvd_source?: string;
  cvd_bucket_count?: number;
  cvd_fidelity?: string;
  cvd_is_proxy?: boolean;
  cvd_uses_real_trade_buckets?: boolean;
  aaa_complete?: boolean;
  aggression_source?: string;
  aggression_source_raw?: string;
  aggression_source_is_proxy?: boolean;
  aggression_confirmed?: boolean;
  aggression_uses_real_order_flow?: boolean;
  data_fidelity?: DataFidelity;
  strict_fabio_pass?: boolean;
  strict_fabio_reason?: string;
  strict_fabio_missing_pillars?: string[];
  strict_fabio_pillars?: Record<string, boolean>;
  current_vs_strict_status?: string;
  aggression_components?: Record<string, boolean>;
  profile_anchor_mode?: string;
  profile_anchor_bars?: number;
  profile_anchor_start?: string | null;
  profile_anchor_end?: string | null;
  profile_anchor_shadow?: ProfileAnchorShadow;
  vwap?: number;
  htf_bias?: string;
  htf_bias_tf?: string;
  spread_pips?: number;
  session?: string;
  execution_tf?: string;
  context_tf?: string;
  structure_tf?: string;
  gate_result?: string;
  executable?: boolean;
  candidate_status?: string;
  fail_reasons?: string[];
  soft_warnings?: string[];
  fee_guard?: Record<string, unknown>;
  advisory?: Record<string, unknown>;
  advisory_summary?: string;
  premarket_delta_cluster_type?: string;
  premarket_delta_proxy_levels?: Record<string, unknown>;
  engine?: string;
  timestamp?: string;
  risk_level?: string;
  rr_ok?: boolean;
  tp_partial?: number;
  [k: string]: unknown;
}

interface ScalpScanResponse {
  signals?: ScalpSignal[];
  skipped?: Array<{ pair?: string; display?: string; reason?: string; gate_result?: string; ai_grade?: string; ai_score?: number; fail_reasons?: string[] }>;
  scanned?: number;
  pairs?: string[];
  pair_count?: number;
  scan_mode?: 'fast' | 'full';
  session?: string;
  sessions_active?: string[];
  reason?: string;
  diagnostic?: boolean;
  diagnostic_summary?: Record<string, unknown>;
  pass_count?: number;
  candidate_count?: number;
  skip_count?: number;
  error?: string;
}

interface ScalpPairsResponse {
  pairs?: string[];
  count?: number;
  error?: string;
}

interface ScalpExecuteResponse {
  success?: boolean;
  ticket?: string;
  error?: string;
  newDirection?: string | string[];
  skipped?: Array<{ pair?: string; reason?: string; gate_result?: string }>;
  fresh_scan?: ScalpScanResponse;
}

function gradeColor(grade?: string): string {
  switch ((grade || 'D').toUpperCase()) {
    case 'A': return 'bg-long/20 text-long border-long/40';
    case 'B': return 'bg-blue-500/20 text-blue-400 border-blue-500/40';
    case 'C': return 'bg-warning/20 text-warning border-warning/40';
    case 'D': return 'bg-short/20 text-short border-short/40';
    default: return 'bg-muted text-muted-foreground';
  }
}

function gradeBorder(grade?: string): string {
  switch ((grade || 'D').toUpperCase()) {
    case 'A': return 'border-long/50';
    case 'B': return 'border-blue-500/50';
    case 'C': return 'border-warning/50';
    default: return 'border-short/50';
  }
}

function signalKey(s: Pick<ScalpSignal, 'display' | 'pair' | 'symbol'>): string {
  return String(s.display || s.pair || s.symbol || '').trim().toUpperCase();
}

function isSignalExecutable(sig: ScalpSignal): boolean {
  return sig.executable === true && String(sig.gate_result || '').toUpperCase() === 'PASS';
}

function canOpenWorkbench(sig: ScalpSignal): boolean {
  const symbol = String(sig.symbol || sig.pair || sig.display || '').trim();
  const direction = String(sig.direction || '').toUpperCase();
  return Boolean(symbol) && (direction === 'LONG' || direction === 'SHORT');
}

function invalidateScalpSignal(
  scan: ScalpScanResponse | null,
  symbol: string,
  reason: string,
  fresh?: ScalpScanResponse,
): ScalpScanResponse | null {
  if (!scan) return scan;
  const target = symbol.trim().toUpperCase();
  const skipped = fresh?.skipped || [];
  const freshRow = skipped.find((row) => signalKey(row) === target);
  const failReason = freshRow?.reason || reason || 'fresh_scan_no_setup';
  const signals = (scan.signals || []).map((sig) => {
    if (signalKey(sig) !== target) return sig;
    return {
      ...sig,
      executable: false,
      gate_result: 'NO_SETUP',
      candidate_status: failReason,
      fail_reasons: [failReason],
      soft_warnings: Array.from(new Set([...(sig.soft_warnings || []), 'fresh_execute_scan_invalidated_setup'])),
    };
  });
  return {
    ...scan,
    signals,
    skipped: skipped.length > 0 ? skipped : scan.skipped,
    skip_count: fresh?.skip_count ?? skipped.length ?? scan.skip_count,
    reason: fresh?.reason ?? scan.reason,
    diagnostic_summary: fresh?.diagnostic_summary ?? scan.diagnostic_summary,
  };
}

export default function ScalpLabPanel() {
  const {
    showToast,
    scalpLabScanCache,
    scalpLabSelectedCache,
    setScalpLabScanCache,
    setScalpLabSelectedCache,
    setActivePanel,
    setScalpWorkbenchIntent,
  } = useStore();
  const [diagnostic, setDiagnostic] = useState(false);
  const [confirmExec, setConfirmExec] = useState<ScalpSignal | null>(null);
  const {
    volumeMode,
    setVolumeMode,
    sizingOverride,
    setSizingOverride,
  } = useExecutionVolumeState('min_lot');
  const [riskPreviewLine, setRiskPreviewLine] = useState<string | null>(null);

  const { data: pairsData } = useApiPoll<ScalpPairsResponse>('/api/scalp-pairs', 0);
  const { data: health } = useApiPoll<{
    paper_mode?: boolean;
    scalp_execution_mode?: ScalpExecutionMode;
    scalp_execute_requires_ai_review?: boolean;
  }>('/api/health', 60_000);
  const { post: postScan, loading: scanning, error: scanError } = useApiPost<ScalpScanResponse>();
  const { post: postExecute, loading: executing } = useApiPost<ScalpExecuteResponse>();
  const { priceFor, ageSecFor, sourceFor } = useLivePrices();

  const universeCount = pairsData?.count ?? pairsData?.pairs?.length ?? 0;
  const scanResult = scalpLabScanCache as ScalpScanResponse | null;
  const selected = scalpLabSelectedCache as ScalpSignal | null;
  const scalpExecutionMode: ScalpExecutionMode = health?.scalp_execution_mode
    || (health?.paper_mode ? 'paper' : 'live_disabled');
  const requireAiReview = health?.scalp_execute_requires_ai_review !== false;
  const canDirectExecute = (
    (scalpExecutionMode === 'paper' || scalpExecutionMode === 'demo')
    && !requireAiReview
  );
  const executeEndpoint = scalpExecutionMode === 'paper'
    ? '/api/scalp-paper-execute'
    : '/api/scalp-execute';
  const directExecuteLabel = scalpExecutionMode === 'paper'
    ? (requireAiReview ? 'AI Review Required' : 'Paper Execute')
    : scalpExecutionMode === 'demo'
      ? (requireAiReview ? 'AI Review Required' : 'Demo Execute')
      : 'Direct Execute Unavailable';

  const openWorkbench = useCallback((sig: ScalpSignal, autoReview = false) => {
    setScalpLabSelectedCache(sig);
    const symbol = String(sig.symbol || sig.pair || sig.display || '').toUpperCase().trim();
    if (!symbol) {
      showToast('Cannot open workbench: missing symbol', 'error');
      return;
    }
    setScalpWorkbenchIntent({
      id: `scalp-${symbol}-${Date.now()}`,
      source: 'scalp_lab',
      symbol,
      signal: sig,
      preferredTf: 'M5',
      autoReview,
      createdAt: new Date().toISOString(),
    });
    setActivePanel('scalpWorkbench');
    showToast(
      autoReview
        ? `Opening ${sig.display || symbol} workbench for AI review`
        : `Opening ${sig.display || symbol} workbench`,
      'info',
    );
  }, [setActivePanel, setScalpLabSelectedCache, setScalpWorkbenchIntent, showToast]);

  const openWorkbenchAndReview = useCallback(
    (sig: ScalpSignal) => openWorkbench(sig, true),
    [openWorkbench],
  );

  const runScan = useCallback(async (fullUniverse = false) => {
    setScalpLabSelectedCache(null);
    const result = await postScan('/api/scalp-scan', {
      diagnostic,
      ...(fullUniverse ? {} : { max_actionable_candidates: 1 }),
    });
    if (!result || result.error) {
      showToast(`Scalp scan failed: ${result?.error || 'unknown'}`, 'error');
      return;
    }
    setScalpLabScanCache(result);
    showToast(
      `Engine D ${result.scan_mode || (fullUniverse ? 'full' : 'fast')}: ${result.pass_count ?? 0}/${result.candidate_count ?? 0} pass · ${result.skip_count ?? 0} skipped (session: ${result.session || '—'})`,
      'success',
    );
  }, [diagnostic, postScan, showToast, setScalpLabScanCache, setScalpLabSelectedCache]);

  const runFastScan = useCallback(() => runScan(false), [runScan]);
  const runFullScan = useCallback(() => runScan(true), [runScan]);

  const requestExecute = useCallback((s: ScalpSignal) => setConfirmExec(s), []);

  useEffect(() => {
    if (!confirmExec) {
      setRiskPreviewLine(null);
      return;
    }
    const symbol = String(confirmExec.pair || confirmExec.display || confirmExec.symbol || '').trim();
    if (!symbol) {
      setRiskPreviewLine(null);
      return;
    }
    let cancelled = false;
    void fetchRiskPreview({
      pair: symbol,
      display: confirmExec.display || symbol,
      symbol,
      type: confirmExec.type,
      direction: confirmExec.direction,
      entry: confirmExec.entry ?? confirmExec.price,
      price: confirmExec.entry ?? confirmExec.price,
      sl: confirmExec.sl,
      volume_mode: volumeMode,
      sizing_override: volumeMode === 'calculated' ? sizingOverride : 1.0,
      style: 'scalp',
    }).then((preview) => {
      if (!cancelled) {
        setRiskPreviewLine(formatRiskPreviewLine(preview));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [confirmExec, volumeMode, sizingOverride]);

  const onConfirmExecute = useCallback(async () => {
    if (!confirmExec) return;
    const symbol = String(confirmExec.pair || confirmExec.display || confirmExec.symbol || '').trim();
    if (!symbol) {
      showToast('Cannot execute — missing symbol', 'error');
      setConfirmExec(null);
      return;
    }
    const payload = buildScalpExecutePayload({
      symbol,
      signal: {
        symbol,
        pair: symbol,
        display: confirmExec.display || symbol,
        direction: confirmExec.direction,
        type: confirmExec.type,
        ai_grade: confirmExec.ai_grade,
        entry: confirmExec.entry ?? confirmExec.price,
        price: confirmExec.entry ?? confirmExec.price,
        sl: confirmExec.sl,
        tp1: confirmExec.tp1,
      },
      volumeMode,
      sizingOverride,
      executionMode: scalpExecutionMode,
      requireAiReview,
    });
    const result = await postExecute(executeEndpoint, payload);
    if (!result) {
      showToast('Execution failed (no response)', 'error');
    } else if (result.success) {
      showToast(`Scalp executed — ticket ${result.ticket || '?'}`, 'success');
    } else {
      const reason = result.error || 'unknown';
      setScalpLabScanCache(invalidateScalpSignal(scanResult, symbol, reason, result.fresh_scan));
      if (selected && signalKey(selected) === symbol.toUpperCase()) {
        setScalpLabSelectedCache({
          ...selected,
          executable: false,
          gate_result: 'NO_SETUP',
          candidate_status: reason,
          fail_reasons: [reason],
          soft_warnings: Array.from(new Set([...(selected.soft_warnings || []), 'fresh_execute_scan_invalidated_setup'])),
        });
      }
      showToast(`Scalp rejected: ${reason}`, 'error');
    }
    setConfirmExec(null);
  }, [
    confirmExec,
    executeEndpoint,
    postExecute,
    requireAiReview,
    scanResult,
    scalpExecutionMode,
    selected,
    setScalpLabScanCache,
    setScalpLabSelectedCache,
    showToast,
    volumeMode,
    sizingOverride,
  ]);

  const signals = useMemo(() => scanResult?.signals || [], [scanResult?.signals]);
  const skipped = useMemo(() => scanResult?.skipped || [], [scanResult?.skipped]);
  const skippedSummary = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of skipped) {
      const reason = row.reason || 'unknown';
      counts.set(reason, (counts.get(reason) || 0) + 1);
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  }, [skipped]);

  const groupedByGrade = useMemo(() => {
    const acc: Record<string, ScalpSignal[]> = { A: [], B: [], C: [], D: [] };
    for (const s of signals) {
      const g = String(s.ai_grade || 'D').toUpperCase();
      if (acc[g]) acc[g].push(s); else acc.D.push(s);
    }
    return acc;
  }, [signals]);

  return (
    <div className="space-y-5">
      {/* Header / scan controls */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
            <Activity className="w-4 h-4 text-primary" />
            Engine D — Scalp Lab (VP + OrderFlow)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Fabio Valentini methodology. Volume Profile (M15) → Absorption / CVD / AAA → VWAP lean →
            HTF bias gate → setup classification → AI quality grade. Crypto uses configured exchange trade buckets where fresh;
            non-crypto uses MT5 OHLC + EODHD volume overlay (where cached).
          </p>
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <Layers className="w-3.5 h-3.5" />
              Universe: <span className="font-mono text-foreground">{universeCount}</span> pairs
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={diagnostic} onCheckedChange={setDiagnostic} />
              <span className="text-xs text-muted-foreground">Diagnostic mode (show skipped)</span>
            </div>
            <Button size="sm" className="h-8 gap-1 text-xs" onClick={runFastScan} disabled={scanning}>
              <Zap className={scanning ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
              {scanning ? 'Engine D scanning…' : 'Fast Scan (first A/B)'}
            </Button>
            <Button size="sm" variant="outline" className="h-8 gap-1 text-xs" onClick={runFullScan} disabled={scanning}>
              <Layers className="w-3.5 h-3.5" />
              Full Scan
            </Button>
          </div>
          {scanError && <ErrorBanner message={scanError} onRetry={runFastScan} />}
        </CardContent>
      </Card>

      {scanResult && (
        <>
          {/* Summary row */}
          <div className="grid grid-cols-6 gap-3">
            <Kpi title="Universe" value={scanResult.pair_count ?? scanResult.pairs?.length ?? '—'} />
            <Kpi title="Scanned" value={scanResult.scanned ?? '—'} />
            <Kpi title="Candidates" value={scanResult.candidate_count ?? signals.length} />
            <Kpi title="Pass (exec)" value={scanResult.pass_count ?? 0} accent="text-long" />
            <Kpi title="Skipped" value={scanResult.skip_count ?? skipped.length} accent="text-warning" />
            <Kpi title="Session" value={scanResult.session || '—'} />
          </div>
          {scanResult.reason && signals.length === 0 && (
            <Card className="border-warning/40 bg-warning/10">
              <CardContent className="p-3 text-xs text-warning flex items-center gap-2">
                <AlertTriangle className="w-3.5 h-3.5" />
                {scanResult.reason}
              </CardContent>
            </Card>
          )}
          {skippedSummary.length > 0 && (
            <Card className="border-warning/40 bg-warning/10">
              <CardContent className="p-3 space-y-2">
                <div className="text-xs text-warning flex items-center gap-2">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  Engine D blocked {skipped.length} pair{skipped.length === 1 ? '' : 's'} on the latest scan
                </div>
                <div className="flex flex-wrap gap-1">
                  {skippedSummary.slice(0, 6).map(([reason, count]) => (
                    <Badge key={reason} variant="outline" className="text-[10px] text-warning border-warning/40">
                      {reason} × {count}
                    </Badge>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Signals + detail */}
          <div className="grid grid-cols-5 gap-4">
            <Card className="col-span-3 border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Scalp Candidates</CardTitle>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[520px] pr-2">
                  {signals.length === 0 ? (
                    <div className="text-xs text-muted-foreground text-center py-12">
                      No candidates produced this scan.
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {(['A', 'B', 'C', 'D'] as const).map((grade) => {
                        const list = groupedByGrade[grade] || [];
                        if (list.length === 0) return null;
                        return (
                          <div key={grade} className="space-y-2">
                            <div className="flex items-center gap-2">
                              <Badge variant="outline" className={`text-[10px] ${gradeColor(grade)}`}>Grade {grade}</Badge>
                              <span className="text-[10px] text-muted-foreground">{list.length}</span>
                            </div>
                            {list.map((s, i) => (
                              <ScalpCard
                                key={`${s.display || s.pair || s.symbol || i}-${i}`}
                                sig={s}
                                livePrice={priceFor(s)}
                                livePriceAgeSec={ageSecFor(s)}
                                livePriceSource={sourceFor(s)}
                                selected={selected === s}
                                onSelect={setScalpLabSelectedCache}
                                onExecute={openWorkbench}
                              />
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </ScrollArea>
              </CardContent>
            </Card>

            <Card className="col-span-2 border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Detail</CardTitle>
              </CardHeader>
              <CardContent>
                {selected ? (
                  <ScrollArea className="h-[520px] pr-2">
                    <ScalpDetail
                      sig={{
                        ...selected,
                        livePrice: priceFor(selected),
                        livePriceAgeSec: ageSecFor(selected),
                        livePriceSource: sourceFor(selected),
                      }}
                      onExecute={requestExecute}
                      onOpenWorkbenchAndReview={openWorkbenchAndReview}
                      canDirectExecute={canDirectExecute}
                      directExecuteLabel={directExecuteLabel}
                    />
                  </ScrollArea>
                ) : (
                  <div className="flex flex-col items-center justify-center h-[520px] text-muted-foreground">
                    <Activity className="w-8 h-8 mb-3 opacity-40" />
                    <p className="text-sm">Select a candidate to inspect VP / orderflow detail</p>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Skipped table (diagnostic) */}
          {scanResult.diagnostic && skipped.length > 0 && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Skipped / Filtered</CardTitle>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[260px]">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-border/40">
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Pair</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Gate</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Grade</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {skipped.map((row, i) => (
                        <tr key={`${row.pair || row.display || i}-${i}`} className="border-b border-border/20">
                          <td className="py-2 text-xs font-mono">{row.display || row.pair || '—'}</td>
                          <td className="py-2 text-[10px]">{row.gate_result || '—'}</td>
                          <td className="py-2 text-[10px]">{row.ai_grade || '—'}</td>
                          <td className="py-2 text-[10px] text-muted-foreground">
                            {row.reason || (row.fail_reasons || []).join(', ') || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </ScrollArea>
              </CardContent>
            </Card>
          )}
        </>
      )}

      {/* Execute confirm */}
      <AlertDialog open={!!confirmExec} onOpenChange={() => setConfirmExec(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Scalp Execution · Engine D</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-xs">
                <div>
                  Execute <b>{confirmExec?.direction}</b> {confirmExec?.display || confirmExec?.pair} ·
                  Grade <span className="font-mono">{confirmExec?.ai_grade || '—'}</span> ·
                  Score <span className="font-mono">{toNum(confirmExec?.ai_score).toFixed(0)}/100</span>
                </div>
                <div className="font-mono">
                  Entry {fmtPrice(confirmExec?.entry ?? confirmExec?.price, confirmExec?.pair, confirmExec?.type)} ·
                  SL {fmtPrice(confirmExec?.sl, confirmExec?.pair, confirmExec?.type)} ·
                  TP1 {fmtPrice(confirmExec?.tp1, confirmExec?.pair, confirmExec?.type)} ·
                  RR {fmtNum(confirmExec?.rr ?? confirmExec?.rr1, 2)}
                </div>
                <div className="text-muted-foreground">
                  Backend re-runs run_scalp_scan() against this single pair, applies risk_check(), and routes to
                  Bybit (crypto) or MT5 (others).
                </div>
                <VolumeModeField
                  volumeMode={volumeMode}
                  onVolumeModeChange={setVolumeMode}
                  sizingOverride={sizingOverride}
                  onSizingOverrideChange={setSizingOverride}
                />
                {riskPreviewLine && (
                  <div className="font-mono text-[10px] text-muted-foreground">{riskPreviewLine}</div>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmExecute} disabled={executing}>
              {executing ? 'Executing…' : 'Confirm'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ScalpCard({
  sig, livePrice, livePriceAgeSec, livePriceSource, selected, onSelect, onExecute,
}: {
  sig: ScalpSignal;
  livePrice?: number;
  livePriceAgeSec?: number;
  livePriceSource?: string;
  selected: boolean;
  onSelect: (s: ScalpSignal) => void;
  onExecute: (s: ScalpSignal) => void;
}) {
  const dir = String(sig.direction || '').toUpperCase();
  const long = dir === 'LONG' || dir === 'BUY';
  const display = sig.display || sig.pair || sig.symbol || '—';
  const grade = String(sig.ai_grade || 'D').toUpperCase();
  const aiScore = toNum(sig.ai_score);
  const rr = sig.rr ?? sig.rr1;
  const sizeMult = sig.size_multiplier;
  const exec = isSignalExecutable(sig);
  const openable = canOpenWorkbench(sig);
  const live = toNum(livePrice);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(sig)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(sig); }}
      className={`p-3 rounded-md border cursor-pointer transition-colors ${
        selected ? 'border-primary/60 bg-primary/10' : `${gradeBorder(grade)} bg-muted/30 hover:bg-muted/50`
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-mono font-bold truncate">{display}</span>
          <Badge className={long ? 'badge-long' : 'badge-short'}>{dir}</Badge>
          <Badge variant="outline" className={`text-[10px] ${gradeColor(grade)}`}>{grade}</Badge>
          {sig.zone_type && <Badge variant="outline" className="text-[10px] uppercase">{sig.zone_type}</Badge>}
          {sig.market_state && <Badge variant="outline" className="text-[10px] uppercase">{sig.market_state}</Badge>}
        </div>
        <div className="text-[10px] font-mono text-muted-foreground">
          {aiScore.toFixed(0)}/100 · RR {fmtNum(rr, 2)}
        </div>
      </div>

      <div className="mt-2 grid grid-cols-4 gap-2 text-[10px] text-muted-foreground">
        <div className="text-primary">
          Live {live > 0 ? fmtPrice(live, sig.pair, sig.type) : '—'}
          <span className="block text-[9px] text-muted-foreground">
            {fmtLiveQuoteMeta(livePriceAgeSec, livePriceSource)}
          </span>
        </div>
        <div>VAH {fmtNum(sig.vp_vah, 5)}</div>
        <div>POC {fmtNum(sig.vp_poc, 5)}</div>
        <div>VAL {fmtNum(sig.vp_val, 5)}</div>
      </div>

      <div className="mt-1 flex items-center gap-2 flex-wrap">
        {sig.absorption_count != null && (
          <Badge variant="outline" className="text-[10px]">absorb ×{sig.absorption_count}</Badge>
        )}
        {sig.cvd_direction && (
          <Badge variant="outline" className="text-[10px] uppercase">cvd {sig.cvd_direction}</Badge>
        )}
        {sig.vp_volume_source && (
          <Badge variant="outline" className="text-[10px]">vp {sig.vp_volume_source}</Badge>
        )}
        {sig.cvd_source && (
          <Badge variant="outline" className="text-[10px]">cvd src {sig.cvd_source}</Badge>
        )}
        {sig.aggression_source_is_proxy != null && (
          <Badge variant="outline" className={`text-[10px] ${sig.aggression_source_is_proxy ? 'text-short' : 'text-long'}`}>
            {sig.aggression_source_is_proxy ? 'proxy flow' : 'trade flow'}
          </Badge>
        )}
        {sig.strict_fabio_pass != null && (
          <Badge variant="outline" className={`text-[10px] ${sig.strict_fabio_pass ? 'text-long' : 'text-short'}`}>
            strict {sig.strict_fabio_pass ? 'yes' : 'no'}
          </Badge>
        )}
          {sig.current_vs_strict_status && (
            <Badge variant="outline" className="text-[10px]">{sig.current_vs_strict_status}</Badge>
          )}
        {sig.gate_result && (
          <Badge variant="outline" className={`text-[10px] ${exec ? 'text-long' : 'text-warning'}`}>
            gate {sig.gate_result}
          </Badge>
        )}
        {sig.htf_bias && (
          <Badge variant="outline" className="text-[10px] uppercase">{sig.htf_bias_tf || 'HTF'}: {sig.htf_bias}</Badge>
        )}
        {sizeMult != null && (
          <Badge variant="outline" className="text-[10px]">size {fmtNum(sizeMult, 2)}×</Badge>
        )}
        {sig.spread_pips != null && (
          <Badge variant="outline" className="text-[10px]">spr {fmtNum(sig.spread_pips, 2)}p</Badge>
        )}
      </div>

      <div className="mt-2 flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          className="h-7 gap-1 text-[10px]"
          onClick={(e) => { e.stopPropagation(); onExecute(sig); }}
          disabled={!openable}
        >
          <Play className="w-3 h-3" />
          {openable ? 'Open Workbench' : 'Missing direction'}
        </Button>
        {!exec && sig.candidate_status && (
          <span className="text-[10px] text-warning">{sig.candidate_status}</span>
        )}
        {!exec && !sig.candidate_status && (
          <span className="text-[10px] text-warning">{sig.gate_result || 'not executable'}</span>
        )}
      </div>
    </div>
  );
}

function boolText(value?: boolean): string {
  if (value == null) return '-';
  return value ? 'yes' : 'no';
}

function anchorCandidateText(candidate?: AnchorCandidate): string {
  if (!candidate) return '-';
  if (candidate.valid === false) return candidate.reason || 'not available';
  const bits = [];
  if (candidate.direction) bits.push(candidate.direction);
  if (candidate.bars != null) bits.push(`${candidate.bars} bars`);
  if (candidate.heuristic) bits.push(candidate.heuristic);
  return bits.join(' / ') || 'valid';
}

function ScalpDetail({
  sig,
  onExecute,
  onOpenWorkbenchAndReview,
  canDirectExecute,
  directExecuteLabel,
}: {
  sig: ScalpSignal;
  onExecute: (s: ScalpSignal) => void;
  onOpenWorkbenchAndReview: (s: ScalpSignal) => void;
  canDirectExecute: boolean;
  directExecuteLabel: string;
}) {
  const display = sig.display || sig.pair || sig.symbol || '—';
  const grade = String(sig.ai_grade || 'D').toUpperCase();
  const activeAnchor = sig.profile_anchor_shadow?.active_anchor;
  const fixedAnchor = sig.profile_anchor_shadow?.fixed_lookback_anchor;
  const anchorCandidates = sig.profile_anchor_shadow?.candidates || {};
  const vpProxy = sig.vp_is_proxy ?? sig.data_fidelity?.vp_is_proxy;
  const cvdProxy = sig.cvd_is_proxy ?? sig.data_fidelity?.cvd_is_proxy;
  const absorptionProxy = sig.absorption_is_proxy ?? sig.data_fidelity?.absorption_is_proxy;
  const realOrderFlow = sig.aggression_uses_real_order_flow ?? sig.data_fidelity?.aggression_uses_real_order_flow;
  const exec = isSignalExecutable(sig);
  const openable = canOpenWorkbench(sig);
  const gradeExecutable = grade === 'A' || grade === 'B';
  return (
    <div className="space-y-3">
      <div className="p-3 rounded-md bg-muted/30 space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-sm font-mono font-bold">{display}</p>
          <Badge variant="outline" className={`text-[10px] ${gradeColor(grade)}`}>Grade {grade}</Badge>
        </div>
        <div className="text-[10px] text-muted-foreground">
          {sig.engine || 'SCALP'} · {sig.execution_tf || 'M1'} entry · context {sig.context_tf || 'M5'} · structure {sig.structure_tf || 'M15'}
          {sig.session ? ` · ${sig.session}` : ''}
        </div>
      </div>

      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 space-y-2">
          <p className="text-[10px] uppercase text-muted-foreground">Levels</p>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <Row k="Gate" v={sig.gate_result || '—'} accent={exec ? 'long' : 'short'} />
            <Row k="Status" v={sig.candidate_status || (exec ? 'executable' : 'not executable')} accent={exec ? 'long' : 'short'} />
            <Row k="Entry" v={fmtPrice(sig.entry ?? sig.price, sig.pair, sig.type)} />
            <Row
              k="Live"
              v={fmtPrice(sig.livePrice, sig.pair, sig.type)}
              meta={fmtLiveQuoteMeta(sig.livePriceAgeSec, sig.livePriceSource)}
            />
            <Row k="SL" v={`${fmtPrice(sig.sl, sig.pair, sig.type)}${sig.sl_method ? ` · ${sig.sl_method}` : ''}`} accent="short" />
            <Row k="TP1" v={fmtPrice(sig.tp1, sig.pair, sig.type)} accent="long" />
            <Row k="TP2" v={fmtPrice(sig.tp2, sig.pair, sig.type)} accent="long" />
            <Row k="RR1" v={fmtNum(sig.rr1 ?? sig.rr, 2)} />
            <Row k="Spread" v={sig.spread_pips != null ? `${fmtNum(sig.spread_pips, 2)} pips` : '—'} />
            <Row k="Size mult" v={sig.size_multiplier != null ? `${fmtNum(sig.size_multiplier, 2)}×` : '—'} />
            <Row k="Risk level" v={sig.risk_level || '—'} />
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 space-y-2">
          <p className="text-[10px] uppercase text-muted-foreground">Volume Profile (M15)</p>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <Row k="VAH" v={fmtNum(sig.vp_vah, 6)} />
            <Row k="POC" v={fmtNum(sig.vp_poc, 6)} />
            <Row k="VAL" v={fmtNum(sig.vp_val, 6)} />
            <Row k="LVN count" v={sig.vp_lvn_count ?? '—'} />
            <Row k="VP source" v={sig.vp_volume_source || '—'} />
            <Row k="VP buckets" v={sig.vp_bucket_count ?? '—'} />
            <Row k="VP fidelity" v={sig.vp_fidelity || sig.data_fidelity?.vp_fidelity || '—'} />
            <Row
              k="VP proxy"
              v={boolText(vpProxy)}
              accent={vpProxy == null ? undefined : vpProxy ? 'short' : 'long'}
            />
            <Row k="Anchor mode" v={sig.profile_anchor_mode || activeAnchor?.mode || '—'} />
            <Row k="Anchor bars" v={sig.profile_anchor_bars ?? activeAnchor?.bars ?? '—'} />
            <Row k="Fixed lookback" v={fixedAnchor?.bars != null ? `${fixedAnchor.bars} bars` : '—'} />
            <Row k="Prior anchor" v={anchorCandidateText(anchorCandidates.prior_session)} />
            <Row k="Impulse anchor" v={anchorCandidateText(anchorCandidates.impulse_leg)} />
            <Row k="Reclaim anchor" v={anchorCandidateText(anchorCandidates.reclaim_leg)} />
            <Row k="Market state" v={sig.market_state || '—'} />
            <Row k="Zone" v={`${sig.zone_type || '—'}${sig.zone_level != null ? ` @ ${fmtNum(sig.zone_level, 5)}` : ''}`} />
          </div>
          {sig.zone_desc && <p className="text-[10px] text-muted-foreground">{sig.zone_desc}</p>}
        </CardContent>
      </Card>

      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 space-y-2">
          <p className="text-[10px] uppercase text-muted-foreground">Order Flow (Aggression)</p>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <Row k="Absorption count" v={sig.absorption_count ?? '—'} />
            <Row k="Absorption src" v={sig.absorption_source || sig.data_fidelity?.absorption_source || '—'} />
            <Row k="Absorption fidelity" v={sig.absorption_fidelity || sig.data_fidelity?.absorption_fidelity || '—'} />
            <Row
              k="Absorption proxy"
              v={boolText(absorptionProxy)}
              accent={absorptionProxy == null ? undefined : absorptionProxy ? 'short' : 'long'}
            />
            <Row k="CVD direction" v={sig.cvd_direction || '—'} />
            <Row k="CVD slope" v={fmtNum(sig.cvd_slope, 4)} />
            <Row k="CVD source" v={sig.cvd_source || '—'} />
            <Row k="CVD buckets" v={sig.cvd_bucket_count ?? '—'} />
            <Row k="CVD fidelity" v={sig.cvd_fidelity || sig.data_fidelity?.cvd_fidelity || '—'} />
            <Row
              k="CVD proxy"
              v={boolText(cvdProxy)}
              accent={cvdProxy == null ? undefined : cvdProxy ? 'short' : 'long'}
            />
            <Row k="Aggression source" v={sig.aggression_source || '—'} />
            <Row
              k="Real order flow"
              v={boolText(realOrderFlow)}
              accent={realOrderFlow == null ? undefined : realOrderFlow ? 'long' : 'short'}
            />
            <Row
              k="Aggression confirmed"
              v={sig.aggression_confirmed == null ? '—' : sig.aggression_confirmed ? 'yes' : 'no'}
              accent={sig.aggression_confirmed == null ? undefined : sig.aggression_confirmed ? 'long' : 'short'}
            />
            <Row
              k="Strict Fabio pass"
              v={sig.strict_fabio_pass == null ? '—' : sig.strict_fabio_pass ? 'yes' : 'no'}
              accent={sig.strict_fabio_pass == null ? undefined : sig.strict_fabio_pass ? 'long' : 'short'}
            />
            <Row k="Strict reason" v={sig.strict_fabio_reason || '—'} />
            <Row k="Missing pillars" v={(sig.strict_fabio_missing_pillars || []).join(', ') || '—'} />
            <Row k="Current vs strict" v={sig.current_vs_strict_status || '—'} />
            <Row
              k="Proxy flow"
              v={sig.aggression_source_is_proxy == null ? '—' : sig.aggression_source_is_proxy ? 'yes' : 'no'}
              accent={sig.aggression_source_is_proxy == null ? undefined : sig.aggression_source_is_proxy ? 'short' : 'long'}
            />
            <Row k="AAA complete" v={sig.aaa_complete ? 'yes' : 'no'} accent={sig.aaa_complete ? 'long' : undefined} />
            <Row k="VWAP" v={fmtNum(sig.vwap, 6)} />
            <Row k="HTF bias" v={`${sig.htf_bias || '—'}${sig.htf_bias_tf ? ` (${sig.htf_bias_tf})` : ''}`} />
            <Row k="Trigger" v={sig.trigger_type || '—'} />
            <Row k="Momentum" v={sig.momentum_method || '—'} />
          </div>
          {sig.trigger_desc && <p className="text-[10px] text-muted-foreground">{sig.trigger_desc}</p>}
        </CardContent>
      </Card>

      {sig.ai_reasons && sig.ai_reasons.length > 0 && (
        <Card className="border-border/60 bg-card/50">
          <CardContent className="p-3 space-y-2">
            <p className="text-[10px] uppercase text-muted-foreground">AI Quality Reasons</p>
            <ul className="text-[11px] space-y-1 list-disc pl-4 text-muted-foreground">
              {sig.ai_reasons.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </CardContent>
        </Card>
      )}

      {sig.fail_reasons && sig.fail_reasons.length > 0 && (
        <Card className="border-short/40 bg-short/10">
          <CardContent className="p-3 space-y-1">
            <p className="text-[10px] uppercase text-short">Fail Reasons</p>
            <div className="flex flex-wrap gap-1">
              {sig.fail_reasons.map((r, i) => (
                <Badge key={i} variant="outline" className="text-[10px] text-short border-short/40">{r}</Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {sig.soft_warnings && sig.soft_warnings.length > 0 && (
        <Card className="border-warning/40 bg-warning/10">
          <CardContent className="p-3 space-y-1">
            <p className="text-[10px] uppercase text-warning">Soft Warnings</p>
            <ul className="text-[10px] space-y-1 list-disc pl-4 text-warning">
              {sig.soft_warnings.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </CardContent>
        </Card>
      )}

      <Button
        size="sm"
        variant="secondary"
        className="w-full gap-2"
        onClick={() => onOpenWorkbenchAndReview(sig)}
      >
        <Sparkles className="w-3.5 h-3.5" />
        Open Workbench &amp; Review
      </Button>

      <Button
        size="sm"
        className="w-full gap-2"
        onClick={() => onExecute(sig)}
        disabled={!openable || !gradeExecutable || !canDirectExecute}
      >
        <Play className="w-3.5 h-3.5" />
        {directExecuteLabel}
      </Button>
    </div>
  );
}

function Kpi({ title, value, accent }: { title: string; value: string | number; accent?: string }) {
  return (
    <div className="p-3 rounded-md bg-muted/30">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
      <p className={`text-lg font-mono font-bold mt-1 ${accent || ''}`}>{value}</p>
    </div>
  );
}

function Row({ k, v, accent, meta }: { k: string; v: string | number | null | undefined; accent?: 'long' | 'short'; meta?: string }) {
  const cls = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : 'text-foreground';
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[10px] uppercase text-muted-foreground">{k}</span>
      <span className={`font-mono text-right ${cls}`}>
        {v == null || v === '' ? '—' : v}
        {meta && <span className="block text-[9px] text-muted-foreground">{meta}</span>}
      </span>
    </div>
  );
}
