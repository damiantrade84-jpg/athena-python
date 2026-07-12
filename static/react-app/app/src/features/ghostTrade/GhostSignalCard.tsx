import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { pct, scoreLabel } from './display';
import type { GhostSignal } from './types';

export function GhostSignalCard({ signal, onSelect }: { signal: GhostSignal; onSelect: () => void }) {
  const componentLabels: Array<[string, string]> = [
    ['D1Structure', 'D1 Structure'],
    ['H4Momentum', 'H4 Momentum'],
    ['H4Volatility', 'H4 Volatility'],
    ['H1EntryQuality', 'H1 Entry Quality'],
    ['M15LiveTrigger', 'M15 Live Trigger'],
  ];
  return (
    <button type="button" className="w-full text-left" onClick={onSelect}>
      <Card className="transition-colors hover:border-primary/50">
        <CardContent className="p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="font-semibold">{signal.instrument.canonicalSymbol}</div>
              <div className="text-[10px] text-muted-foreground">{signal.instrument.source} · {signal.instrument.brokerSymbol}</div>
            </div>
            <Badge className={signal.direction === 'LONG' ? 'badge-long' : signal.direction === 'SHORT' ? 'badge-short' : 'badge-neutral'}>{signal.direction}</Badge>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs font-mono">
            <span>Confirmed {pct(signal.confirmedScore)}</span>
            <span>Live {signal.liveAdjustment >= 0 ? '+' : ''}{pct(signal.liveAdjustment)}</span>
            <span>Display {pct(signal.displayScore)}</span>
          </div>
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span>{scoreLabel(signal.displayScore)} · {signal.volatilityRegime}</span>
            <span>{signal.structuralRR == null ? '—' : `${signal.structuralRR.toFixed(2)}R`}</span>
          </div>
          <div className="text-[10px] text-muted-foreground">
            Group rank {signal.groupRank ?? '—'} of {signal.groupCount ?? '—'} · Global {signal.globalRank ?? '—'} of {signal.globalCount ?? '—'}
          </div>
          <div className="space-y-0.5 border-t pt-2 text-[10px] text-muted-foreground">
            {componentLabels.map(([key, label]) => (
              <div key={key} className="flex justify-between gap-2">
                <span>{label}</span>
                <span className="max-w-[55%] truncate font-mono">
                  {signal.components[key] ? JSON.stringify(signal.components[key]) : 'unavailable'}
                </span>
              </div>
            ))}
          </div>
          {signal.reasons.length > 0 && <div className="text-[10px] text-amber-300">{signal.reasons.join(' · ')}</div>}
        </CardContent>
      </Card>
    </button>
  );
}
