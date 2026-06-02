import { useCallback, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ErrorBanner } from '@/components/shared';
import { Workflow, BarChart2, Eye, RefreshCw, Filter } from 'lucide-react';
import { fmtNum, cn } from '@/lib/utils';
import { regimeLabel, sessionLabel } from '@/lib/athenaFormat';
import type { EngineASignal } from '@/types/athena';
import apiClient from '@/lib/apiClient';
import type { CascadeActionState, CascadeCandidate, CascadeScanResponse } from '@/types';

/** Local-only chart-review status overlay. Cascade results never auto-run Vision. */
type LocalReviewStatus = 'OPENED';

const ACTION_STATE_CLASS: Record<CascadeActionState, string> = {
  READY_FOR_CHART_REVIEW: 'badge-long',
  ENGINE_A_ONLY: 'badge-neutral',
  WATCHLIST_ONLY: 'badge-neutral',
  BLOCKED: 'badge-short',
};

function actionStateLabel(state: CascadeActionState): string {
  return state.replace(/_/g, ' ');
}

function directionClass(direction: string): string {
  const dir = direction.toUpperCase();
  if (dir === 'LONG') return 'text-long';
  if (dir === 'SHORT') return 'text-short';
  return 'text-foreground';
}

function fidelityPct(score: number): string {
  if (!Number.isFinite(score)) return '—';
  return `${Math.round(score * 100)}%`;
}

function compactList(items: string[] | undefined, max = 3): { text: string; title: string } | null {
  if (!Array.isArray(items) || items.length === 0) return null;
  const cleaned = items.map((i) => String(i).replace(/_/g, ' '));
  const text = cleaned.slice(0, max).join(' · ') + (cleaned.length > max ? ` +${cleaned.length - max}` : '');
  return { text, title: cleaned.join('\n') };
}

