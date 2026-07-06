import { useCallback, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ErrorBanner } from '@/components/shared';
import { Sparkles, BarChart2, Filter, History, ChevronDown, ChevronRight } from 'lucide-react';
import { fmtNum, cn } from '@/lib/utils';
import apiClient from '@/lib/apiClient';

interface BulkReviewRow {
  scanRunId: string;
  symbol: string;
  pair: string;
  direction: string | null;
  confluenceScore: number | null;
  rr: number | null;
  shortlistRank: number | null;
  grade: string | null;
  edgeProbability: number | null;
  riskLevel: string | null;
  verdict: string | null;
  provider: string | null;
  model: string | null;
  cacheHit: boolean;
}

interface BulkFilteredOut {
  symbol: string;
  direction: string | null;
  grade: string | null;
  isSafeDefault: boolean;
}

interface BulkReviewResponse {
  success?: boolean;
  busy?: boolean;
  error?: string;
  scanRunId?: string;
  assetClass?: string;
  universeCount?: number;
  shortlistCount?: number;
  hardBlockedCount?: number;
  hardBlockedSummary?: { symbol: string; blockers: string[] }[];
  results?: BulkReviewRow[];
  filteredOut?: BulkFilteredOut[];
  skipped?: { symbol: string; reason: string }[];
  summary?: {
    reviewed: number;
    cacheHits: number;
    safeDefaults: number;
    aGrades: number;
    bGrades: number;
    skippedBudget: number;
    elapsedMs: number;
  };
  nextStep?: string;
}

interface HistoryRun {
  scan_run_id: string;
  created_at: string;
  asset_class: string | null;
  style: string | null;
  reviewed: number;
  a_grades: number;
  b_grades: number;
  safe_defaults: number;
  cache_hits: number;
}

function gradeBadgeClass(grade: string | null): string {
  const g = (grade || '').toUpperCase();
  if (g === 'A+' || g === 'A') return 'badge-long';
  if (g === 'B') return 'badge-neutral';
  return 'badge-short';
}

function directionClass(direction: string | null): string {
  const dir = (direction || '').toUpperCase();
  if (dir === 'LONG') return 'text-long';
  if (dir === 'SHORT') return 'text-short';
  return 'text-foreground';
}

