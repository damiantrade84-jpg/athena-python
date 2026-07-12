import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { GhostPerformance } from './types';

export function GhostPerformancePanel({ performance }: { performance: GhostPerformance }) {
  const stats = [
    ['Closed', performance.totalClosedTrades], ['Mean R', performance.meanR ?? '—'],
    ['Median R', performance.medianR ?? '—'], ['Win rate', performance.winRate == null ? '—' : `${(performance.winRate * 100).toFixed(1)}%`],
    ['Profit factor', performance.profitFactor ?? '—'], ['Total R', performance.totalR],
  ];
  return <Card><CardHeader><CardTitle className="text-sm">Ghost-only performance</CardTitle></CardHeader><CardContent className="grid grid-cols-2 gap-3 md:grid-cols-6">{stats.map(([label, value]) => <div key={String(label)}><div className="text-[10px] uppercase text-muted-foreground">{label}</div><div className="font-mono text-sm">{String(value)}</div></div>)}</CardContent></Card>;
}
