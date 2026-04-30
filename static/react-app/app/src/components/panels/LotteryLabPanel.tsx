import { useState, useCallback } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';
import { ErrorBanner } from '@/components/shared';
import { Dices, Upload, Sparkles, History, BarChart3, TrendingUp } from 'lucide-react';

interface LotteryDashboard {
  total_draws: number;
  most_common: number[];
  hot_numbers: { number: number; frequency: number }[];
  cold_numbers: { number: number; frequency: number }[];
}

interface LotteryTicket {
  numbers: number[];
  powerball?: number;
}

interface LotteryDraw {
  date: string;
  numbers: number[];
  powerball?: number;
}

export default function LotteryLabPanel() {
  const { showToast } = useStore();
  const [activeTab, setActiveTab] = useState('overview');
  const [generatedTicket, setGeneratedTicket] = useState<LotteryTicket | null>(null);
  const [aiAnalysis, setAiAnalysis] = useState<string>('');
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [newDraw, setNewDraw] = useState('');

  const { data: dashboard, loading: dashLoading, error: dashError } = useApiPoll<LotteryDashboard>('/api/lottery/dashboard', 0);
  const { data: frequency, loading: freqLoading } = useApiPoll<{ number: number; frequency: number }[]>('/api/lottery/frequency', 0);
  const { data: draws, loading: drawsLoading, error: drawsError, refresh: refreshDraws } = useApiPoll<LotteryDraw[]>('/api/lottery/draws', 0);
  const { data: stats } = useApiPoll<Record<string, unknown>>('/api/lottery/stats', 0);

  const { post: postGenerate, loading: generating } = useApiPost<LotteryTicket>();
  const { post: postAi, loading: aiLoading } = useApiPost<{ analysis: string }>();
  const { post: postImport, loading: importing } = useApiPost<{ imported: number }>();
  const { post: postAddDraw } = useApiPost<{ success: boolean }>();

  const handleGenerate = useCallback(async () => {
    const res = await postGenerate('/api/lottery/generate');
    if (res) setGeneratedTicket(res);
  }, [postGenerate]);

  const handleAi = useCallback(async () => {
    const res = await postAi('/api/lottery/ai-analysis');
    if (res) setAiAnalysis(res.analysis);
  }, [postAi]);

  const handleImport = useCallback(async () => {
    if (!csvFile) return;
    const formData = new FormData();
    formData.append('file', csvFile);
    const res = await fetch('/api/lottery/import', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.imported) {
      showToast(`Imported ${data.imported} draws`, 'success');
      refreshDraws();
    }
  }, [csvFile, showToast, refreshDraws]);

  const handleAddDraw = useCallback(async () => {
    const nums = newDraw.split(',').map(n => parseInt(n.trim())).filter(n => !isNaN(n));
    const res = await postAddDraw('/api/lottery/add-draw', { numbers: nums });
    if (res?.success) {
      showToast('Draw added', 'success');
      refreshDraws();
      setNewDraw('');
    }
  }, [newDraw, postAddDraw, showToast, refreshDraws]);

  const freqData = frequency?.map(f => ({ name: f.number, freq: f.frequency })) || [];

  return (
    <div className="space-y-4">
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
                  <p className="text-xl font-mono font-bold">{dashboard?.total_draws || 0}</p>
                )}
              </CardContent>
            </Card>
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-3">
                <p className="text-[10px] uppercase text-muted-foreground">Most Common</p>
                {dashLoading ? <Skeleton className="h-6 w-16 mt-1" /> : (
                  <p className="text-xl font-mono font-bold">{dashboard?.most_common?.join(', ') || 'N/A'}</p>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <TrendingUp className="w-4 h-4 text-long" />
                  Hot Numbers
                </CardTitle>
              </CardHeader>
              <CardContent>
                {dashLoading ? <Skeleton className="h-20 w-full" /> : (
                  <div className="flex flex-wrap gap-1">
                    {dashboard?.hot_numbers?.map(n => (
                      <Badge key={n.number} variant="outline" className="text-[10px] font-mono bg-long/10 text-long border-long/40">
                        {n.number} ({n.frequency}x)
                      </Badge>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <TrendingUp className="w-4 h-4 text-short" />
                  Cold Numbers
                </CardTitle>
              </CardHeader>
              <CardContent>
                {dashLoading ? <Skeleton className="h-20 w-full" /> : (
                  <div className="flex flex-wrap gap-1">
                    {dashboard?.cold_numbers?.map(n => (
                      <Badge key={n.number} variant="outline" className="text-[10px] font-mono bg-short/10 text-short border-short/40">
                        {n.number} ({n.frequency}x)
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
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
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
                <div className="text-center text-muted-foreground py-12 text-sm">No frequency data</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="history" className="mt-2 space-y-4">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
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
                ) : draws && draws.length > 0 ? (
                  <div className="space-y-1">
                    {draws.map((d, i) => (
                      <div key={i} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                        <span className="text-[10px] text-muted-foreground">{d.date}</span>
                        <div className="flex gap-1">
                          {d.numbers.map(n => (
                            <span key={n} className="w-6 h-6 rounded-full bg-primary/20 text-primary text-[10px] font-mono flex items-center justify-center">{n}</span>
                          ))}
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
              <CardTitle className="text-sm font-semibold">Add Draw / Import</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-2">
                <Input value={newDraw} onChange={e => setNewDraw(e.target.value)} className="h-8 text-xs font-mono" placeholder="1,2,3,4,5,6" />
                <Button size="sm" className="h-8 text-xs" onClick={handleAddDraw}>Add</Button>
              </div>
              <div className="flex items-center gap-2">
                <Input type="file" accept=".csv" className="h-8 text-xs" onChange={e => setCsvFile(e.target.files?.[0] || null)} />
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
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
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
                  <div className="flex gap-2">
                    {generatedTicket.numbers.map(n => (
                      <span key={n} className="w-10 h-10 rounded-full bg-primary/20 text-primary text-sm font-mono font-bold flex items-center justify-center">{n}</span>
                    ))}
                  </div>
                  {generatedTicket.powerball && (
                    <div className="mt-3">
                      <p className="text-[10px] text-muted-foreground uppercase">Powerball</p>
                      <span className="w-10 h-10 rounded-full bg-warning/20 text-warning text-sm font-mono font-bold flex items-center justify-center mt-1">{generatedTicket.powerball}</span>
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
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
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
                <div className="p-4 rounded-md bg-muted/30 text-xs leading-relaxed whitespace-pre-wrap">
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