export default function BulkAiReviewPanel() {
  const { showToast, setActivePanel, setTvChartIntent } = useStore();
  const [assetClass, setAssetClass] = useState('forex');
  const [topN, setTopN] = useState(8);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BulkReviewResponse | null>(null);
  const [showFiltered, setShowFiltered] = useState(false);
  const [historyRuns, setHistoryRuns] = useState<HistoryRun[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const runBulkReview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = (await apiClient.postJson('/api/bulk-ai-review', {
        asset_class: assetClass,
        style: 'auto',
        top_n: topN,
      })) as BulkReviewResponse;

      if (resp?.busy) {
        setError('A scan is already in progress. Try again shortly.');
        showToast('Bulk AI review busy — another scan is running', 'error');
        return;
      }
      if (resp?.success === false || resp?.error) {
        const failMsg = resp.error || 'Bulk AI review failed';
        setError(failMsg);
        showToast(`Bulk AI review failed: ${failMsg}`, 'error');
        return;
      }
      setResult(resp);
      const s = resp.summary;
      showToast(
        `Bulk AI review complete · ${s?.aGrades ?? 0} A / ${s?.bGrades ?? 0} B of ${s?.reviewed ?? 0} reviewed`,
        'success',
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'unknown';
      setError(msg);
      showToast(`Bulk AI review error: ${msg}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [assetClass, topN, showToast]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const resp = (await apiClient.getJson('/api/bulk-ai-review/history?view=runs&limit=20')) as {
        success?: boolean;
        runs?: HistoryRun[];
      };
      setHistoryRuns(resp?.runs ?? []);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'unknown';
      showToast(`History load failed: ${msg}`, 'error');
    } finally {
      setHistoryLoading(false);
    }
  }, [showToast]);

  /** Reuse the existing client-side TV chart flow for manual chart review of A/B survivors. */
  const openOnTvChart = useCallback(
    (row: BulkReviewRow, autoReview: boolean) => {
      const symbol = String(row.symbol || '').trim();
      if (!symbol) {
        showToast('Cannot open chart: missing symbol', 'error');
        return;
      }
      setTvChartIntent({
        id: `tv-bulkai-${symbol}-${Date.now()}`,
        source: 'manual',
        symbol,
        display: symbol,
        signal: row,
        preferredTf: 'H4',
        autoReview,
        createdAt: new Date().toISOString(),
      });
      setActivePanel('tvChart');
      showToast(`Opening ${symbol} on TV Chart${autoReview ? ' for chart AI review' : ''}`, 'info');
    },
    [setActivePanel, setTvChartIntent, showToast],
  );

  const rows = result?.results ?? [];
  const filteredOut = result?.filteredOut ?? [];
  const summary = result?.summary;

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
            <span className="text-[11px] text-muted-foreground">Top N (max 15)</span>
            <Input
              type="number"
              min={1}
              max={15}
              className="w-[70px] h-8 text-xs"
              value={topN}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10);
                setTopN(Number.isFinite(n) ? Math.max(1, Math.min(n, 15)) : 8);
              }}
            />
          </div>

          <span className="text-[10px] text-muted-foreground">
            Scan → shortlist → Marcus Reid text review · advisory only
          </span>

          <Button
            size="sm"
            className="h-8 gap-1 text-xs ml-auto"
            onClick={runBulkReview}
            disabled={loading}
          >
            {loading
              ? <span className="w-3.5 h-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
              : <Sparkles className="w-3.5 h-3.5" />}
            {loading ? 'Scanning + reviewing…' : 'Bulk Scan + AI Review'}
          </Button>
        </CardContent>
      </Card>

      {error && <ErrorBanner message={error} onRetry={runBulkReview} />}

      {/* -- SUMMARY ROW -- */}
      {result && summary && (
        <div className="flex items-center gap-3 text-[11px] text-muted-foreground flex-wrap">
          <span>Run {result.scanRunId?.slice(0, 8) || '—'}</span>
          <span>Asset class: {result.assetClass}</span>
          <span>Universe: {result.universeCount}</span>
          <span>Shortlisted: {result.shortlistCount}</span>
          {(result.hardBlockedCount ?? 0) > 0 && (
            <span className="text-warning">Hard blocked: {result.hardBlockedCount}</span>
          )}
          <span>Reviewed: {summary.reviewed}</span>
          <span className="text-long">A: {summary.aGrades}</span>
          <span>B: {summary.bGrades}</span>
          {summary.cacheHits > 0 && <span>Cache hits: {summary.cacheHits}</span>}
          {summary.safeDefaults > 0 && (
            <span className="text-warning">AI failures: {summary.safeDefaults}</span>
          )}
          {summary.skippedBudget > 0 && (
            <span className="text-warning">Skipped (budget): {summary.skippedBudget}</span>
          )}
          <span>{(summary.elapsedMs / 1000).toFixed(1)}s</span>
        </div>
      )}

      {/* -- A/B RESULTS -- */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle
            className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider"
            style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}
          >
            <Sparkles className="w-4 h-4 text-primary" />
            A / B Graded Signals
            <Badge variant="outline" className="ml-auto text-[10px]">{rows.length} shown</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {rows.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-[220px] text-muted-foreground text-center px-4">
              <Filter className="w-8 h-8 mb-3 opacity-40" />
              <p className="text-sm">{result ? 'No A/B-graded signals this run' : 'No bulk review yet'}</p>
              <p className="text-[11px] mt-1">
                {!result
                  ? 'Run a bulk scan + AI review to surface Marcus Reid A/B signals.'
                  : (result.shortlistCount ?? 0) === 0
                    ? 'No candidates reached the AI review — the engines emitted no eligible signals this run (see hard-block reasons below).'
                    : 'All shortlisted candidates graded C or below (or AI unavailable).'}
              </p>
            </div>
          ) : (
            <ScrollArea className="h-[420px] pr-2">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-[10px]">#</TableHead>
                    <TableHead className="text-[10px]">Symbol</TableHead>
                    <TableHead className="text-[10px]">Dir</TableHead>
                    <TableHead className="text-[10px]">Grade</TableHead>
                    <TableHead className="text-[10px] text-right">Edge %</TableHead>
                    <TableHead className="text-[10px]">Risk</TableHead>
                    <TableHead className="text-[10px] text-right">Conf.</TableHead>
                    <TableHead className="text-[10px] text-right">RR</TableHead>
                    <TableHead className="text-[10px]">Verdict</TableHead>
                    <TableHead className="text-[10px]">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={`${r.symbol}-${r.shortlistRank}`}>
                      <TableCell className="text-xs font-mono">{r.shortlistRank ?? '—'}</TableCell>
                      <TableCell className="text-xs font-mono font-semibold">
                        {r.symbol}
                        {r.cacheHit && (
                          <span className="block text-[9px] text-muted-foreground font-normal">cached</span>
                        )}
                      </TableCell>
                      <TableCell className={cn('text-xs font-mono font-semibold', directionClass(r.direction))}>
                        {r.direction || '—'}
                      </TableCell>
                      <TableCell>
                        <Badge className={cn('text-[10px] font-bold', gradeBadgeClass(r.grade))}>
                          {r.grade || '—'}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs font-mono text-right">
                        {r.edgeProbability == null ? '—' : `${fmtNum(r.edgeProbability, 0)}%`}
                      </TableCell>
                      <TableCell className="text-[11px]">{r.riskLevel || '—'}</TableCell>
                      <TableCell className="text-xs font-mono text-right">{fmtNum(r.confluenceScore ?? 0, 2)}</TableCell>
                      <TableCell className="text-xs font-mono text-right">
                        {r.rr == null ? '—' : fmtNum(r.rr, 2)}
                      </TableCell>
                      <TableCell className="text-[11px] max-w-[260px]" title={r.verdict || undefined}>
                        {r.verdict || '—'}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1 flex-wrap">
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 gap-1 text-[10px]"
                            onClick={() => openOnTvChart(r, false)}
                          >
                            <BarChart2 className="w-3 h-3" />
                            Open chart
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </ScrollArea>
          )}
        </CardContent>
      </Card>

      {/* -- HARD BLOCKED -- */}
      {result && (result.hardBlockedSummary?.length ?? 0) > 0 && (
        <Card className="border-warning/35 bg-warning/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-warning">
              Blocked before AI review ({result.hardBlockedCount ?? result.hardBlockedSummary?.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-[11px]">
            {result.hardBlockedSummary?.map((row) => (
              <div key={row.symbol} className="rounded border border-border/50 px-2 py-1.5">
                <span className="font-mono font-semibold">{row.symbol}</span>
                <span className="text-muted-foreground"> — {row.blockers.join(', ')}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* -- FILTERED OUT -- */}
      {filteredOut.length > 0 && (
        <Card className="border-border/60 bg-card/40">
          <CardHeader className="pb-2 cursor-pointer" onClick={() => setShowFiltered((v) => !v)}>
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider text-muted-foreground">
              {showFiltered ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              Filtered out ({filteredOut.length})
            </CardTitle>
          </CardHeader>
          {showFiltered && (
            <CardContent className="flex flex-wrap gap-2 text-[11px]">
              {filteredOut.map((f) => (
                <Badge key={f.symbol} variant="outline" className="text-[10px] gap-1">
                  <span className="font-mono">{f.symbol}</span>
                  <span className={cn(directionClass(f.direction))}>{f.direction || ''}</span>
                  <span className="font-bold">{f.isSafeDefault ? 'AI FAIL' : (f.grade || '?')}</span>
                </Badge>
              ))}
            </CardContent>
          )}
        </Card>
      )}

      {/* -- HISTORY -- */}
      <Card className="border-border/60 bg-card/40">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider text-muted-foreground">
            <History className="w-3.5 h-3.5" />
            Review History
            <Button
              size="sm"
              variant="outline"
              className="h-7 gap-1 text-[10px] ml-auto"
              onClick={loadHistory}
              disabled={historyLoading}
            >
              {historyLoading ? 'Loading…' : 'Load runs'}
            </Button>
          </CardTitle>
        </CardHeader>
        {historyRuns && (
          <CardContent>
            {historyRuns.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">No persisted runs yet.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-[10px]">Run</TableHead>
                    <TableHead className="text-[10px]">Time (UTC)</TableHead>
                    <TableHead className="text-[10px]">Asset</TableHead>
                    <TableHead className="text-[10px] text-right">Reviewed</TableHead>
                    <TableHead className="text-[10px] text-right">A</TableHead>
                    <TableHead className="text-[10px] text-right">B</TableHead>
                    <TableHead className="text-[10px] text-right">AI fails</TableHead>
                    <TableHead className="text-[10px] text-right">Cache</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {historyRuns.map((run) => (
                    <TableRow key={run.scan_run_id}>
                      <TableCell className="text-xs font-mono">{run.scan_run_id.slice(0, 8)}</TableCell>
                      <TableCell className="text-[11px]">{run.created_at?.slice(0, 16).replace('T', ' ')}</TableCell>
                      <TableCell className="text-[11px]">{run.asset_class || '—'}</TableCell>
                      <TableCell className="text-xs font-mono text-right">{run.reviewed}</TableCell>
                      <TableCell className="text-xs font-mono text-right text-long">{run.a_grades}</TableCell>
                      <TableCell className="text-xs font-mono text-right">{run.b_grades}</TableCell>
                      <TableCell className="text-xs font-mono text-right text-warning">{run.safe_defaults}</TableCell>
                      <TableCell className="text-xs font-mono text-right">{run.cache_hits}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        )}
      </Card>
    </div>
  );
}
