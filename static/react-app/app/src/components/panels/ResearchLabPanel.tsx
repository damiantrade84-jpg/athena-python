import { useState, useCallback } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';
import { ErrorBanner } from '@/components/shared';
import { FlaskConical, Play, Microscope, Eye, TrendingDown, BookOpen } from 'lucide-react';
import type { FactorAttribution } from '@/types';

interface LearningStats {
  total_signals: number;
  win_rate: number;
  engines: Record<string, { signals: number; win_rate: number }>;
}

interface MetaResult {
  best_engine: string;
  best_asset_class: string;
  recommendation: string;
  timestamp: string;
}

interface AuditEntry {
  id: string;
  timestamp: string;
  level: string;
  message: string;
  source: string;
}

interface ShadowSignal {
  id: string;
  pair: string;
  direction: string;
  entry: number;
  outcome: string;
  pnl: number;
  timestamp: string;
}

interface ScoreDecay {
  engine: string;
  decay_curve: { day: number; score: number }[];
}

export default function ResearchLabPanel() {
  const { showToast } = useStore();
  const [metaResult, setMetaResult] = useState<MetaResult | null>(null);

  const { data: learningStats, loading: statsLoading, error: statsError } = useApiPoll<LearningStats>('/api/learning/stats', 0);
  const { data: lastMeta, loading: metaLoading } = useApiPoll<MetaResult>('/api/learning/last-meta', 0);
  const { data: audit, loading: auditLoading, error: auditError } = useApiPoll<AuditEntry[]>('/api/audit', 0);
  const { data: factorAttr, loading: factorLoading, error: factorError } = useApiPoll<FactorAttribution[]>('/api/factor-attribution', 0);
  const { data: shadows, loading: shadowLoading, error: shadowError } = useApiPoll<ShadowSignal[]>('/api/shadow-signals', 0);
  const { data: scoreDecay, loading: decayLoading, error: decayError } = useApiPoll<ScoreDecay[]>('/api/score-decay', 0);

  const { post: postMeta, loading: metaRunning } = useApiPost<MetaResult>();

  const runMeta = useCallback(async () => {
    const res = await postMeta('/api/learning/meta-analysis');
    if (res) {
      setMetaResult(res);
      showToast('Meta-analysis complete', 'success');
    }
  }, [postMeta, showToast]);

  return (
    <div className="space-y-4">
      <Tabs defaultValue="meta">
        <TabsList>
          <TabsTrigger value="meta" className="text-xs">Meta-Learning</TabsTrigger>
          <TabsTrigger value="audit" className="text-xs">Audit Log</TabsTrigger>
          <TabsTrigger value="factors" className="text-xs">Factor Attribution</TabsTrigger>
          <TabsTrigger value="shadows" className="text-xs">Shadow Signals</TabsTrigger>
          <TabsTrigger value="decay" className="text-xs">Score Decay</TabsTrigger>
        </TabsList>

        <TabsContent value="meta" className="mt-2 space-y-4">
          {statsError && <ErrorBanner message={statsError} />}
          <div className="grid grid-cols-4 gap-3">
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-3">
                <p className="text-[10px] uppercase text-muted-foreground">Total Signals</p>
                {statsLoading ? <Skeleton className="h-6 w-16 mt-1" /> : (
                  <p className="text-xl font-mono font-bold">{learningStats?.total_signals || 0}</p>
                )}
              </CardContent>
            </Card>
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-3">
                <p className="text-[10px] uppercase text-muted-foreground">Win Rate</p>
                {statsLoading ? <Skeleton className="h-6 w-16 mt-1" /> : (
                  <p className="text-xl font-mono font-bold">{((learningStats?.win_rate || 0) * 100).toFixed(1)}%</p>
                )}
              </CardContent>
            </Card>
          </div>

          {learningStats?.engines && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold">By Engine</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-4 gap-3">
                  {Object.entries(learningStats.engines).map(([engine, data]) => (
                    <div key={engine} className="p-3 rounded-md bg-muted/30">
                      <p className="text-[10px] uppercase text-muted-foreground">{engine}</p>
                      <p className="text-lg font-mono font-bold">{data.signals}</p>
                      <p className="text-[10px] text-muted-foreground">WR: {(data.win_rate * 100).toFixed(1)}%</p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <FlaskConical className="w-4 h-4 text-primary" />
                Meta-Analysis
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Button size="sm" className="gap-1 text-xs" onClick={runMeta} disabled={metaRunning}>
                <Play className="w-3 h-3" />
                {metaRunning ? 'Running...' : 'Run Analysis'}
              </Button>
              {(metaResult || lastMeta) && (
                <div className="p-3 rounded-md bg-muted/30 space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono font-bold">Best Engine:</span>
                    <Badge variant="outline" className="text-[10px]">{(metaResult || lastMeta)?.best_engine}</Badge>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono font-bold">Best Asset:</span>
                    <Badge variant="outline" className="text-[10px]">{(metaResult || lastMeta)?.best_asset_class}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">{(metaResult || lastMeta)?.recommendation}</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="audit" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-primary" />
                Audit Log
              </CardTitle>
            </CardHeader>
            <CardContent>
              {auditError && <ErrorBanner message={auditError} />}
              <ScrollArea className="h-[500px]">
                {auditLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                  </div>
                ) : audit && audit.length > 0 ? (
                  <div className="space-y-1">
                    {audit.map(entry => (
                      <div key={entry.id} className="flex items-start gap-2 p-2 rounded-md bg-muted/30">
                        <Badge variant={entry.level === 'error' ? 'destructive' : entry.level === 'warning' ? 'outline' : 'secondary'} className="text-[9px] h-4 shrink-0 mt-0.5">
                          {entry.level}
                        </Badge>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs">{entry.message}</p>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-[10px] text-muted-foreground">{entry.source}</span>
                            <span className="text-[10px] text-muted-foreground">{new Date(entry.timestamp).toLocaleString()}</span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center text-muted-foreground py-12 text-sm">No audit entries</div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="factors" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Microscope className="w-4 h-4 text-primary" />
                Factor Attribution
              </CardTitle>
            </CardHeader>
            <CardContent>
              {factorError && <ErrorBanner message={factorError} />}
              {factorLoading ? (
                <Skeleton className="h-20 w-full" />
              ) : factorAttr && factorAttr.length > 0 ? (
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border/40">
                      <th className="text-[10px] uppercase py-2 text-muted-foreground">Factor</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">IC Score</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Win Rate</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Trades</th>
                    </tr>
                  </thead>
                  <tbody>
                    {factorAttr.map(f => (
                      <tr key={f.factor} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                        <td className="py-2 text-xs font-mono font-bold">{f.factor}</td>
                        <td className="py-2 text-xs font-mono text-right">{f.ic_score.toFixed(3)}</td>
                        <td className="py-2 text-xs font-mono text-right">{(f.win_rate * 100).toFixed(1)}%</td>
                        <td className="py-2 text-xs font-mono text-right">{f.trades}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No factor attribution data</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="shadows" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Eye className="w-4 h-4 text-primary" />
                Shadow Signals
              </CardTitle>
            </CardHeader>
            <CardContent>
              {shadowError && <ErrorBanner message={shadowError} />}
              <ScrollArea className="h-[500px]">
                {shadowLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                  </div>
                ) : shadows && shadows.length > 0 ? (
                  <div className="space-y-1">
                    {shadows.map(s => (
                      <div key={s.id} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                        <div className="flex items-center gap-2">
                          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${s.direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                            {s.direction}
                          </span>
                          <span className="text-xs font-mono">{s.pair}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <Badge variant="outline" className="text-[10px]">{s.outcome}</Badge>
                          <span className={`text-xs font-mono font-bold ${s.pnl >= 0 ? 'text-long' : 'text-short'}`}>
                            {s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(2)}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center text-muted-foreground py-12 text-sm">No shadow signals</div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="decay" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <TrendingDown className="w-4 h-4 text-primary" />
                Score Decay
              </CardTitle>
            </CardHeader>
            <CardContent>
              {decayError && <ErrorBanner message={decayError} />}
              {decayLoading ? (
                <Skeleton className="h-[300px] w-full" />
              ) : scoreDecay && scoreDecay.length > 0 ? (
                <div className="h-[300px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={scoreDecay[0]?.decay_curve || []}>
                      <defs>
                        <linearGradient id="decayGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="hsl(var(--short))" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="hsl(var(--short))" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="day" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} width={40} />
                      <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '6px', fontSize: '11px' }} />
                      <Area type="monotone" dataKey="score" stroke="hsl(var(--short))" strokeWidth={2} fill="url(#decayGrad)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No decay data</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
