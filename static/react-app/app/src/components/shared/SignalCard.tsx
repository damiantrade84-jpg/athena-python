import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { cn, fmtNum, toNum } from '@/lib/utils';
import { Play, Clock, ArrowUpRight } from 'lucide-react';
import type { Signal } from '@/types';

interface SignalCardProps {
  signal: Signal;
  onExecute?: (signal: Signal) => void;
  disabled?: boolean;
  disabledLabel?: string;
  compact?: boolean;
}

export default function SignalCard({ signal, onExecute, disabled, disabledLabel, compact }: SignalCardProps) {
  const dirBg = signal.direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short';
  const conf = toNum(signal.conviction ?? signal.confidence, 0);
  // Configurable confidence color thresholds (can be driven by backend later)
  const CONF_HIGH = 85;
  const CONF_MED = 70;
  const confColor = conf >= CONF_HIGH ? 'text-long' : conf >= CONF_MED ? 'text-warning' : 'text-muted-foreground';
  const tp2Num = Number(signal.tp2);
  const hasTp2 = signal.tp2 != null && Number.isFinite(tp2Num);

  if (compact) {
    return (
      <div className="flex items-center justify-between p-2 rounded-md bg-muted/30 hover:bg-muted/50 transition-colors">
        <div className="flex items-center gap-2">
          <span className={cn('text-[10px] font-bold px-1.5 py-0.5 rounded', dirBg)}>
            {signal.direction || '—'}
          </span>
          <span className="text-xs font-mono font-medium">{signal.pair || '—'}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground">{fmtNum(conf, 0)}%</span>
          <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => onExecute?.(signal)} disabled={disabled}>
            <Play className="w-3 h-3 text-primary" />
          </Button>
        </div>
      </div>
    );
  }

  return (
    <Card className="border-border/60 bg-card/50 hover:border-primary/30 transition-colors">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-sm font-mono font-bold">{signal.pair || '—'}</span>
            <Badge className={cn('text-[10px]', dirBg)}>{signal.direction || '—'}</Badge>
            <Badge variant="outline" className="text-[10px]">{signal.engine || '—'}</Badge>
          </div>
          <span className="text-[10px] font-mono text-muted-foreground">
            <Clock className="w-3 h-3 inline mr-1" />
            {signal.timestamp ? new Date(signal.timestamp).toLocaleTimeString() : '—'}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-3 gap-2">
          <div className="p-2 rounded-md bg-muted/30">
            <p className="text-[10px] text-muted-foreground uppercase">Entry</p>
            <p className="text-xs font-mono font-bold">{fmtNum(signal.entry, 5)}</p>
          </div>
          <div className="p-2 rounded-md bg-short/10">
            <p className="text-[10px] text-short uppercase">SL</p>
            <p className="text-xs font-mono font-bold text-short">{fmtNum(signal.sl, 5)}</p>
          </div>
          <div className="p-2 rounded-md bg-long/10">
            <p className="text-[10px] text-long uppercase">TP</p>
            <p className="text-xs font-mono font-bold text-long">{fmtNum(signal.tp, 5)}</p>
          </div>
        </div>

        {hasTp2 && (
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <ArrowUpRight className="w-3 h-3 text-long" />
            TP2: <span className="font-mono text-foreground">{fmtNum(signal.tp2, 5)}</span>
          </div>
        )}

        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] text-muted-foreground">Confidence</span>
              <span className={cn('text-[10px] font-mono font-bold', confColor)}>
                {fmtNum(conf, 0)}%
              </span>
            </div>
            <Progress value={Number.isFinite(conf) ? conf : 0} className="h-1.5" />
          </div>
          <Badge variant="outline" className="text-[10px] font-mono">
            R:R {fmtNum(signal.rr ?? signal.rRatio, 2)}
          </Badge>
        </div>

        {signal.factors && signal.factors.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {signal.factors.map(f => (
              <Badge key={f} variant="secondary" className="text-[9px]">{f}</Badge>
            ))}
          </div>
        )}

        {onExecute && (
          <Button
            size="sm"
            className="w-full gap-2"
            onClick={() => onExecute(signal)}
            disabled={disabled}
          >
            <Play className="w-3.5 h-3.5" />
            {disabledLabel || 'Execute'}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
