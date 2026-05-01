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
import { ErrorBanner } from '@/components/shared';
import { Filter, Search, Activity } from 'lucide-react';
import type { ScreenerResult } from '@/types';

interface ScreenerScanResult {
  results: ScreenerResult[];
}

interface MicroHealth {
  pair: string;
  quality: number;
  last_update: string;
  status: string;
}

export default function ScreenerPanel() {
  const { setActivePanel } = useStore();
  const [assetClass, setAssetClass] = useState('forex');
  const [trendFilter, setTrendFilter] = useState('all');
  const [minStrength, setMinStrength] = useState(0);
  const [results, setResults] = useState<ScreenerResult[]>([]);
  const [scanning, setScanning] = useState(false);

  const { post: postScan } = useApiPost<ScreenerScanResult>();
  const { data: microHealth, loading: microLoading, error: microError } = useApiPoll<MicroHealth[]>('/api/microstructure-health', 0);

  const handleScan = useCallback(async () => {
    setScanning(true);
    const res = await postScan('/api/screener-scan', {
      asset_class: assetClass,
      filters: { trend: trendFilter === 'all' ? undefined : trendFilter, min_strength: minStrength },
    });
    if (res) setResults(res.results);
    setScanning(false);
  }, [assetClass, trendFilter, minStrength, postScan]);

  const trendColor = (trend: string) => {
    switch (trend) {
      case 'bullish': return 'bg-long/20 text-long';
      case 'bearish': return 'bg-short/20 text-short';
      default: return 'bg-muted text-muted-foreground';
    }
  };

  const sentimentColor = (s: string) => {
    switch (s) {
      case 'risk_on': return 'bg-long/20 text-long';
      case 'risk_off': return 'bg-short/20 text-short';
      default: return 'bg-muted text-muted-foreground';
    }
  };

  return (
    <div className="space-y-4">
      <Tabs defaultValue="screener">
        <TabsList>
          <TabsTrigger value="screener" className="text-xs">Screener</TabsTrigger>
          <TabsTrigger value="micro" className="text-xs">Microstructure Health</TabsTrigger>
        </TabsList>

        <TabsContent value="screener" className="mt-2 space-y-4">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Filter className="w-4 h-4 text-primary" />
                Screener Filters
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-3 flex-wrap">
                <select value={assetClass} onChange={e => setAssetClass(e.target.value)} className="h-8 text-xs bg-card border border-border/60 rounded px-2">
                  <option value="forex">Forex</option>
                  <option value="crypto">Crypto</option>
                  <option value="stocks">Stocks</option>
                  <option value="indices">Indices</option>
                  <option value="commodities">Commodities</option>
                </select>
                <select value={trendFilter} onChange={e => setTrendFilter(e.target.value)} className="h-8 text-xs bg-card border border-border/60 rounded px-2">
                  <option value="all">All Trends</option>
                  <option value="bullish">Bullish</option>
                  <option value="bearish">Bearish</option>
                  <option value="neutral">Neutral</option>
                </select>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground">Min Strength</span>
                  <Input type="number" value={minStrength} onChange={e => setMinStrength(parseInt(e.target.value))} className="w-20 h-8 text-xs font-mono" />
                </div>
                <Button size="sm" className="h-8 text-xs gap-1" onClick={handleScan} disabled={scanning}>
                  <Search className="w-3 h-3" />
                  {scanning ? 'Scanning...' : 'Scan'}
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Results</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[500px]">
                {scanning ? (
                  <div className="space-y-2">
                    {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                  </div>
                ) : results.length === 0 ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">Run a scan to see results</div>
                ) : (
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-border/40">
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Pair</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Trend</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Strength</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">RSI</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">MACD</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Vol</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">ATR</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Sentiment</th>
                      </tr>
                    </thead>
                    <tbody>
                      {results.map(r => (
                        <tr
                          key={r.pair}
                          className="border-b border-border/20 hover:bg-muted/30 transition-colors cursor-pointer"
                          onClick={() => setActivePanel('pairBrowser')}
                        >
                          <td className="py-2 text-xs font-mono font-bold">{r.pair}</td>
                          <td className="py-2">
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${trendColor(r.trend)}`}>
                              {r.trend}
                            </span>
                          </td>
                          <td className="py-2">
                            <div className="flex items-center gap-2 w-24">
                              <Progress value={r.strength} className="h-1.5 flex-1" />
                              <span className="text-[10px] font-mono">{r.strength}</span>
                            </div>
                          </td>
                          <td className="py-2 text-xs font-mono text-right">{r.rsi.toFixed(1)}</td>
                          <td className="py-2 text-xs font-mono text-right">{r.macd.toFixed(4)}</td>
                          <td className="py-2 text-xs font-mono text-right">{r.volume.toFixed(0)}</td>
                          <td className="py-2 text-xs font-mono text-right">{r.atr.toFixed(5)}</td>
                          <td className="py-2">
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${sentimentColor(r.sentiment)}`}>
                              {r.sentiment}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="micro" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Activity className="w-4 h-4 text-primary" />
                Microstructure Health
              </CardTitle>
            </CardHeader>
            <CardContent>
              {microError && <ErrorBanner message={microError} />}
              {microLoading ? (
                <Skeleton className="h-20 w-full" />
              ) : microHealth && microHealth.length > 0 ? (
                <div className="space-y-2">
                  {microHealth.map(m => (
                    <div key={m.pair} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                      <span className="text-xs font-mono font-bold">{m.pair}</span>
                      <div className="flex items-center gap-4 flex-1 mx-4">
                        <Progress value={m.quality} className="h-1.5 flex-1" />
                        <span className="text-xs font-mono">{m.quality.toFixed(0)}%</span>
                      </div>
                      <Badge variant="outline" className="text-[10px]">{m.status}</Badge>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No microstructure data</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
