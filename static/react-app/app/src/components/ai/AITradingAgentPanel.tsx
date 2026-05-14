import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Eye,
  FileText,
  GitCompare,
  Info,
  Loader2,
  MessageSquare,
  RefreshCw,
  Send,
  ShieldAlert,
  Wrench,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { ErrorBanner } from '@/components/shared';
import { getAiStrategistBrief, postAiTradeChat } from '@/lib/apiClient';
import { cn } from '@/lib/utils';
import type {
  AiDataCheckedSummary,
  AiMarketIntelligenceSummary,
  AiSelectedSignalSummary,
  AiStrategistBriefResponse,
  AiStrategistSummary,
  AiToolCallSummary,
  AiTradeChatResponse,
  AiVisionSummary,
} from '@/types/athena';

const DEFAULT_SEED =
  'Review this trade. What supports it, what argues against it, and what would confirm or invalidate it?';

const QUICK_PROMPTS = [
  'Give me the market read',
  'What would confirm this?',
  'What invalidates this?',
  'Do similar setups support this?',
  'Does Vision agree?',
  'What does the Strategist say?',
  'What is the weakest part?',
];

type ChatMessage =
  | { id: string; role: 'user'; content: string; createdAt: string }
  | { id: string; role: 'assistant'; content: string; createdAt: string; response: AiTradeChatResponse };

function decisionClass(decision?: string): string {
  switch (decision) {
    case 'VALID_SETUP':
      return 'bg-long/20 text-long border-long/30';
    case 'BLOCKED_BY_RISK':
    case 'NO_TRADE':
      return 'bg-short/20 text-short border-short/30';
    case 'WAIT_FOR_CONFIRMATION':
    case 'WATCHLIST':
      return 'bg-warning/20 text-warning border-warning/30';
    case 'DATA_INSUFFICIENT':
      return 'bg-muted/60 text-muted-foreground border-border/60';
    default:
      return 'bg-muted/40 text-muted-foreground border-border/60';
  }
}