export default function CascadeScanPanel() {
  const { showToast, setActivePanel, setTvChartIntent } = useStore();
  const [assetClass, setAssetClass] = useState<string>('forex');
  const [enableTriage, setEnableTriage] = useState<boolean>(false);
  const [topN, setTopN] = useState<number>(5);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CascadeScanResponse | null>(null);
  const [reviewStatus, setReviewStatus] = useState<Record<string, LocalReviewStatus>>({});

  const runScan = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = (await apiClient.postJson('/api/cascade-scan', {
        asset_class: assetClass,
        style: 'auto',
        enable_triage: enableTriage,
        top_n: topN,
      })) as CascadeScanResponse;

      if (resp?.busy) {
        setError('A scan is already in progress. Try again shortly.');
        showToast('Cascade scan busy — another scan is running', 'error');
        return;
      }
      if (resp?.success === false || resp?.error) {
        setError(resp.error || 'Cascade scan failed');
        showToast(`Cascade scan failed: ${resp.error || 'unknown'}`, 'error');
        return;
      }
      setResult(resp);
      setReviewStatus({});
      showToast(
        `Cascade scan complete · ${resp.shortlistCount} shortlisted of ${resp.engineACandidateCount} Engine A candidates`,
        'success',
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'unknown';
      setError(msg);
      showToast(`Cascade scan error: ${msg}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [assetClass, enableTriage, topN, showToast]);

  /**
   * Reuse the existing client-side chart flow: load the symbol + reviewTimeframe
   * onto the TV Chart. autoReview drives the SAME screenshot_meta + renderedLayers
   * + /api/chart-analysis path SignalsPanel uses. No new review path; no execution.
   */
  const openOnTvChart = useCallback(
    (candidate: CascadeCandidate, autoReview: boolean) => {
      const symbol = String(candidate.symbol || '').trim();
      if (!symbol) {
        showToast('Cannot open chart: missing symbol', 'error');
        return;
      }
      setTvChartIntent({
        id: `tv-cascade-${symbol}-${Date.now()}`,
        source: 'manual',
        symbol,
        display: symbol,
        signal: candidate,
        preferredTf: (candidate.reviewTimeframe || 'H4').toUpperCase(),
        autoReview,
        createdAt: new Date().toISOString(),
      });
      setActivePanel('tvChart');
      if (autoReview) {
        setReviewStatus((prev) => ({ ...prev, [symbol]: 'OPENED' }));
        showToast(`Opening ${symbol} (${candidate.reviewTimeframe}) for chart AI review`, 'info');
      } else {
        showToast(`Opening ${symbol} (${candidate.reviewTimeframe}) on TV Chart`, 'info');
      }
    },
    [setActivePanel, setTvChartIntent, showToast],
  );

  const candidates = result?.candidates ?? [];

  return (
    <div className="space-y-4">
      {/* -- CONTROLS -- */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 flex items-center gap-3 flex-wrap">
          <Select value={assetClass} onValueChange={setAssetClass}>
            <SelectTrigger className="w-[130px] h-8 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="forex">Forex</SelectItem>
              <SelectItem value="crypto">Crypto</SelectItem>
              <SelectItem value="stock">Stocks</SelectItem>
              <SelectItem value="index">Indices</SelectItem>
              <SelectItem value="commodity">Commodities</SelectItem>
              <SelectItem value="etf">ETFs</SelectItem>
            </SelectContent>
          </Select>

          <div className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground">Top N</span>
            <Input
              type="number"
              min={1}
              max={50}
              className="w-[70px] h-8 text-xs"
              value={topN}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10);
                setTopN(Number.isFinite(n) && n > 0 ? n : 1);
              }}
            />
          </div>

          <div className="flex items-center gap-2">
            <Switch checked={enableTriage} onCheckedChange={setEnableTriage} />
            <span className="text-[11px] text-muted-foreground">
              JSON triage {enableTriage ? 'on' : 'off'}{' '}
              <span className="text-[10px]">(display-only)</span>
            </span>
          </div>

          <Button
            size="sm"
            className="h-8 gap-1 text-xs ml-auto"
            onClick={runScan}
            disabled={loading}
          >
            {loading
              ? <span className="w-3.5 h-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
              : <Workflow className="w-3.5 h-3.5" />}
            {loading ? 'Scanning…' : 'Run Cascade Scan'}
          </Button>
        </CardContent>
      </Card>

      {error && <ErrorBanner message={error} onRetry={runScan} />}

      {/* -- STATUS ROW -- */}
      {result && (
        <div className="flex items-center gap-3 text-[11px] text-muted-foreground flex-wrap">
          <span>Run {result.scanRunId?.slice(0, 8) || '—'}</span>
          <span>Asset class: {result.assetClass}</span>
          <span>Universe: {result.universeCount}</span>
          <span>Engine A candidates: {result.engineACandidateCount}</span>
          <span>Shortlisted: {result.shortlistCount}</span>
          <span>Triage: {result.triageEnabled ? 'on' : 'off'}</span>
        </div>
      )}

      {/* -- RESULTS -- */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle
            className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider"
            style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}
          >
            <Workflow className="w-4 h-4 text-primary" />
            Cascade Shortlist
            <Badge variant="outline" className="ml-auto text-[10px]">{candidates.length} shown</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {candidates.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-[260px] text-muted-foreground text-center px-4">
              <Filter className="w-8 h-8 mb-3 opacity-40" />
              <p className="text-sm">{result ? 'No candidates shortlisted' : 'No cascade scan yet'}</p>
              <p className="text-[11px] mt-1">
                {result
                  ? 'Engine A produced no candidates passing the deterministic shortlist for this run.'
                  : 'Run a cascade scan to shortlist Engine A candidates for chart review.'}
              </p>
            </div>
          ) : (
            <ScrollArea className="h-[620px] pr-2">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-[10px]">#</TableHead>
                    <TableHead className="text-[10px]">Symbol</TableHead>
                    <TableHead className="text-[10px]">Dir</TableHead>
                    <TableHead className="text-[10px] text-right">Conf.</TableHead>
                    <TableHead className="text-[10px] text-right">RR</TableHead>
                    <TableHead className="text-[10px]">Regime / Session</TableHead>
                    <TableHead className="text-[10px] text-right">Coherence</TableHead>
                    <TableHead className="text-[10px]">Source Fidelity</TableHead>
                    <TableHead className="text-[10px]">Reasons</TableHead>
                    <TableHead className="text-[10px]">Blockers / Warnings</TableHead>
                    {result?.triageEnabled && <TableHead className="text-[10px]">Triage</TableHead>}
                    <TableHead className="text-[10px]">Action State</TableHead>
                    <TableHead className="text-[10px]">Chart AI</TableHead>
                    <TableHead className="text-[10px]">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {candidates.map((c) => {
                    const reasons = compactList(c.shortlistReasons);
                    const blockers = compactList(c.shortlistBlockers);
                    const opened = reviewStatus[c.symbol] === 'OPENED';
                    return (
                      <TableRow key={`${c.symbol}-${c.shortlistRank}`}>
                        <TableCell className="text-xs font-mono">{c.shortlistRank}</TableCell>
                        <TableCell className="text-xs font-mono font-semibold">
                          {c.symbol}
                          <span className="block text-[9px] text-muted-foreground font-normal">
                            {c.displayTimeframe} · {c.analysisTimeframes.join('/')}
                          </span>
                        </TableCell>
                        <TableCell className={cn('text-xs font-mono font-semibold', directionClass(c.direction))}>
                          {c.direction}
                        </TableCell>
                        <TableCell className="text-xs font-mono text-right">{fmtNum(c.confluenceScore, 2)}</TableCell>
                        <TableCell className="text-xs font-mono text-right">
                          {c.rr == null ? '—' : fmtNum(c.rr, 2)}
                        </TableCell>
                        <TableCell className="text-[11px]">
                          {regimeLabel(c.regime as EngineASignal['regime'])}
                          <span className="text-muted-foreground">
                            {' '}/ {sessionLabel(c.session as EngineASignal['session'])}
                          </span>
                        </TableCell>
                        <TableCell className="text-xs font-mono text-right" title={c.coherenceReason}>
                          {fmtNum(c.coherenceScore, 2)}
                        </TableCell>
                        <TableCell className="text-[11px]">
                          <div className="flex items-center gap-1">
                            <span className="font-mono">{fidelityPct(c.sourceFidelity?.score)}</span>
                            <Badge variant="outline" className="text-[9px] uppercase">
                              {c.sourceFidelity?.pairSource || '—'}
                            </Badge>
                          </div>
                          <span className="text-[9px] text-muted-foreground">
                            {c.sourceFidelity?.dataFreshness || '—'}
                          </span>
                        </TableCell>
                        <TableCell className="text-[11px] max-w-[160px]">
                          {reasons ? <span title={reasons.title}>{reasons.text}</span> : '—'}
                        </TableCell>
                        <TableCell className="text-[11px] max-w-[160px] text-warning">
                          {blockers ? <span title={blockers.title}>{blockers.text}</span> : '—'}
                        </TableCell>
                        {result?.triageEnabled && (
                          <TableCell className="text-[11px]">
                            {c.triageVerdict ? (
                              <Badge
                                variant="outline"
                                className="text-[9px]"
                                title={`${c.triageReason || ''}${c.triageConfidence != null ? ` · conf ${fmtNum(c.triageConfidence, 2)}` : ''}`}
                              >
                                {c.triageVerdict.replace(/_/g, ' ')}
                              </Badge>
                            ) : '—'}
                          </TableCell>
                        )}
                        <TableCell>
                          <Badge className={cn('text-[9px]', ACTION_STATE_CLASS[c.actionState] || 'badge-neutral')}>
                            {actionStateLabel(c.actionState)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-[10px] font-mono uppercase text-muted-foreground">
                          {opened ? 'review opened' : c.chartAiStatus.toLowerCase().replace('_', ' ')}
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1 flex-wrap">
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 gap-1 text-[10px]"
                              onClick={() => openOnTvChart(c, false)}
                            >
                              <BarChart2 className="w-3 h-3" />
                              Open chart
                            </Button>
                            {c.readyForChartReview && (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 gap-1 text-[10px]"
                                onClick={() => openOnTvChart(c, true)}
                              >
                                <Eye className="w-3 h-3" />
                                Review with chart AI
                              </Button>
                            )}
                            {c.readyForChartReview && opened && (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 gap-1 text-[10px]"
                                onClick={() => openOnTvChart(c, true)}
                                title="Re-render and run a fresh chart AI review — never reuses stale cascade results."
                              >
                                <RefreshCw className="w-3 h-3" />
                                Re-run fresh ENTRY_NOW review
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </ScrollArea>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
