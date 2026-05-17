import { useCallback, useEffect, useState } from 'react';
import { BrainCircuit, AlertTriangle, CheckCircle2, XCircle, BarChart3, Eye, GitCompare, MessageSquare, Bot, FlaskConical, ShieldAlert, RefreshCw, Loader2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ErrorBanner } from '@/components/shared';
import { cn } from '@/lib/utils';

interface SurfaceMetric {
  sample_count: number;
  valid_outcome_count: number;
  insufficient_count: number;
  win_rate_when_positive: number | null;
  avg_r_when_positive: number | null;
  block_count: number;
  useful_block_count: number;
  false_block_count: number;
  harmful_allow_count: number;
  contradiction_rate: number | null;
  missing_data_rate: number | null;
  parse_failure_rate: number | null;
  stale_context_rate: number | null;
  notes: string[];
}

interface EvalReport {
  report_id?: string;
  generated_at?: string;
  lookback_days?: number;
  surfaces?: string[];
  overall_summary?: string;
  surface_metrics?: Record<string, SurfaceMetric>;
  best_ai_behaviors?: string[];
  worst_ai_behaviors?: string[];
  recommendations?: string[];
  do_not_auto_apply?: boolean;
  warnings?: string[];
  vision?: Record<string, unknown>;
  strategist?: Record<string, unknown>;
  debate?: Record<string, unknown>;
  total_samples?: number;
  valid_outcomes?: number;
  overall_win_rate?: number | null;
  overall_avg_r?: number | null;
}

const SURFACE_ICONS: Record<string, typeof Bot> = {
  MARCUS: Bot,
  VISION: Eye,
  DEBATE: GitCompare,
  STRATEGIST: FlaskConical,
  CHAT: MessageSquare,
  ENGINE_B_AI: BrainCircuit,
  ENGINE_C_AI: BrainCircuit,
  RESEARCH_AGENT: FlaskConical,
};

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function fmtNum(v: number | null | undefined, d = 2): string {
  if (v == null) return '—';
  return v.toFixed(d);
}

function KpiCard({ title, value, loading }: { title: string; value: string; loading?: boolean }) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
        {loading ? <Skeleton className="h-6 w-16 mt-1" /> : <p className="text-lg font-bold font-mono mt-1">{value}</p>}
      </CardContent>
    </Card>
  );
}

