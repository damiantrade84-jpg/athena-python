import { useState, useEffect } from 'react';
import { useApiPoll } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';
import { ErrorBanner } from '@/components/shared';
import { fmtNum, toNum } from '@/lib/utils';
import { Globe, Clock, TrendingUp, TrendingDown, Activity } from 'lucide-react';

interface BulkPriceRow {
  bid?: number | null;
  ask?: number | null;
  spread?: number | null;
  change_pct?: number | null;
  price?: number | null;
  changePct?: number | null;
}

interface BulkPrices {
  prices: Record<string, BulkPriceRow>;
}

interface MarketHours {
  sessions: Record<string, { open?: string | boolean; close?: string; active?: boolean; status?: string; note?: string; hours?: string; time_to_open?: string; time_to_close?: string }>;
}

interface YieldCurve {
  maturities: Record<string, number>;
}

interface RegimeShift {
  regimes?: Record<string, string>;
  [pair: string]: unknown;
}

export default function MarketsPanel() {
  const { data: prices, loading: pricesLoading, error: pricesError, refresh: refreshPrices } = useApiPoll<BulkPrices>('/api/bulk-prices', 10000);
  const { data: hours, loading: hoursLoading, error: hoursError } = useApiPoll<MarketHours>('/api/market-hours', 30000);
  const { data: yieldCurve, loading: yieldLoading, error: yieldError } = useApiPoll<YieldCurve>('/api/yield-curve', 0);
  const { data: regime, loading: regimeLoading, error: regimeError } = useApiPoll<RegimeShift>('/api/regime-shift', 30000);

  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const priceEntries = prices?.prices ? Object.entries(prices.prices) : [];
  const yieldData = yieldCurve?.maturities ? Object.entries(yieldCurve.maturities).map(([k, v]) => ({ maturity: k, yield: v })) : [];
  const regimeMap = (regime?.regimes ?? regime ?? {}) as Record<string, unknown>;

  const sessionOrder = ['london', 'new_york', 'tokyo', 'sydney'];
  const sessionActive = (data?: MarketHours['sessions'][string]) => Boolean((data?.active ?? data?.open === true) || data?.status === 'Active');
  const sessionWindow = (data?: MarketHours['sessions'][string]) => {
    if (!data) return '--:--';
    if (typeof data.open === 'string' && data.close) return `${data.open}-${data.close}`;
    return data.hours || data.status || data.note || '--:--';
  };

  return (
    <div className="space-y-5">
      {(pricesError || hoursError || yieldError || regimeError) && (
        <ErrorBanner
          message={[pricesError, hoursError, yieldError, regimeError].filter(Boolean).join(' | ')}
          onRetry={() => refreshPrices()}
        />
      )}

      {/* Session Clock Bar */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3">
          <div className="flex items-center justify-between">
            {sessionOrder.map(session => {
              const data = hours?.sessions?.[session];
              const active = sessionActive(data);
              return (
                <div key={session} className={`flex items-center gap-2 px-3 py-2 rounded-md ${active ? 'bg-primary/10' : 'bg-muted/30'}`}>
                  <Globe className={`w-3.5 h-3.5 ${active ? 'text-primary' : 'text-muted-foreground'}`} />
                  <div>
                    <p className={`text-[10px] font-bold uppercase ${active ? 'text-primary' : 'text-muted-foreground'}`}>{session.replace('_', ' ')}</p>
                    <p className="text-[10px] font-mono text-muted-foreground">
                      {sessionWindow(data)}
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
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
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
                    {priceEntries.map(([pair, data]) => {
                      const p = String(pair).toUpperCase();
                      const isJpy = p.includes('JPY');
                      const isCrypto = p.includes('BTC') || p.includes('ETH') || p.includes('SOL') || p.includes('BNB') || p.includes('DOGE') || p.includes('SHIB');
                      const mult = isCrypto ? 1 : isJpy ? 100 : 10000;
                      const bid = toNum(data.bid ?? data.price, NaN);
                      const ask = toNum(data.ask ?? data.price, NaN);
                      const spread = data.spread != null ? toNum(data.spread, NaN) : Math.abs(ask - bid);
                      const changePct = toNum(data.change_pct ?? data.changePct, NaN);
                      const changeText = Number.isFinite(changePct) ? `${changePct >= 0 ? '+' : ''}${fmtNum(changePct, 2)}%` : 'N/A';
                      return (
                        <tr key={pair} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                          <td className="py-2 text-xs font-mono font-bold">{pair}</td>
                          <td className="py-2 text-xs font-mono text-right">{fmtNum(bid, 5, 'N/A')}</td>
                          <td className="py-2 text-xs font-mono text-right">{fmtNum(ask, 5, 'N/A')}</td>
                          <td className="py-2 text-xs font-mono text-right">{Number.isFinite(spread) ? fmtNum(spread * mult, 1, 'N/A') : 'N/A'}</td>
                          <td className={`py-2 text-xs font-mono font-bold text-right ${changePct >= 0 ? 'text-long' : 'text-short'}`}>
                            {changeText}
                          </td>
                          <td className="py-2">
                            <Badge variant="outline" className="text-[9px]">
                              {String(regimeMap[pair] || 'N/A')}
                            </Badge>
                          </td>
                        </tr>
                      );
                    })}
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
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Yield Curve</CardTitle>
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
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Regime Status</CardTitle>
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

          {/* News Sentiment — /api/news-sentiment requires a per-pair symbol (see Pair Browser). */}
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>News Sentiment</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground text-xs leading-relaxed py-2">
                EODHD + AI sentiment runs per symbol. Open <span className="text-foreground font-medium">Pair Browser</span>, choose a pair, and run news sentiment there — bulk markets view has no single symbol to query.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
