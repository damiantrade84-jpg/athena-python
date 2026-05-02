import { useMemo, useState, useCallback } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';
import { ErrorBanner } from '@/components/shared';
import { Dices, Upload, Sparkles, History, BarChart3, TrendingUp } from 'lucide-react';

const GAMES = [
  { value: 'lotto', label: 'SA Lotto' },
  { value: 'powerball', label: 'Powerball' },
  { value: 'daily_lotto', label: 'Daily Lotto' },
];

interface HotColdRow {
  number: number;
  count?: number;
  avg_gap_draws?: number | null;
}

interface LotteryDashboard {
  game?: string;
  total_draws: number;
  hot_numbers?: HotColdRow[];
  cold_numbers?: HotColdRow[];
  number_frequency?: HotColdRow[];
}

interface LotteryDrawRow {
  draw_date: string;
  numbers: number[];
  bonus?: number | null;
}

interface LotteryDrawsEnvelope {
  history?: LotteryDrawRow[];
}

interface LotteryFrequencyResponse {
  number_frequency?: { number: number; count?: number }[];
}

interface LotteryGenerateResponse {
  tickets?: Array<{ numbers: number[]; bonus?: number | null }>;
}

export default function LotteryLabPanel() {
  const { showToast } = useStore();
  const [activeTab, setActiveTab] = useState('overview');
  const [game, setGame] = useState('lotto');
  const [generatedTicket, setGeneratedTicket] = useState<{ numbers: number[]; bonus?: number | null } | null>(null);
  const [aiAnalysis, setAiAnalysis] = useState<string>('');
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [newDraw, setNewDraw] = useState('');
  const [drawDate, setDrawDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [optionalBonus, setOptionalBonus] = useState('');

  const qs = useMemo(() => `?game=${encodeURIComponent(game)}`, [game]);

  const { data: dashboard, loading: dashLoading, error: dashError } = useApiPoll<LotteryDashboard>(`/api/lottery/dashboard${qs}`, 0);
  const { data: freqRaw, loading: freqLoading } = useApiPoll<LotteryFrequencyResponse>(`/api/lottery/frequency${qs}`, 0);
  const { data: drawsEnvelope, loading: drawsLoading, error: drawsError, refresh: refreshDraws } =
    useApiPoll<LotteryDrawsEnvelope | LotteryDrawRow[]>(`/api/lottery/draws${qs}`, 0);

  const { post: postGenerate, loading: generating } = useApiPost<LotteryGenerateResponse>();
  const { post: postAi, loading: aiLoading } = useApiPost<Record<string, unknown>>();
  const { post: postImport, loading: importing } = useApiPost<{ imported?: number }>();

  const drawRows: LotteryDrawRow[] = useMemo(() => {
    if (!drawsEnvelope) return [];
    if (Array.isArray(drawsEnvelope)) return drawsEnvelope;
    return drawsEnvelope.history || [];
  }, [drawsEnvelope]);

  const hotColdCount = useCallback((n: HotColdRow) => n.count ?? (n as { frequency?: number }).frequency ?? 0, []);

  const mostCommonPreview = useMemo(() => {
    const hot = dashboard?.hot_numbers || [];
    if (!hot.length) return '—';
    return hot.slice(0, 6).map((h) => h.number).join(', ');
  }, [dashboard?.hot_numbers]);

  const handleGenerate = useCallback(async () => {
    const includeBonus = game !== 'daily_lotto';
    const res = await postGenerate('/api/lottery/generate', {
      game,
      mode: 'pure_random',
      ticket_count: 1,
      include_bonus: includeBonus,
    });
    const t = res?.tickets?.[0];
    if (t?.numbers?.length) {
      setGeneratedTicket({ numbers: t.numbers, bonus: t.bonus });
      showToast('Ticket generated', 'success');
    } else {
      showToast('Generate returned no ticket — check history / game rules', 'error');
    }
  }, [game, postGenerate, showToast]);

  const handleAi = useCallback(async () => {
    const res = await postAi('/api/lottery/ai-analysis', { game });
    if (!res) return;
    const raw = typeof res.raw_analysis_text === 'string' ? res.raw_analysis_text : '';
    const parsed = res.analysis != null ? JSON.stringify(res.analysis, null, 2) : '';
    setAiAnalysis(raw || parsed || JSON.stringify(res, null, 2));
  }, [game, postAi]);

  const handleImport = useCallback(async () => {
    if (!csvFile) return;
    const formData = new FormData();
    formData.append('file', csvFile);
    formData.append('game', game);
    formData.append('mode', 'append');
    const res = await fetch('/api/lottery/import', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok || data.error) {
      showToast(data.error || 'Import failed', 'error');
      return;
    }
    if (data.imported != null) {
      showToast(`Imported ${data.imported} draws`, 'success');
      refreshDraws();
    }
  }, [csvFile, game, showToast, refreshDraws]);

  const handleAddDraw = useCallback(async () => {
    const nums = newDraw.split(/[,;\s]+/).map((x) => parseInt(x.trim(), 10)).filter((n) => !Number.isNaN(n));
    if (!drawDate) {
      showToast('Draw date required', 'error');
      return;
    }
    if (nums.length < 1) {
      showToast('Enter main numbers', 'error');
      return;
    }
    const bonusRaw = optionalBonus.trim();
    const bonusNum = bonusRaw === '' ? undefined : parseInt(bonusRaw, 10);
    const body: Record<string, unknown> = { game, draw_date: drawDate, numbers: nums };
    if (bonusNum != null && !Number.isNaN(bonusNum)) body.bonus = bonusNum;

    const res = await fetch('/api/lottery/add-draw', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = (await res.json()) as { inserted?: boolean; duplicate?: boolean; error?: string };
    if (!res.ok && res.status !== 409) {
      showToast(data.error || `HTTP ${res.status}`, 'error');
      return;
    }
    if (data.inserted) {
      showToast('Draw added', 'success');
      refreshDraws();
      setNewDraw('');
      setOptionalBonus('');
    } else if (data.duplicate) {
      showToast('Draw already exists for this date — not inserted', 'info');
    } else {
      showToast(data.error || 'Add draw failed — check counts and bonus for this game', 'error');
    }
  }, [newDraw, drawDate, game, optionalBonus, showToast, refreshDraws]);

  const freqRows = freqRaw?.number_frequency || [];
  const freqData = freqRows.map((f) => ({ name: f.number, freq: f.count ?? 0 }));

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase text-muted-foreground">Game</span>
        <Select value={game} onValueChange={setGame}>
          <SelectTrigger className="w-[160px] h-8 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            {GAMES.map((g) => <SelectItem key={g.value} value={g.value}>{g.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <span className="text-[11px] text-muted-foreground">All lottery endpoints require ?game — selection applies to Overview, Frequency, History, Generate, AI.</span>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview" className="text-xs">Overview</TabsTrigger>
          <TabsTrigger value="frequency" className="text-xs">Frequency</TabsTrigger>
          <TabsTrigger value="history" className="text-xs">History</TabsTrigger>
          <TabsTrigger value="generate" className="text-xs">Generate</TabsTrigger>
          <TabsTrigger value="ai" className="text-xs">AI Analysis</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-2 space-y-4">
          {dashError && <ErrorBanner message={dashError} />}
          <div className="grid grid-cols-4 gap-3">
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-3">
                <p className="text-[10px] uppercase text-muted-foreground">Total Draws</p>
                {dashLoading ? <Skeleton className="h-6 w-16 mt-1" /> : (
                  <p className="text-xl font-mono font-bold">{dashboard?.total_draws ?? 0}</p>
                )}
              </CardContent>
            </Card>
            <Card className="border-border/60 bg-card/50 col-span-2">
              <CardContent className="p-3">
                <p className="text-[10px] uppercase text-muted-foreground">Top picks (hot, first 6)</p>
                {dashLoading ? <Skeleton className="h-6 w-full mt-1" /> : (
                  <p className="text-sm font-mono font-bold truncate">{mostCommonPreview}</p>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                  <TrendingUp className="w-4 h-4 text-long" />
                  Hot Numbers
                </CardTitle>
              </CardHeader>
              <CardContent>
                {dashLoading ? <Skeleton className="h-20 w-full" /> : (
                  <div className="flex flex-wrap gap-1">
                    {(dashboard?.hot_numbers || []).map((n) => (
                      <Badge key={n.number} variant="outline" className="text-[10px] font-mono bg-long/10 text-long border-long/40">
                        {n.number} ({hotColdCount(n)}×)
                      </Badge>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                  <TrendingUp className="w-4 h-4 text-short" />
                  Cold Numbers
                </CardTitle>
              </CardHeader>
              <CardContent>
                {dashLoading ? <Skeleton className="h-20 w-full" /> : (
                  <div className="flex flex-wrap gap-1">
                    {(dashboard?.cold_numbers || []).map((n) => (
                      <Badge key={n.number} variant="outline" className="text-[10px] font-mono bg-short/10 text-short border-short/40">
                        {n.number} ({hotColdCount(n)}×)
                      </Badge>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="frequency" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <BarChart3 className="w-4 h-4 text-primary" />
                Number Frequency
              </CardTitle>
            </CardHeader>
            <CardContent>
              {freqLoading ? <Skeleton className="h-[300px] w-full" /> : freqData.length > 0 ? (
                <div className="h-[300px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={freqData}>
                      <XAxis dataKey="name" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                      <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '6px', fontSize: '11px' }} />
                      <Bar dataKey="freq" radius={[3, 3, 0, 0]}>
                        {freqData.map((_, i) => (
                          <Cell key={i} fill="hsl(var(--primary))" />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No frequency data — import CSV or add draws.</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="history" className="mt-2 space-y-4">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <History className="w-4 h-4 text-primary" />
                Draw History
              </CardTitle>
            </CardHeader>
            <CardContent>
              {drawsError && <ErrorBanner message={drawsError} />}
              <ScrollArea className="h-[400px]">
                {drawsLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
                  </div>
                ) : drawRows.length > 0 ? (
                  <div className="space-y-1">
                    {drawRows.map((d, i) => (
                      <div key={`${d.draw_date}-${i}`} className="flex items-center justify-between p-2 rounded-md bg-muted/30 gap-2">
                        <span className="text-[10px] text-muted-foreground shrink-0">{d.draw_date}</span>
                        <div className="flex gap-1 flex-wrap justify-end">
                          {(d.numbers || []).map((n) => (
                            <span key={n} className="w-6 h-6 rounded-full bg-primary/20 text-primary text-[10px] font-mono flex items-center justify-center">{n}</span>
                          ))}
                          {d.bonus != null && (
                            <span className="text-[10px] text-warning font-mono">+{d.bonus}</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center text-muted-foreground py-12 text-sm">No draw history</div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>Add Draw / Import</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                <Input type="date" value={drawDate} onChange={(e) => setDrawDate(e.target.value)} className="h-8 text-xs font-mono w-[140px]" />
                <Input value={newDraw} onChange={(e) => setNewDraw(e.target.value)} className="h-8 text-xs font-mono flex-1 min-w-[200px]" placeholder="Main numbers e.g. 7,14,22,31,40,51" />
                {game !== 'daily_lotto' && (
                  <Input value={optionalBonus} onChange={(e) => setOptionalBonus(e.target.value)} className="h-8 text-xs font-mono w-20" placeholder="Bonus" />
                )}
                <Button size="sm" className="h-8 text-xs" onClick={handleAddDraw}>Add</Button>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <Input type="file" accept=".csv" className="h-8 text-xs max-w-[240px]" onChange={(e) => setCsvFile(e.target.files?.[0] || null)} />
                <Button size="sm" className="h-8 text-xs gap-1" onClick={handleImport} disabled={importing || !csvFile}>
                  <Upload className="w-3 h-3" />
                  {importing ? 'Importing...' : 'Import'}
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="generate" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Dices className="w-4 h-4 text-primary" />
                Generate Ticket
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Button size="sm" className="gap-1 text-xs" onClick={handleGenerate} disabled={generating}>
                <Sparkles className="w-3 h-3" />
                {generating ? 'Generating...' : 'Generate'}
              </Button>
              {generatedTicket && (
                <div className="p-4 rounded-md bg-muted/30">
                  <p className="text-[10px] text-muted-foreground uppercase mb-2">Generated Numbers</p>
                  <div className="flex gap-2 flex-wrap">
                    {generatedTicket.numbers.map((n) => (
                      <span key={n} className="w-10 h-10 rounded-full bg-primary/20 text-primary text-sm font-mono font-bold flex items-center justify-center">{n}</span>
                    ))}
                  </div>
                  {generatedTicket.bonus != null && (
                    <div className="mt-3">
                      <p className="text-[10px] text-muted-foreground uppercase">Bonus</p>
                      <span className="w-10 h-10 rounded-full bg-warning/20 text-warning text-sm font-mono font-bold flex items-center justify-center mt-1">{generatedTicket.bonus}</span>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="ai" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Sparkles className="w-4 h-4 text-primary" />
                AI Pattern Analysis
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Button size="sm" className="gap-1 text-xs" onClick={handleAi} disabled={aiLoading}>
                <Sparkles className="w-3 h-3" />
                {aiLoading ? 'Analyzing...' : 'Run AI Analysis'}
              </Button>
              {aiAnalysis && (
                <div className="p-4 rounded-md bg-muted/30 text-xs leading-relaxed whitespace-pre-wrap font-mono max-h-[480px] overflow-y-auto">
                  {aiAnalysis}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