export default function AiPerformancePanel() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<EvalReport | null>(null);
  const [activeTab, setActiveTab] = useState('overview');

  const loadSummary = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/ai/evaluation/summary?lookback_days=30');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setReport(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadSummary(); }, [loadSummary]);

  const surfaces = report?.surface_metrics || {};
  const surfaceNames = Object.keys(surfaces);
  const visionData = report?.vision;
  const strategistData = report?.strategist;
  const debateData = report?.debate;
  const recs = report?.recommendations || [];
  const warnings = report?.warnings || [];

  return (
    <div className="space-y-5">
      {/* Header */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BrainCircuit className="w-5 h-5 text-primary" />
            <span className="text-sm font-semibold">AI Performance</span>
            <Badge variant="outline" className="text-[10px]">
              {report?.do_not_auto_apply ? 'ADVISORY ONLY' : '—'}
            </Badge>
          </div>
          <Button size="sm" className="h-7 text-xs" onClick={loadSummary} disabled={loading}>
            <RefreshCw className={cn('w-3.5 h-3.5 mr-1', loading && 'animate-spin')} />
            Refresh
          </Button>
        </CardContent>
      </Card>

      {error && <ErrorBanner message={error} onRetry={loadSummary} />}

      {/* Warnings */}
      {warnings.length > 0 && (
        <Card className="border-warning/40 bg-warning/10">
          <CardContent className="p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
            <div className="text-[11px] text-warning space-y-1">
              {warnings.map((w, i) => <p key={i}>{w}</p>)}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Overview KPI cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3">
        <KpiCard title="Total Samples" value={fmtNum(report?.total_samples, 0)} loading={loading} />
        <KpiCard title="Valid Outcomes" value={fmtNum(report?.valid_outcomes, 0)} loading={loading} />
        <KpiCard title="Overall Win Rate" value={fmtPct(report?.overall_win_rate)} loading={loading} />
        <KpiCard title="Avg R" value={fmtNum(report?.overall_avg_r)} loading={loading} />
        <KpiCard title="Surfaces" value={String(surfaceNames.length)} loading={loading} />
        <KpiCard title="Lookback" value={`${report?.lookback_days || 30}d`} loading={loading} />
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview" className="text-xs">Overview</TabsTrigger>
          <TabsTrigger value="surfaces" className="text-xs">Surfaces</TabsTrigger>
          <TabsTrigger value="vision" className="text-xs">Vision</TabsTrigger>
          <TabsTrigger value="debate" className="text-xs">Debate</TabsTrigger>
          <TabsTrigger value="strategist" className="text-xs">Strategist</TabsTrigger>
          <TabsTrigger value="weekly" className="text-xs">Weekly</TabsTrigger>
        </TabsList>

        {/* ── Overview Tab ── */}
        <TabsContent value="overview" className="space-y-4">
          <Card className="border-border/60 bg-card/50">
            <CardContent className="p-3 text-[11px] text-muted-foreground leading-relaxed">
              {loading ? <Skeleton className="h-4 w-full" /> : report?.overall_summary || 'No data.'}
            </CardContent>
          </Card>

          {surfaceNames.length > 0 && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider">Best AI Behaviors</CardTitle>
              </CardHeader>
              <CardContent className="p-3 pt-0">
                {(report?.best_ai_behaviors || []).length > 0 ? (
                  <ul className="text-[11px] text-long space-y-1">
                    {report?.best_ai_behaviors?.map((b, i) => <li key={i} className="flex items-start gap-1"><CheckCircle2 className="w-3.5 h-3.5 shrink-0 mt-0.5" />{b}</li>)}
                  </ul>
                ) : (
                  <p className="text-[11px] text-muted-foreground">No notable positive behaviors identified.</p>
                )}
              </CardContent>
            </Card>
          )}

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold uppercase tracking-wider">Worst AI Behaviors</CardTitle>
            </CardHeader>
            <CardContent className="p-3 pt-0">
              {(report?.worst_ai_behaviors || []).length > 0 ? (
                <ul className="text-[11px] text-short space-y-1">
                  {report?.worst_ai_behaviors?.map((b, i) => <li key={i} className="flex items-start gap-1"><XCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{b}</li>)}
                </ul>
              ) : (
                <p className="text-[11px] text-muted-foreground">No notable negative behaviors identified.</p>
              )}
            </CardContent>
          </Card>

          {/* Recommendations */}
          {recs.length > 0 && (
            <Card className="border-warning/40 bg-warning/5">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider flex items-center gap-2">
                  <AlertTriangle className="w-3.5 h-3.5 text-warning" />
                  Recommendations
                  <Badge variant="outline" className="text-[9px] text-warning border-warning/40">DO NOT AUTO-APPLY</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-3 pt-0">
                <ul className="text-[11px] space-y-1">
                  {recs.map((r, i) => <li key={i} className="flex items-start gap-1"><span className="text-warning">•</span>{r}</li>)}
                </ul>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ── Surfaces Tab ── */}
        <TabsContent value="surfaces" className="space-y-4">
          {loading ? (
            <div className="space-y-3">
              {[1, 2, 3].map(i => <Skeleton key={i} className="h-24 w-full" />)}
            </div>
          ) : surfaceNames.length === 0 ? (
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-6 text-center text-[11px] text-muted-foreground">No surface data available.</CardContent>
            </Card>
          ) : (
            surfaceNames.map(name => {
              const m = surfaces[name];
              const Icon = SURFACE_ICONS[name] || Bot;
              return (
                <Card key={name} className="border-border/60 bg-card/50">
                  <CardContent className="p-3 space-y-2">
                    <div className="flex items-center gap-2">
                      <Icon className="w-4 h-4 text-primary" />
                      <span className="text-xs font-semibold">{name}</span>
                      <Badge variant="outline" className="text-[9px]">{m.sample_count} samples</Badge>
                      {m.valid_outcome_count > 0 && <Badge className="text-[9px] bg-long/20 text-long">{m.valid_outcome_count} outcomes</Badge>}
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2 text-[10px]">
                      <div><span className="text-muted-foreground">Win Rate+ </span><span className="font-mono">{fmtPct(m.win_rate_when_positive)}</span></div>
                      <div><span className="text-muted-foreground">Avg R+ </span><span className="font-mono">{fmtNum(m.avg_r_when_positive)}</span></div>
                      <div><span className="text-muted-foreground">Blocks </span><span className={cn('font-mono', m.useful_block_count > 0 && 'text-long')}>{m.block_count}</span></div>
                      <div><span className="text-muted-foreground">Useful </span><span className="font-mono text-long">{m.useful_block_count}</span></div>
                      <div><span className="text-muted-foreground">False </span><span className={cn('font-mono', m.false_block_count > 0 && 'text-short')}>{m.false_block_count}</span></div>
                      <div><span className="text-muted-foreground">Harmful </span><span className={cn('font-mono', m.harmful_allow_count > 0 && 'text-short')}>{m.harmful_allow_count}</span></div>
                      <div><span className="text-muted-foreground">Parse Fail </span><span className="font-mono">{fmtPct(m.parse_failure_rate)}</span></div>
                      <div><span className="text-muted-foreground">Stale </span><span className="font-mono">{fmtPct(m.stale_context_rate)}</span></div>
                    </div>
                    {m.notes?.length > 0 && (
                      <div className="text-[10px] text-warning space-y-0.5">
                        {m.notes.map((n, i) => <p key={i}>{n}</p>)}
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })
          )}
        </TabsContent>

        {/* ── Vision Tab ── */}
        <TabsContent value="vision">
          <Card className="border-border/60 bg-card/50">
            <CardContent className="p-3 space-y-3">
              <div className="flex items-center gap-2">
                <Eye className="w-4 h-4 text-primary" />
                <span className="text-xs font-semibold">Vision Accuracy</span>
              </div>
              {loading ? <Skeleton className="h-20 w-full" /> : visionData ? (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[10px]">
                  <div><span className="text-muted-foreground">Samples </span><span className="font-mono">{fmtNum(visionData.sample_count as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Valid </span><span className="font-mono">{fmtNum(visionData.valid_outcome_count as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Confirms WR </span><span className="font-mono">{fmtPct(visionData.confirms_win_rate as number)}</span></div>
                  <div><span className="text-muted-foreground">Contradicts LR </span><span className="font-mono">{fmtPct(visionData.contradicts_loss_rate as number)}</span></div>
                  <div><span className="text-muted-foreground">Stale Rate </span><span className={cn('font-mono', (visionData.stale_vision_rate as number || 0) > 0.3 && 'text-short')}>{fmtPct(visionData.stale_vision_rate as number)}</span></div>
                  <div><span className="text-muted-foreground">Right Edge </span><span className="font-mono">{fmtPct(visionData.right_edge_reliability as number)}</span></div>
                  <div><span className="text-muted-foreground">Parse Fail </span><span className="font-mono">{fmtNum(visionData.parse_failures as number, 0)}</span></div>
                </div>
              ) : (
                <p className="text-[11px] text-muted-foreground">No Vision data.</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Debate Tab ── */}
        <TabsContent value="debate">
          <Card className="border-border/60 bg-card/50">
            <CardContent className="p-3 space-y-3">
              <div className="flex items-center gap-2">
                <GitCompare className="w-4 h-4 text-primary" />
                <span className="text-xs font-semibold">Debate Gate Metrics</span>
              </div>
              {loading ? <Skeleton className="h-20 w-full" /> : debateData ? (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[10px]">
                  <div><span className="text-muted-foreground">Samples </span><span className="font-mono">{fmtNum(debateData.sample_count as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Valid </span><span className="font-mono">{fmtNum(debateData.valid_outcome_count as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Total Blocks </span><span className="font-mono">{fmtNum(debateData.total_blocks as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Useful </span><span className="font-mono text-long">{fmtNum(debateData.useful_blocks as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">False Blocks </span><span className={cn('font-mono', (debateData.false_blocks as number || 0) > 0 && 'text-short')}>{fmtNum(debateData.false_blocks as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Harmful Allows </span><span className={cn('font-mono', (debateData.harmful_allows as number || 0) > 0 && 'text-short')}>{fmtNum(debateData.harmful_allows as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Parse Fail </span><span className="font-mono">{fmtNum(debateData.parse_failures as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Safety Fallback </span><span className="font-mono">{fmtNum(debateData.safety_fallbacks as number, 0)}</span></div>
                </div>
              ) : (
                <p className="text-[11px] text-muted-foreground">No Debate data.</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Strategist Tab ── */}
        <TabsContent value="strategist">
          <Card className="border-border/60 bg-card/50">
            <CardContent className="p-3 space-y-3">
              <div className="flex items-center gap-2">
                <FlaskConical className="w-4 h-4 text-primary" />
                <span className="text-xs font-semibold">Strategist Metrics</span>
              </div>
              {loading ? <Skeleton className="h-20 w-full" /> : strategistData ? (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[10px]">
                  <div><span className="text-muted-foreground">Samples </span><span className="font-mono">{fmtNum(strategistData.sample_count as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Valid </span><span className="font-mono">{fmtNum(strategistData.valid_outcome_count as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Concurs </span><span className="font-mono">{fmtNum(strategistData.concurs as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Concur WR </span><span className="font-mono">{fmtPct(strategistData.concur_win_rate as number)}</span></div>
                  <div><span className="text-muted-foreground">Objects </span><span className="font-mono">{fmtNum(strategistData.objects as number, 0)}</span></div>
                  <div><span className="text-muted-foreground">Object Accuracy </span><span className="font-mono">{fmtPct(strategistData.object_accuracy as number)}</span></div>
                  <div><span className="text-muted-foreground">False Objections </span><span className={cn('font-mono', (strategistData.false_objections as number || 0) > 0 && 'text-short')}>{fmtNum(strategistData.false_objections as number, 0)}</span></div>
                </div>
              ) : (
                <p className="text-[11px] text-muted-foreground">No Strategist data.</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Weekly Tab ── */}
        <TabsContent value="weekly" className="space-y-4">
          <AiWeeklyReportCard />
        </TabsContent>
      </Tabs>

      {/* Footer safety notice */}
      <Card className="border-border/60 bg-muted/20">
        <CardContent className="p-3 text-[10px] text-muted-foreground flex items-center gap-2">
          <ShieldAlert className="w-3.5 h-3.5 text-amber-400 shrink-0" />
          AI Performance is advisory only. It cannot change live thresholds or execute trades.
          Recommendations require manual review. All recommendations have do_not_auto_apply=true.
        </CardContent>
      </Card>
    </div>
  );
}

function AiWeeklyReportCard() {
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);

  const generate = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/ai/evaluation/generate-weekly', { method: 'POST' });
      const data = await res.json();
      setReport(data);
    } catch { /* ignore */ } finally { setLoading(false); }
  }, []);

  return (
    <div className="space-y-3">
      <Button size="sm" className="h-7 text-xs" onClick={generate} disabled={loading}>
        {loading ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
        Generate Weekly Report
      </Button>

      {report && (
        <Card className="border-border/60 bg-card/50">
          <CardContent className="p-3 space-y-3">
            <div className="flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-primary" />
              <span className="text-xs font-semibold">{report.headline as string || 'Weekly Report'}</span>
              <Badge variant="outline" className="text-[9px]">DO NOT AUTO-APPLY</Badge>
            </div>

            <div className="text-[11px] text-muted-foreground">{report.summary as string}</div>

            {/* What AI got right */}
            {Array.isArray(report.what_ai_got_right) && (report.what_ai_got_right as string[]).length > 0 && (
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-long mb-1">Got Right</p>
                <ul className="text-[11px] space-y-0.5">
                  {(report.what_ai_got_right as string[]).map((item, i) => (
                    <li key={i} className="flex items-start gap-1"><CheckCircle2 className="w-3 h-3 text-long shrink-0 mt-0.5" />{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* What AI got wrong */}
            {Array.isArray(report.what_ai_got_wrong) && (report.what_ai_got_wrong as string[]).length > 0 && (
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-short mb-1">Got Wrong</p>
                <ul className="text-[11px] space-y-0.5">
                  {(report.what_ai_got_wrong as string[]).map((item, i) => (
                    <li key={i} className="flex items-start gap-1"><XCircle className="w-3 h-3 text-short shrink-0 mt-0.5" />{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Recommendations */}
            {Array.isArray(report.recommendations) && (report.recommendations as string[]).length > 0 && (
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-warning mb-1">Recommendations</p>
                <ul className="text-[11px] space-y-0.5">
                  {(report.recommendations as string[]).map((item, i) => (
                    <li key={i} className="flex items-start gap-1"><AlertTriangle className="w-3 h-3 text-warning shrink-0 mt-0.5" />{item}</li>
                  ))}
                </ul>
              </div>
            )}

            <p className="text-[10px] text-muted-foreground italic">Generated: {String(report.generated_at || '').slice(0, 19).replace('T', ' ')}</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
