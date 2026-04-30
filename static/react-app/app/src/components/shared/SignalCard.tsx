import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';
import { Play, Clock, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import type { Signal } from '@/types';

interface SignalCardProps {
  signal: Signal;
  onExecute?: (signal: Signal) => void;
  disabled?: boolean;
  disabledLabel?: string;
  compact?: boolean;
}

export default function SignalCard({ signal, onExecute, disabled, disabledLabel, compact }: SignalCardProps) {
  const dirColor = signal.direction === 'LONG' ? 'text-long' : 'text-short';
  const dirBg = signal.direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short';

  if (compact) {
    return (
      <div className="flex items-center justify-between p-2 rounded-md bg-muted/30 hover:bg-muted/50 transition-colors">
        <div className="flex items-center gap-2">
          <span className={cn('text-[10px] font-bold px-1.5 py-0.5 rounded', dirBg)}>
            {signal.direction}
          </span>
          <span className="text-xs font-mono font-medium">{signal.pair}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground">{signal.confidence}%</span>
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
            <span className="text-sm font-mono font-bold">{signal.pair}</span>
            <Badge className={cn('text-[10px]', dirBg)}>{signal.direction}</Badge>
            <Badge variant="outline" className="text-[10px]">{signal.engine}</Badge>
          </div>
          <span className="text-[10px] font-mono text-muted-foreground">
            <Clock className="w-3 h-3 inline mr-1" />
            {new Date(signal.timestamp).toLocaleTimeString()}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-3 gap-2">
          <div className="p-2 rounded-md bg-muted/30">
            <p className="text-[10px] text-muted-foreground uppercase">Entry</p>
            <p className="text-xs font-mono font-bold">{signal.entry.toFixed(5)}</p>
          </div>
          <div className="p-2 rounded-md bg-short/10">
            <p className="text-[10px] text-short uppercase">SL</p>
            <p className="text-xs font-mono font-bold text-short">{signal.sl.toFixed(5)}</p>
          </div>
          <div className="p-2 rounded-md bg-long/10">
            <p className="text-[10px] text-long uppercase">TP</p>
            <p className="text-xs font-mono font-bold text-long">{signal.tp.toFixed(5)}</p>
          </div>
        </div>

        {signal.tp2 && (
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <ArrowUpRight className="w-3 h-3 text-long" />
            TP2: <span className="font-mono text-foreground">{signal.tp2.toFixed(5)}</span>
          </div>
        )}

        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] text-muted-foreground">Confidence</span>
              <span className={cn('text-[10px] font-mono font-bold', signal.confidence >= 85 ? 'text-long' : signal.confidence >= 70 ? 'text-warning' : 'text-muted-foreground')}>
                {signal.confidence}%
              </span>
            </div>
            <Progress value={signal.confidence} className="h-1.5" />
          </div>
          <Badge variant="outline" className="text-[10px] font-mono">
            R:R {signal.rRatio.toFixed(2)}
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
