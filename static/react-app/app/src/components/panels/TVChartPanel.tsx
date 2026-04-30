import { useState, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

const TIMEFRAMES = ['1', '5', '15', '30', '60', '240', 'D', 'W'];

function formatSymbol(symbol: string): string {
  const s = symbol.toUpperCase();
  // Crypto
  if (s.includes('BTC')) return 'BINANCE:BTCUSDT';
  if (s.includes('ETH')) return 'BINANCE:ETHUSDT';
  if (s.includes('SOL')) return 'BINANCE:SOLUSDT';
  if (s.includes('XRP')) return 'BINANCE:XRPUSDT';
  // Commodities
  if (s === 'XAUUSD') return 'TVC:GOLD';
  if (s === 'XAGUSD') return 'TVC:SILVER';
  if (s === 'USOIL' || s === 'WTI') return 'TVC:USOIL';
  if (s === 'BRENT') return 'TVC:UKOIL';
  // Indices
  if (s === 'US30' || s === 'DJ30') return 'DJ:DJI';
  if (s === 'US500' || s === 'SPX') return 'SP:SPX';
  if (s === 'NAS100' || s === 'NDX') return 'NASDAQ:NDX';
  if (s === 'DE40' || s === 'DAX') return 'XETR:DAX';
  if (s === 'UK100' || s === 'FTSE') return 'TVC:UKX';
  // Forex default
  if (s.length === 6 && !s.includes('USDJPY')) return `FX:${s}`;
  return `FX:${s}`;
}

export default function TVChartPanel() {
  const [pair, setPair] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState('60');
  const [ema20, setEma20] = useState(true);
  const [ema50, setEma50] = useState(true);
  const [ema200, setEma200] = useState(false);
  const [loading, setLoading] = useState(true);

  const tvSymbol = useMemo(() => formatSymbol(pair), [pair]);

  const studies = useMemo(() => {
    const list: string[] = [];
    if (ema20) list.push('EMA@tv-basicstudies(20)');
    if (ema50) list.push('EMA@tv-basicstudies(50)');
    if (ema200) list.push('EMA@tv-basicstudies(200)');
    return list.join(',');
  }, [ema20, ema50, ema200]);

  const widgetUrl = useMemo(() => {
    const params = new URLSearchParams({
      symbol: tvSymbol,
      interval: timeframe,
      theme: 'dark',
      style: '1',
      locale: 'en',
      toolbar_bg: '#f1f3f6',
      enable_publishing: 'false',
      hide_top_toolbar: 'false',
      hide_legend: 'false',
      studies: studies,
      autosize: 'true',
    });
    return `https://www.tradingview.com/widgetembed/?${params.toString()}`;
  }, [tvSymbol, timeframe, studies]);

  return (
    <div className="flex flex-col gap-4 h-[calc(100vh-120px)]">
      {/* Controls */}
      <div className="flex items-center gap-3 flex-wrap">
        <Input
          value={pair}
          onChange={e => { setPair(e.target.value.toUpperCase()); setLoading(true); }}
          className="w-32 h-8 text-xs font-mono"
          placeholder="Symbol"
        />
        <div className="flex items-center gap-1 bg-card border border-border/60 rounded-md px-1">
          {TIMEFRAMES.map(tf => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-2 py-1 rounded text-[10px] font-mono transition-colors ${timeframe === tf ? 'bg-primary text-primary-foreground' : 'hover:bg-muted/50 text-muted-foreground'}`}
            >
              {tf}
            </button>
          ))}
        </div>
        <Badge variant="outline" className="text-[10px] font-mono">{tvSymbol}</Badge>
        <div className="flex items-center gap-4 ml-auto">
          <div className="flex items-center gap-1.5">
            <Switch checked={ema20} onCheckedChange={setEma20} />
            <span className="text-[10px] text-muted-foreground">EMA20</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Switch checked={ema50} onCheckedChange={setEma50} />
            <span className="text-[10px] text-muted-foreground">EMA50</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Switch checked={ema200} onCheckedChange={setEma200} />
            <span className="text-[10px] text-muted-foreground">EMA200</span>
          </div>
        </div>
      </div>

      {/* Chart */}
      <Card className="flex-1 border-border/60 bg-card/50 overflow-hidden relative">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-card z-10">
            <Skeleton className="h-full w-full" />
          </div>
        )}
        <iframe
          key={widgetUrl}
          src={widgetUrl}
          className="w-full h-full border-0"
          onLoad={() => setLoading(false)}
          title="TradingView Chart"
          allow="clipboard-write"
        />
      </Card>
    </div>
  );
}