function ListBlock({ title, items }: { title: string; items?: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="space-y-1">
      <p className="text-[10px] uppercase text-muted-foreground">{title}</p>
      <div className="flex flex-wrap gap-1">
        {items.slice(0, 12).map((item, index) => (
          <Badge key={`${item}-${index}`} variant="outline" className="text-[10px] max-w-full break-words whitespace-normal">
            {item}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function asList(items?: unknown): string[] {
  if (!Array.isArray(items)) return [];
  return items.map((item) => {
    if (typeof item === 'string') return item;
    if (item == null) return '';
    try {
      return JSON.stringify(item);
    } catch {
      return String(item);
    }
  }).filter(Boolean);
}

function formatValue(value: unknown): string {
  if (value == null || value === '') return '-';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function statusClass(status?: string): string {
  if (status === 'ok' || status === 'fresh' || status === 'allowed') return 'bg-long/15 text-long border-long/30';
  if (status === 'error' || status === 'stale' || status === 'blocked') return 'bg-short/15 text-short border-short/30';
  if (status === 'skipped' || status === 'partial' || status === 'unavailable') return 'bg-warning/15 text-warning border-warning/30';
  return 'bg-muted/40 text-muted-foreground border-border/60';
}

function SectionCard({
  title,
  children,
  tone = 'default',
}: {
  title: string;
  children?: string | null;
  tone?: 'default' | 'warning' | 'short' | 'long';
}) {
  if (!children) return null;
  const toneClass =
    tone === 'warning'
      ? 'border-warning/40 bg-warning/5'
      : tone === 'short'
        ? 'border-short/40 bg-short/5'
        : tone === 'long'
          ? 'border-long/40 bg-long/5'
          : 'border-border/50 bg-muted/20';
  return (
    <div className={cn('rounded-md border p-2 space-y-1', toneClass)}>
      <p className="text-[10px] uppercase text-muted-foreground">{title}</p>
      <p className="text-xs leading-relaxed whitespace-pre-wrap break-words">{children}</p>
    </div>
  );
}

function DetailGrid({ rows }: { rows: Array<[string, unknown]> }) {
  const visibleRows = rows.filter(([, value]) => value != null && value !== '');
  if (visibleRows.length === 0) return null;
  return (
    <div className="grid grid-cols-2 gap-2 text-xs">
      {visibleRows.map(([label, value]) => (
        <div key={label} className="flex items-center justify-between gap-2 rounded border border-border/30 bg-muted/10 px-2 py-1">
          <span className="text-[10px] text-muted-foreground capitalize">{label.replace(/_/g, ' ')}</span>
          <span className="font-mono text-right break-words">{formatValue(value)}</span>
        </div>
      ))}
    </div>
  );
}

function InfoCard({
  title,
  icon,
  rows,
  warnings,
}: {
  title: string;
  icon: ReactNode;
  rows: Array<[string, unknown]>;
  warnings?: string[];
}) {
  const hasRows = rows.some(([, value]) => value != null && value !== '');
  if (!hasRows && (!warnings || warnings.length === 0)) return null;
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold flex items-center gap-1">
            {icon} {title}
          </span>
        </div>
        <DetailGrid rows={rows} />
        <ListBlock title="Warnings" items={warnings} />
      </CardContent>
    </Card>
  );
}

function ExpandableSection({
  title,
  icon,
  children,
  defaultOpen = false,
}: {
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-md border border-border/50 bg-muted/10">
      <CollapsibleTrigger asChild>
        <Button type="button" variant="ghost" className="h-8 w-full justify-between px-2 text-xs">
          <span className="inline-flex items-center gap-1">
            {icon}
            {title}
          </span>
          {open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="px-2 pb-2">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

function MarketIntelligenceCard({ data }: { data?: AiMarketIntelligenceSummary }) {
  if (!data) return null;
  const macroRegime = data.risk_regime || data.macro_regime?.risk_regime;
  return (
    <InfoCard
      title="Market Intelligence"
      icon={<ShieldAlert className="w-3.5 h-3.5 text-warning" />}
      rows={[
        ['freshness_status', data.freshness_status],
        ['risk_regime', macroRegime],
        ['calendar_72h', asList(data.calendar_within_72h || data.macro_regime?.calendar_within_72h).join('; ')],
        ['source_status', Object.keys(data.source_status || {}).length ? data.source_status : null],
      ]}
      warnings={asList(data.warnings)}
    />
  );
}

function VisionSummaryCard({ data }: { data?: AiVisionSummary }) {
  if (!data) return null;
  return (
    <InfoCard
      title="Vision Summary"
      icon={<Eye className="w-3.5 h-3.5 text-primary" />}
      rows={[
        ['right_edge_status', data.right_edge_status],
        ['tf_alignment', data.tf_alignment],
        ['freshness_status', data.freshness_status],
        ['execution_context', data.allowed_for_execution_context ? 'allowed advisory context' : 'not allowed'],
        ['style_ratings', Object.keys(data.style_ratings || {}).length ? data.style_ratings : null],
        ['visible_obstacles', asList(data.visible_obstacles).join('; ')],
        ['memo', data.memo],
      ]}
    />
  );
}

function SelectedSignalSummary({
  signal,
  symbol,
  traceId,
}: {
  signal?: AiSelectedSignalSummary | null;
  symbol?: string | null;
  traceId?: string | null;
}) {
  const resolvedSymbol = signal?.symbol || symbol;
  const resolvedTraceId = signal?.trace_id || traceId;
  const hasSignal = !!(resolvedSymbol || resolvedTraceId);
  if (!hasSignal) {
    return (
      <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-[11px] text-warning space-y-1">
        <div className="flex items-center gap-2 font-medium">
          <AlertTriangle className="w-3.5 h-3.5" />
          No selected signal
        </div>
        <p className="leading-relaxed">
          Select a scan or cockpit signal before asking for a trade-specific review. The agent stays read-only and will not infer an executable setup from an empty context.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-md border border-border/50 bg-muted/10 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold">Selected Signal</p>
        <Badge variant="outline" className="text-[10px] max-w-[220px] truncate">{resolvedSymbol || 'linked trace'}</Badge>
      </div>
      <DetailGrid
        rows={[
          ['trace_id', resolvedTraceId],
          ['direction', signal?.direction],
          ['engine', signal?.engine],
          ['state', signal?.state],
          ['score', signal?.score],
          ['threshold', signal?.threshold],
          ['rr', signal?.rr],
          ['entry', signal?.entry],
          ['sl', signal?.sl],
          ['tp', signal?.tp],
          ['style', signal?.style],
        ]}
      />
    </div>
  );
}

function DataCheckedCard({
  data,
  factsUsed,
  missingData,
}: {
  data?: AiDataCheckedSummary;
  factsUsed: string[];
  missingData: string[];
}) {
  const sources = asList(data?.sources);
  const warnings = asList(data?.warnings);
  const checkRows: Array<[string, unknown]> = [
    ['signal', data?.signal],
    ['market_intelligence', data?.market_intelligence],
    ['vision', data?.vision],
    ['similar_setups', data?.similar_setups],
    ['strategist', data?.strategist],
    ['freshness', data?.freshness],
  ];
  return (
    <ExpandableSection title="Data checked" icon={<Info className="w-3.5 h-3.5 text-primary" />} defaultOpen>
      <div className="space-y-2">
        <DetailGrid rows={checkRows} />
        {!data && (
          <p className="text-[11px] text-muted-foreground">
            Backend did not return a structured data_checked block; showing facts and missing fields from the chat summary.
          </p>
        )}
        <ListBlock title="Sources" items={sources} />
        <ListBlock title="Facts used" items={factsUsed} />
        <ListBlock title="Missing data" items={missingData} />
        <ListBlock title="Warnings" items={warnings} />
      </div>
    </ExpandableSection>
  );
}

function ToolCallsCard({ toolCalls }: { toolCalls?: AiToolCallSummary[] }) {
  const calls = Array.isArray(toolCalls) ? toolCalls : [];
  return (
    <ExpandableSection title="Tool transparency" icon={<Wrench className="w-3.5 h-3.5 text-primary" />}>
      {calls.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          No structured tool_calls were returned by the backend for this turn.
        </p>
      ) : (
        <div className="space-y-2">
          {calls.map((call, index) => {
            const name = call.name || call.tool || `tool_${index + 1}`;
            const summary = call.output_summary || call.summary || call.reason || call.error;
            return (
              <div key={`${name}-${index}`} className="rounded border border-border/40 bg-card/40 p-2 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium">{name}</span>
                  <Badge className={cn('text-[10px] border', statusClass(call.status))}>{call.status || 'unknown'}</Badge>
                </div>
                <DetailGrid rows={[['duration_ms', call.duration_ms], ['args', call.args || call.input]]} />
                {summary && <p className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words">{summary}</p>}
              </div>
            );
          })}
        </div>
      )}
    </ExpandableSection>
  );
}

function SafetyCard({ safety, riskWarning }: { safety?: AiTradeChatResponse['safety']; riskWarning?: string | null }) {
  const warnings = asList(safety?.warnings);
  const blockedReasons = asList(safety?.blocked_reasons);
  const advisoryOnly = safety?.advisory_only !== false;
  return (
    <div className="rounded-md border border-warning/40 bg-warning/5 p-2 text-[11px] text-warning space-y-2">
      <div className="flex items-center gap-2 font-medium">
        <ShieldAlert className="w-3.5 h-3.5" />
        Safety note
      </div>
      <p className="leading-relaxed">
        {safety?.note || 'AI is advisory/read-only. Execution still requires ATHENA risk, freshness, guardian, and trade gates.'}
      </p>
      <DetailGrid
        rows={[
          ['advisory_only', advisoryOnly],
          ['can_execute', safety?.can_execute],
          ['execution_blocked', safety?.execution_blocked],
        ]}
      />
      <SectionCard title="Risk warning" tone="short">{riskWarning}</SectionCard>
      <ListBlock title="Safety warnings" items={warnings} />
      <ListBlock title="Blocked reasons" items={blockedReasons} />
    </div>
  );
}

function StrategistSummaryCard({ summary }: { summary?: AiStrategistSummary | null }) {
  if (!summary) return null;
  return (
    <ExpandableSection title="Strategist summary" icon={<FileText className="w-3.5 h-3.5 text-primary" />}>
      <div className="space-y-2">
        <SectionCard title="Headline" tone="long">{summary.headline}</SectionCard>
        <DetailGrid rows={[['macro_regime', summary.macro_regime]]} />
        <ListBlock title="Key risks" items={asList(summary.key_risks)} />
        <ListBlock title="Avoid conditions" items={asList(summary.avoid_conditions)} />
        <ListBlock title="Data warnings" items={asList(summary.data_warnings)} />
      </div>
    </ExpandableSection>
  );
}

function AssistantResponse({
  response,
  symbol,
  traceId,
}: {
  response: AiTradeChatResponse;
  symbol?: string | null;
  traceId?: string | null;
}) {
  const contradictionFlags = asList(response.contradiction_flags);
  const contradictions = asList(response.contradictions);
  const supports = asList(response.supports);
  const confirmationNeeded = asList(response.confirmation_needed);
  const missingData = asList(response.missing_data);
  const factsUsed = asList(response.facts_used);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge className={cn('text-[10px] border', decisionClass(response.decision))}>
          {response.decision || 'NO_DECISION'}
        </Badge>
        {contradictionFlags.length > 0 || contradictions.length > 0 ? (
          <Badge className="bg-short/15 text-short border-short/30 text-[10px]">
            <AlertTriangle className="w-3 h-3 mr-1" />
            {Math.max(contradictionFlags.length, contradictions.length)} flags
          </Badge>
        ) : (
          <Badge className="bg-long/15 text-long border-long/30 text-[10px]">
            <CheckCircle2 className="w-3 h-3 mr-1" />
            No contradictions
          </Badge>
        )}
        {response.final_action && (
          <Badge variant="outline" className="text-[10px]">
            Action: {response.final_action}
          </Badge>
        )}
        {response.compared_symbol && (
          <Badge variant="outline" className="text-[10px]">
            Compared: {response.compared_symbol}
          </Badge>
        )}
      </div>

      <SelectedSignalSummary signal={response.selected_signal} symbol={response.symbol || symbol} traceId={response.trace_id || traceId} />

      <div className="grid gap-2">
        <SectionCard title="Market read">{response.market_read}</SectionCard>
        <SectionCard title="Trade thesis">{response.trade_thesis}</SectionCard>
        <ListBlock title="Supports" items={supports} />
        <ListBlock title="Contradictions" items={contradictions} />
        <ListBlock title="Confirmation needed" items={confirmationNeeded} />
        <SectionCard title="Invalidation" tone="warning">{response.invalidation}</SectionCard>
        <SectionCard title="Historical analogue summary">{response.historical_analogue_summary}</SectionCard>
        <SectionCard title="Compare summary">{response.compare_summary}</SectionCard>
        <SectionCard title="Final action" tone="long">{response.final_action}</SectionCard>
      </div>

      <MarketIntelligenceCard data={response.market_intelligence} />
      <VisionSummaryCard data={response.vision_summary} />

      <ExpandableSection title="Assistant narrative" icon={<MessageSquare className="w-3.5 h-3.5 text-primary" />} defaultOpen>
        <div className="text-xs leading-relaxed whitespace-pre-wrap break-words rounded-md border border-border/40 bg-muted/20 p-3">
          {response.answer || 'No assistant narrative returned.'}
        </div>
      </ExpandableSection>

      <DataCheckedCard data={response.data_checked} factsUsed={factsUsed} missingData={missingData} />
      <ToolCallsCard toolCalls={response.tool_calls} />
      <StrategistSummaryCard summary={response.strategist_summary} />
      <ListBlock title="Contradiction flags" items={contradictionFlags} />
      <SafetyCard safety={response.safety} riskWarning={response.risk_warning} />
    </div>
  );
}

function StrategistBriefCard({
  brief,
  loading,
  error,
  onRefresh,
}: {
  brief: AiStrategistBriefResponse | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  return (
    <div className="space-y-3">
      {error && <ErrorBanner message={error} />}
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold flex items-center gap-1">
            <FileText className="w-3.5 h-3.5 text-primary" />
            Strategist Brief
          </p>
          <p className="text-[10px] text-muted-foreground">
            {brief?.generated_at ? new Date(brief.generated_at).toLocaleString() : 'Read-only Phase 2 strategist context'}
          </p>
        </div>
        <Button type="button" size="sm" variant="outline" className="h-7 text-[10px] gap-1" disabled={loading} onClick={onRefresh}>
          <RefreshCw className={loading ? 'w-3.5 h-3.5 animate-spin' : 'w-3.5 h-3.5'} />
          Refresh
        </Button>
      </div>

      {loading && !brief && (
        <div className="rounded-md border border-border/40 bg-muted/20 p-3 text-xs text-muted-foreground">
          Loading strategist brief...
        </div>
      )}

      {brief && (
        <>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline" className="text-[10px]">{brief.schema_version || 'strategist_brief'}</Badge>
            <Badge variant="outline" className="text-[10px]">Scope: {brief.asset_scope || 'all'}</Badge>
            <Badge className="bg-muted/40 text-muted-foreground text-[10px]">Macro: {brief.macro_regime || 'unknown'}</Badge>
          </div>
          <SectionCard title="Headline" tone="long">{brief.headline}</SectionCard>
          <SectionCard title="Full brief">{brief.full_brief}</SectionCard>
          <SectionCard title="Open positions">{brief.open_positions_summary}</SectionCard>
          <SectionCard title="Yesterday outcomes">{brief.yesterday_outcomes}</SectionCard>
          <ListBlock title="Key risks" items={asList(brief.key_risks)} />
          <ListBlock title="Watchlist" items={asList(brief.watchlist)} />
          <ListBlock title="Avoid conditions" items={asList(brief.avoid_conditions)} />
          <ListBlock title="Calendar risks" items={asList(brief.calendar_risks)} />
          <ListBlock title="Data warnings" items={asList(brief.data_warnings)} />
          <SafetyCard riskWarning="Strategist output is advisory-only. ATHENA execution gates, freshness checks, and risk controls remain authoritative." />
        </>
      )}
    </div>
  );
}

export default function AITradingAgentPanel({
  symbol,
  traceId,
  seedMessage = DEFAULT_SEED,
  className,
}: {
  symbol?: string | null;
  traceId?: string | null;
  seedMessage?: string;
  className?: string;
}) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState(seedMessage);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [compareSymbol, setCompareSymbol] = useState('');
  const [brief, setBrief] = useState<AiStrategistBriefResponse | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [briefError, setBriefError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'review' | 'brief'>('review');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasSelectedSignal = !!(symbol || traceId);

  useEffect(() => {
    setSessionId(null);
    setMessages([]);
    setError(null);
    setInput(seedMessage);
    setCompareSymbol('');
  }, [symbol, traceId, seedMessage]);

  const loadBrief = useCallback(async () => {
    setBriefLoading(true);
    setBriefError(null);
    try {
      const response = await getAiStrategistBrief('all');
      setBrief(response);
    } catch (err) {
      setBriefError(err instanceof Error ? err.message : 'Strategist brief request failed');
    } finally {
      setBriefLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'brief' && !brief && !briefLoading && !briefError) {
      void loadBrief();
    }
  }, [activeTab, brief, briefError, briefLoading, loadBrief]);

  const canSend = input.trim().length > 0 && !loading;
  const contextLabel = useMemo(() => {
    if (symbol && traceId) return `${symbol} / ${traceId}`;
    if (symbol) return symbol;
    if (traceId) return traceId;
    return 'No signal selected';
  }, [symbol, traceId]);

  const send = useCallback(
    async (messageOverride?: string, options?: { compare?: string }) => {
      const message = (messageOverride ?? input).trim();
      if (!message || loading) return;

      const userMessage: ChatMessage = {
        id: `user-${Date.now()}`,
        role: 'user',
        content: message,
        createdAt: new Date().toISOString(),
      };
      setMessages((current) => [...current, userMessage]);
      setLoading(true);
      setError(null);

      try {
        const response = await postAiTradeChat({
          session_id: sessionId,
          trace_id: traceId || null,
          symbol: symbol || null,
          message,
          include_vision: true,
          include_similar_setups: true,
          compare_symbol: options?.compare || null,
        });
        setSessionId(response.session_id);
        setMessages((current) => [
          ...current,
          {
            id: `assistant-${Date.now()}`,
            role: 'assistant',
            content: response.answer,
            response,
            createdAt: response.created_at || new Date().toISOString(),
          },
        ]);
        if (!messageOverride) setInput('');
      } catch (err) {
        setMessages((current) => current.filter((messageItem) => messageItem.id !== userMessage.id));
        setError(err instanceof Error ? err.message : 'AI agent request failed');
      } finally {
        setLoading(false);
      }
    },
    [hasSelectedSignal, input, loading, sessionId, symbol, traceId],
  );

  const sendCompare = useCallback(() => {
    const target = compareSymbol.trim();
    if (!target) return;
    void send(`Compare the selected setup with ${target}. Where do they agree, conflict, and which is cleaner?`, { compare: target });
  }, [compareSymbol, send]);

  return (
    <Card className={cn('border-border/60 bg-card/50', className)}>
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-semibold flex items-center justify-between gap-2 uppercase tracking-wider">
          <span className="inline-flex items-center gap-2">
            <Bot className="w-4 h-4 text-primary" />
            AI Trading Agent
          </span>
          <Badge variant="outline" className="text-[10px] max-w-[240px] truncate">
            {contextLabel}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as 'review' | 'brief')}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="review" className="text-[11px]">Tool Chat</TabsTrigger>
            <TabsTrigger value="brief" className="text-[11px]">Strategist Brief</TabsTrigger>
          </TabsList>

          <TabsContent value="review" className="m-0 mt-3 space-y-3">
            <SelectedSignalSummary symbol={symbol} traceId={traceId} />
            {error && <ErrorBanner message={error} />}

            <div className="rounded-md border border-border/50 bg-muted/10 p-2 space-y-2">
              <div className="flex items-center gap-2">
                <GitCompare className="w-3.5 h-3.5 text-primary" />
                <Input
                  value={compareSymbol}
                  onChange={(event) => setCompareSymbol(event.target.value)}
                  placeholder="Compare symbol, e.g. BTCUSDT"
                  className="h-8 text-xs"
                  disabled={loading}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 text-[10px]"
                  disabled={loading || !hasSelectedSignal || !compareSymbol.trim()}
                  onClick={sendCompare}
                >
                  Compare
                </Button>
              </div>
              <p className="text-[10px] text-muted-foreground">
                Compare flow stays inside advisory chat; it does not call execution or threshold-changing routes.
              </p>
            </div>

            <div className="flex flex-wrap gap-1">
              {QUICK_PROMPTS.map((prompt) => (
                <Button
                  key={prompt}
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 text-[10px]"
                  disabled={loading}
                  onClick={() => send(prompt)}
                >
                  {prompt}
                </Button>
              ))}
            </div>

            <div className="space-y-2">
              <Textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                className="min-h-[96px] text-xs resize-none"
                disabled={loading}
              />
              <div className="flex items-center justify-between gap-2">
                <p className="text-[10px] text-muted-foreground">
                  Advisory only. API failures leave this draft intact.
                </p>
                <Button type="button" size="sm" className="h-8 gap-1 text-xs" disabled={!canSend} onClick={() => send()}>
                  {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                  {loading ? 'Reviewing...' : 'Send'}
                </Button>
              </div>
            </div>

            <ScrollArea className="max-h-[620px] pr-2">
              <div className="space-y-3">
                {messages.length === 0 && !loading && !error && (
                  <div className="flex items-center gap-2 text-[11px] text-muted-foreground rounded-md border border-border/40 bg-muted/10 p-3">
                    <MessageSquare className="w-3.5 h-3.5" />
                    <span>{hasSelectedSignal ? 'Ready for a read-only tool-chat review.' : 'No signal selected. You can ask general questions, but select a signal for trade-specific evidence.'}</span>
                  </div>
                )}

                {messages.map((message) => (
                  <div key={message.id} className={cn('rounded-md border p-3 space-y-2', message.role === 'user' ? 'border-primary/30 bg-primary/5' : 'border-border/50 bg-card/40')}>
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="outline" className="text-[10px]">
                        {message.role === 'user' ? 'You' : 'Assistant'}
                      </Badge>
                      <span className="text-[10px] text-muted-foreground">{new Date(message.createdAt).toLocaleTimeString()}</span>
                    </div>
                    {message.role === 'user' ? (
                      <p className="text-xs whitespace-pre-wrap break-words">{message.content}</p>
                    ) : (
                      <AssistantResponse response={message.response} symbol={symbol} traceId={traceId} />
                    )}
                  </div>
                ))}

                {loading && (
                  <div className="rounded-md border border-border/50 bg-muted/10 p-3 text-xs text-muted-foreground flex items-center gap-2">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Checking signal context, market intelligence, Vision, similar setups, and safety notes...
                  </div>
                )}
              </div>
            </ScrollArea>

            <Separator />
            <SafetyCard riskWarning="This panel is a read-only advisor. It cannot place orders, approve execution, or change config thresholds." />
          </TabsContent>

          <TabsContent value="brief" className="m-0 mt-3">
            <StrategistBriefCard
              brief={brief}
              loading={briefLoading}
              error={briefError}
              onRefresh={loadBrief}
            />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
