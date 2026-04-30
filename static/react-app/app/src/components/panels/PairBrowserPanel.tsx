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
import { Progress } from '@/components/ui/progress';
import { ErrorBanner, EngineTag } from '@/components/shared';
import { Search, Activity, BarChart3, Eye, Newspaper, GitCompare } from 'lucide-react';

interface PairsData {
  forex: string[];
  crypto: string[];
  stocks: string[];
  indices: string[];
  commodities: string[];
}

interface PairAnalysis {
  symbol: string;
  scores?: Record<string, number>;
  trend?: string;
  momentum?: string;
  volatility?: string;
  structure?: string;
  factors?: string[];
}

interface NakedAnalysis {
  zones?: { type: string; price: number }[];
  bos?: boolean;
  choch?: boolean;
  structure?: string;
}

interface VisionResult {
  analysis: string;
  confidence: number;
}

interface NewsSentiment {
  sentiment: string;
  score: number;
  headlines: { title: string; source: string; sentiment: string }[];
}

export default function PairBrowserPanel() {
  const { setActivePanel } = useStore();
  const [search, setSearch] = useState('');
  const [selectedPair, setSelectedPair] = useState<string>('EURUSD');
  const [timeframe, setTimeframe] = useState<string>('H4');
  const [activeTab, setActiveTab] = useState('engineA');

  const { data: pairs, loading: pairsLoading, error: pairsError } = useApiPoll<PairsData>('/api/pairs', 0);
  const { post: postPairScan, loading: scanningA } = useApiPost<PairAnalysis>();
  const { post: postNaked, loading: scanningB } = useApiPost<NakedAnalysis>();
  const { post: postCompare, loading: comparing } = useApiPost<Record<string, unknown>>();
  const { post: postVision, loading: visionLoading } = useApiPost<VisionResult>();
  const { data: newsSentiment, loading: newsLoading } = useApiPoll<NewsSentiment>(`/api/news-sentiment?symbol=${selectedPair}`, 0);
  const { data: candles } = useApiPoll<Record<string, unknown>[]>(`/api/candles?symbol=${selectedPair}&timeframe=${timeframe}&count=100`, 0);
  const { data: intermarket } = useApiPoll<Record<string, unknown>>('/api/intermarket-matrix', 0);

  const [engineA, setEngineA] = useState<PairAnalysis | null>(null);
  const [engineB, setEngineB] = useState<NakedAnalysis | null>(null);
  const [engineC, setEngineC] = useState<Record<string, unknown> | null>(null);
  const [vision, setVision] = useState<VisionResult | null>(null);

  const allPairs = pairs ? [...pairs.forex, ...pairs.crypto, ...pairs.stocks, ...pairs.indices, ...pairs.commodities] : [];
  const filteredPairs = allPairs.filter(p => p.toLowerCase().includes(search.toLowerCase()));

  const runAnalysis = useCallback(async () => {
    if (activeTab === 'engineA') {
      const res = await postPairScan('/api/pair-scan', { symbol: selectedPair, timeframe, engine: 'A' });
      if (res) setEngineA(res);
    } else if (activeTab === 'engineB') {
      const res = await postNaked('/api/naked-analysis', { symbol: selectedPair, timeframe });
      if (res) setEngineB(res);
    } else if (activeTab === 'engineC') {
      const res = await postCompare('/api/compare-engines', { symbol: selectedPair });
      if (res) setEngineC(res);
    } else if (activeTab === 'vision') {
      const res = await postVision('/api/chart-analysis', { symbol: selectedPair, timeframe });
      if (res) setVision(res);
    }
  }, [activeTab, selectedPair, timeframe, postPairScan, postNaked, postCompare, postVision]);

  return (
    <div className="flex gap-4 h-[calc(100vh-120px)]">
      {/* Left Sidebar */}
      <Card className="w-[240px] shrink-0 border-border/60 bg-card/50 flex flex-col">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold">Pairs</CardTitle>
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input
              placeholder="Search..."
              className="pl-7 h-8 text-xs"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
        </CardHeader>
        <CardContent className="flex-1 overflow-hidden p-0">
          <ScrollArea className="h-full px-4 pb-4">
            {pairsLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-6 w-full" />)}
              </div>
            ) : pairsError ? (
              <ErrorBanner message={pairsError} />
            ) : (
              <div className="space-y-1">
                {['forex', 'crypto', 'stocks', 'indices', 'commodities'].map(cls => {
                  const list = pairs?.[cls as keyof PairsData] || [];
                  const shown = search ? list.filter(p => p.toLowerCase().includes(search.toLowerCase())) : list;
                  if (shown.length === 0) return null;
                  return (
                    <div key={cls}>
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground mt-2 mb-1">{cls}</p>
                      {shown.map(pair => (
                        <button
                          key={pair}
                          onClick={() => setSelectedPair(pair)}
                          className={`w-full text-left px-2 py-1 rounded text-xs font-mono transition-colors ${selectedPair === pair ? 'bg-primary/20 text-primary' : 'hover:bg-muted/30 text-foreground'}`}
                        >
                          {pair}
                        </button>
                      ))}
                    </div>
                  );
                })}
              </div>
            )}
          </ScrollArea>
        </CardContent>
      </Card>

      {/* Main Content */}
      <div className="flex-1 flex flex-col gap-4 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-mono font-bold">{selectedPair}</h2>
            <Badge variant="outline" className="text-[10px]">Live</Badge>
          </div>
          <div className="flex items-center gap-2">
            <select
              className="h-8 text-xs bg-card border border-border/60 rounded px-2"
              value={timeframe}
              onChange={e => setTimeframe(e.target.value)}
            >
              <option value="M15">M15</option>
              <option value="H1">H1</option>
              <option value="H4">H4</option>
              <option value="D1">D1</option>
            </select>
            <Button size="sm" className="h-8 text-xs" onClick={runAnalysis} disabled={scanningA || scanningB || comparing || visionLoading}>
              <Activity className="w-3 h-3 mr-1" />
              {scanningA || scanningB || comparing || visionLoading ? 'Analyzing...' : 'Analyze'}
            </Button>
          </div>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col overflow-hidden">
          <TabsList className="w-fit">
            <TabsTrigger value="engineA" className="text-xs">Engine A</TabsTrigger>
            <TabsTrigger value="engineB" className="text-xs">Engine B</TabsTrigger>
            <TabsTrigger value="engineC" className="text-xs">Engine C</TabsTrigger>
            <TabsTrigger value="vision" className="text-xs">Vision</TabsTrigger>
            <TabsTrigger value="news" className="text-xs">News</TabsTrigger>
            <TabsTrigger value="intermarket" className="text-xs">Intermarket</TabsTrigger>
          </TabsList>

          <TabsContent value="engineA" className="flex-1 overflow-auto mt-2">
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-4 space-y-4">
                {!engineA ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">Click Analyze to run Engine A scan</div>
                ) : (
                  <>
                    <div className="grid grid-cols-2 gap-3">
                      {engineA.scores && Object.entries(engineA.scores).map(([key, val]) => (
                        <div key={key} className="p-3 rounded-md bg-muted/30">
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-[10px] uppercase text-muted-foreground">{key}</span>
                            <span className="text-xs font-mono font-bold">{(val as number).toFixed(2)}</span>
                          </div>
                          <Progress value={Math.min(100, (val as number) / 3 * 100)} className="h-1.5" />
                        </div>
                      ))}
                    </div>
                    {engineA.factors && (
                      <div className="flex flex-wrap gap-1">
                        {engineA.factors.map(f => <Badge key={f} variant="secondary" className="text-[10px]">{f}</Badge>)}
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="engineB" className="flex-1 overflow-auto mt-2">
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-4 space-y-4">
                {!engineB ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">Click Analyze to run Engine B scan</div>
                ) : (
                  <>
                    <div className="grid grid-cols-3 gap-3">
                      <div className="p-3 rounded-md bg-muted/30 text-center">
                        <p className="text-[10px] text-muted-foreground uppercase">BOS</p>
                        <p className={`text-sm font-mono font-bold mt-1 ${engineB.bos ? 'text-long' : 'text-muted-foreground'}`}>{engineB.bos ? 'YES' : 'NO'}</p>
                      </div>
                      <div className="p-3 rounded-md bg-muted/30 text-center">
                        <p className="text-[10px] text-muted-foreground uppercase">CHoCH</p>
                        <p className={`text-sm font-mono font-bold mt-1 ${engineB.choch ? 'text-long' : 'text-muted-foreground'}`}>{engineB.choch ? 'YES' : 'NO'}</p>
                      </div>
                      <div className="p-3 rounded-md bg-muted/30 text-center">
                        <p className="text-[10px] text-muted-foreground uppercase">Structure</p>
                        <p className="text-sm font-mono font-bold mt-1">{engineB.structure || 'N/A'}</p>
                      </div>
                    </div>
                    {engineB.zones && (
                      <div>
                        <p className="text-[10px] text-muted-foreground uppercase mb-2">Key Zones</p>
                        <div className="space-y-1">
                          {engineB.zones.map((z, i) => (
                            <div key={i} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                              <Badge variant="outline" className="text-[10px]">{z.type}</Badge>
                              <span className="text-xs font-mono">{z.price.toFixed(5)}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="engineC" className="flex-1 overflow-auto mt-2">
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-4">
                {!engineC ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">Click Analyze to compare engines</div>
                ) : (
                  <pre className="text-xs font-mono bg-muted/30 p-3 rounded-md overflow-auto">
                    {JSON.stringify(engineC, null, 2)}
                  </pre>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="vision" className="flex-1 overflow-auto mt-2">
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-4 space-y-4">
                {!vision ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">Click Analyze for AI Vision</div>
                ) : (
                  <>
                    <div className="flex items-center gap-2">
                      <Eye className="w-4 h-4 text-primary" />
                      <span className="text-sm font-semibold">AI Chart Analysis</span>
                      <Badge variant="outline" className="text-[10px]">{(vision.confidence * 100).toFixed(0)}% confidence</Badge>
                    </div>
                    <p className="text-xs leading-relaxed whitespace-pre-wrap">{vision.analysis}</p>
                  </>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="news" className="flex-1 overflow-auto mt-2">
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-4 space-y-3">
                {newsLoading ? (
                  <Skeleton className="h-20 w-full" />
                ) : newsSentiment ? (
                  <>
                    <div className="flex items-center gap-2">
                      <Newspaper className="w-4 h-4 text-primary" />
                      <span className="text-sm font-semibold">Sentiment: {newsSentiment.sentiment}</span>
                      <Badge variant="outline" className="text-[10px]">Score: {newsSentiment.score.toFixed(2)}</Badge>
                    </div>
                    {newsSentiment.headlines?.map((h, i) => (
                      <div key={i} className="p-2 rounded-md bg-muted/30">
                        <p className="text-xs">{h.title}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <span className="text-[10px] text-muted-foreground">{h.source}</span>
                          <Badge variant="outline" className="text-[9px]">{h.sentiment}</Badge>
                        </div>
                      </div>
                    ))}
                  </>
                ) : (
                  <div className="text-center text-muted-foreground py-12 text-sm">No news data available</div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="intermarket" className="flex-1 overflow-auto mt-2">
            <Card className="border-border/60 bg-card/50">
              <CardContent className="p-4">
                {intermarket ? (
                  <pre className="text-xs font-mono bg-muted/30 p-3 rounded-md overflow-auto">
                    {JSON.stringify(intermarket, null, 2)}
                  </pre>
                ) : (
                  <div className="text-center text-muted-foreground py-12 text-sm">No intermarket data</div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
