import { cn, fmtNum, toNum } from '@/lib/utils';
import { ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react';

interface LivePriceProps {
  price: number | null | undefined;
  change24h?: number;
  decimals?: number;
  className?: string;
  showChange?: boolean;
}

export default function LivePrice({ price, change24h = 0, decimals = 5, className, showChange = true }: LivePriceProps) {
  const n = toNum(price, NaN);
  const valid = Number.isFinite(n) && n > 0;
  const isUp = change24h > 0;
  const isDown = change24h < 0;
  const colorClass = isUp ? 'text-long' : isDown ? 'text-short' : 'text-muted-foreground';

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <span className="font-mono font-bold">{valid ? fmtNum(n, decimals) : '—'}</span>
      {showChange && (
        <span className={cn('flex items-center gap-0.5 text-[10px] font-mono', colorClass)}>
          {isUp ? <ArrowUpRight className="w-3 h-3" /> : isDown ? <ArrowDownRight className="w-3 h-3" /> : <Minus className="w-3 h-3" />}
          {Math.abs(change24h).toFixed(2)}%
        </span>
      )}
    </div>
  );
}
