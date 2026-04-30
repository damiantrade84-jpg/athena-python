import { useState, useEffect } from 'react';
import { useApiPoll } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';
import { ErrorBanner } from '@/components/shared';
import { Globe, Clock, TrendingUp, TrendingDown, Activity } from 'lucide-react';

interface BulkPrices {
  prices: Record<string, { bid: number; ask: number; spread: number; change_pct: number }>;
}

interface MarketHours {
  sessions: Record<string, { open: string; close: string; active: boolean; time_to_open?: string; time_to_close?: string }>;
}

interface YieldCurve {
  maturities: Record<string, number>;
}

interface RegimeShift {
  regimes: Record<string, string>;
}

interface NewsSentimentItem {
  pair: string;
  sentiment: string;
  score: number;
}

export default function MarketsPanel() {
  const { data: prices, loading: pricesLoading, error: pricesError, refresh: refreshPrices } = useApiPoll<BulkPrices>('/api/bulk-prices', 5000);
  const { data: hours, loading: hoursLoading, error: hoursError } = useApiPoll<MarketHours>('/api/market-hours', 30000);
  const { data: yieldCurve, loading: yieldLoading, error: yieldError } = useApiPoll<YieldCurve>('/api/yield-curve', 0);
  const { data: regime, loading: regimeLoading, error: regimeError } = useApiPoll<RegimeShift>('/api/regime-shift', 30000);
  const { data: sentiment, loading: sentLoading, error: sentError } = useApiPoll<NewsSentimentItem[]>('/api/news-sentiment', 30000);

  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const priceEntries = prices ? Object.entries(prices.prices) : [];
  const yieldData = yieldCurve ? Object.entries(yieldCurve.maturities).map(([k, v]) => ({ maturity: k, yield: v })) : [];

  const sessionOrder = ['london', 'new_york', 'tokyo', 'sydney'];

  return (
    <div className="space-y-5">
      {(pricesError || hoursError || yieldError || regimeError || sentError) && (
        <ErrorBanner
          message={[pricesError, hoursError, yieldError, regimeError, sentError].filter(Boolean).join(' | ')}
          onRetry={() => refreshPrices()}
        />
      )}

      {/* Session Clock Bar */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3">
          <div className="flex items-center justify-between">
            {sessionOrder.map(session => {
              const data = hours?.sessions?.[session];
              const active = data?.active;
              return (
                <div key={session} className={`flex items-center gap-2 px-3 py-2 rounded-md ${active ? 'bg-primary/10' : 'bg-muted/30'}`}>
                  <Globe className={`w-3.5 h-3.5 ${active ? 'text-primary' : 'text-muted-foreground'}`} />
                  <div>
                    <p className={`text-[10px] font-bold uppercase ${active ? 'text-primary' : 'text-muted-foreground'}`}>{session.replace('_', ' ')}</p>
                    <p className="text-[10px] font-mono text-muted-foreground">
                      {data ? `${data.open}-${data.close}` : '--:--'}
                    </p>
                  </div>
                  {active && <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-3 gap-5">
        {/* Prices Table */}
        <Card className="col-span-2 border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Activity className="w-4 h-4 text-primary" />
              Live Prices
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-[400px] overflow-auto scrollbar-thin">
              {pricesLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
                </div>
              ) : priceEntries.length > 0 ? (
                <table className="w-full text-left">
                  <thead className="sticky top-0 bg-card z-10">
                    <tr className="border-b border-border/40">
                      <th className="text-[10px] uppercase py-2 text-muted-foreground">Pair</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Bid</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Ask</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Spread</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">24h</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground">Regime</th>
                    </tr>
                  </thead>
                  <tbody>
                    {priceEntries.map(([pair, data]) => (
                      <tr key={pair} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                        <td className="py-2 text-xs font-mono font-bold">{pair}</td>
                        <td className="py-2 text-xs font-mono text-right">{data.bid.toFixed(5)}</td>
                        <td className="py-2 text-xs font-mono text-right">{data.ask.toFixed(5)}</td>
                        <td className="py-2 text-xs font-mono text-right">{(data.spread * 10000).toFixed(1)}</td>
                        <td className={`py-2 text-xs font-mono font-bold text-right ${data.change_pct >= 0 ? 'text-long' : 'text-short'}`}>
                          {data.change_pct >= 0 ? '+' : ''}{data.change_pct.toFixed(2)}%
                        </td>
                        <td className="py-2">
                          <Badge variant="outline" className="text-[9px]">
                            {regime?.regimes?.[pair] || 'N/A'}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No price data</div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Side Column */}
        <div className="space-y-4">
          {/* Yield Curve */}
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Yield Curve</CardTitle>
            </CardHeader>
            <CardContent>
              {yieldLoading ? <Skeleton className="h-[180px] w-full" /> : yieldData.length > 0 ? (
                <div className="h-[180px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={yieldData}>
                      <XAxis dataKey="maturity" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} width={30} />
                      <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '6px', fontSize: '11px' }} />
                      <Line type="monotone" dataKey="yield" stroke="hsl(var(--primary))" strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-8 text-xs">No yield data</div>
              )}
            </CardContent>
          </Card>

          {/* Regime Status */}
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Regime Status</CardTitle>
            </CardHeader>
            <CardContent>
              {regimeLoading ? <Skeleton className="h-20 w-full" /> : regime?.regimes ? (
                <div className="space-y-1">
                  {Object.entries(regime.regimes).map(([asset, r]) => (
                    <div key={asset} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                      <span className="text-xs capitalize">{asset}</span>
                      <Badge variant="outline" className="text-[10px]">{r}</Badge>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-8 text-xs">No regime data</div>
              )}
            </CardContent>
          </Card>

          {/* News Sentiment */}
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">News Sentiment</CardTitle>
            </CardHeader>
            <CardContent>
              {sentLoading ? <Skeleton className="h-20 w-full" /> : sentiment && sentiment.length > 0 ? (
                <div className="space-y-1">
                  {sentiment.map(s => (
                    <div key={s.pair} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                      <span className="text-xs font-mono">{s.pair}</span>
                      <Badge variant="outline" className={`text-[10px] ${s.sentiment === 'risk_on' ? 'text-long border-long/40' : s.sentiment === 'risk_off' ? 'text-short border-short/40' : ''}`}>
                        {s.sentiment}
                      </Badge>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-8 text-xs">No sentiment data</div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
