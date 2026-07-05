import { useCallback, useMemo, useState } from 'react';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorBanner } from '@/components/shared';
import { FlaskConical, ShieldCheck } from 'lucide-react';

interface Suggestion {
  id?: string;
  timestamp?: string;
  engine?: string;
  symbol?: string;
  timeframe?: string;
  suggestion_type?: string;
  title?: string;
  summary?: string;
  score_delta?: number;
  status?: string;
  risk_flags?: string[] | string;
  baseline_metrics?: Record<string, unknown>;
  candidate_metrics?: Record<string, unknown>;
  patch_path?: string;
  rejection_reason?: string;
}

interface FreshnessRow {
  symbol?: string;
  timeframe?: string;
  freshness_score?: number;
  blocking_issues?: string;
  warnings?: string;
  recommended_action?: string;
}

interface SuggestionsResponse {
  research_only?: boolean;
  suggestions?: Suggestion[];
  error?: string;
}

interface FreshnessResponse {
  research_only?: boolean;
  freshness?: FreshnessRow[];
}

function statusBadge(status?: string) {
  const s = (status || 'new').toLowerCase();
  if (s === 'accepted_research') return 'default';
  if (s === 'rejected') return 'destructive';
  if (s === 'promoted_to_review') return 'secondary';
  return 'outline';
}

export default function EdgeLabSuggestionsPanel() {
  const [engineFilter, setEngineFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');

  const query = useMemo(() => {
    const p = new URLSearchParams();
    if (engineFilter !== 'all') p.set('engine', engineFilter);
    if (statusFilter !== 'all') p.set('status', statusFilter);
    p.set('limit', '100');
    return `/api/edgelab/suggestions?${p.toString()}`;
  }, [engineFilter, statusFilter]);

  const { data, loading, error, refresh } = useApiPoll<SuggestionsResponse>(query, 30000);
  const { data: freshData, loading: freshLoading } = useApiPoll<FreshnessResponse>('/api/edgelab/freshness', 60000);
  const { post: reviewPost, loading: reviewLoading } = useApiPost<{ ok?: boolean; error?: string }>();

  const suggestions = data?.suggestions || [];

  const onReview = useCallback(
    async (id: string, action: 'mark_reviewed' | 'reject' | 'promote') => {
      await reviewPost(`/api/edgelab/suggestions/${id}/review`, { action, reviewer: 'ui' });
      refresh();
    },
    [reviewPost, refresh],
  );

  return (
    <div className="p-4 space-y-4 max-w-6xl mx-auto">
      <div className="flex items-center gap-3">
        <FlaskConical className="h-6 w-6 text-amber-500" />
        <div>
          <h1 className="text-xl font-semibold">EdgeLab Suggestions</h1>
          <p className="text-sm text-muted-foreground flex items-center gap-1">
            <ShieldCheck className="h-3.5 w-3.5" /> Research-only — no live trading changes
          </p>
        </div>
        <Badge variant="outline" className="ml-auto">RESEARCH</Badge>
      </div>

      {error && <ErrorBanner message={String(error)} />}

      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Data freshness</CardTitle>
        </CardHeader>
        <CardContent>
          {freshLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : (
            <ScrollArea className="h-24">
              <div className="space-y-1 text-xs">
                {(freshData?.freshness || []).slice(0, 8).map((row, i) => (
                  <div key={i} className="flex gap-2 flex-wrap">
                    <span className="font-medium">{row.symbol} {row.timeframe}</span>
                    <span>score: {row.freshness_score ?? '—'}</span>
                    <span className="text-muted-foreground">{row.recommended_action}</span>
                  </div>
                ))}
                {!freshData?.freshness?.length && (
                  <span className="text-muted-foreground">No freshness records yet. Run EdgeLab daemon.</span>
                )}
              </div>
            </ScrollArea>
          )}
        </CardContent>
      </Card>

      <div className="flex gap-2 flex-wrap">
        <Select value={engineFilter} onValueChange={setEngineFilter}>
          <SelectTrigger className="w-40 h-8 text-xs"><SelectValue placeholder="Engine" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All engines</SelectItem>
            <SelectItem value="engine_b">Engine B</SelectItem>
            <SelectItem value="cascade">Cascade</SelectItem>
            <SelectItem value="engine_a">Engine A</SelectItem>
            <SelectItem value="edgelab">EdgeLab</SelectItem>
          </SelectContent>
        </Select>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-40 h-8 text-xs"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="new">New</SelectItem>
            <SelectItem value="needs_review">Needs review</SelectItem>
            <SelectItem value="accepted_research">Accepted research</SelectItem>
            <SelectItem value="rejected">Rejected</SelectItem>
            <SelectItem value="promoted_to_review">Promoted to review</SelectItem>
          </SelectContent>
        </Select>
        <Button size="sm" variant="outline" onClick={() => refresh()}>Refresh</Button>
      </div>

      {loading ? (
        <Skeleton className="h-48 w-full" />
      ) : (
        <div className="space-y-3">
          {suggestions.map((s) => (
            <Card key={s.id} className="border-border/60 bg-card/40">
              <CardHeader className="py-3 px-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <CardTitle className="text-sm">{s.title || 'Suggestion'}</CardTitle>
                    <p className="text-xs text-muted-foreground mt-1">{s.summary}</p>
                  </div>
                  <Badge variant={statusBadge(s.status)}>{s.status || 'new'}</Badge>
                </div>
              </CardHeader>
              <CardContent className="px-4 pb-3 text-xs space-y-2">
                <div className="flex flex-wrap gap-2 text-muted-foreground">
                  {s.engine && <span>engine: {s.engine}</span>}
                  {s.symbol && <span>{s.symbol}</span>}
                  {s.timeframe && <span>{s.timeframe}</span>}
                  {s.suggestion_type && <span>{s.suggestion_type}</span>}
                  {s.score_delta != null && <span>Δscore: {s.score_delta}</span>}
                </div>
                {s.rejection_reason && (
                  <p className="text-destructive">Rejected: {s.rejection_reason}</p>
                )}
                <div className="flex gap-2 pt-1">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={reviewLoading || !s.id}
                    onClick={() => s.id && onReview(s.id, 'mark_reviewed')}
                  >
                    Mark reviewed
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={reviewLoading || !s.id}
                    onClick={() => s.id && onReview(s.id, 'reject')}
                  >
                    Reject
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={reviewLoading || !s.id}
                    onClick={() => s.id && onReview(s.id, 'promote')}
                  >
                    Promote to review branch
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
          {!suggestions.length && (
            <p className="text-sm text-muted-foreground text-center py-8">
              No suggestions yet. Run: python research/edgelab/edgelab_daemon.py --dry-run --once
            </p>
          )}
        </div>
      )}
    </div>
  );
}
