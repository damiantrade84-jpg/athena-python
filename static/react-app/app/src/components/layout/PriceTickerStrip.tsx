import { useEffect, useMemo, useReducer } from 'react';
import { useLivePrices } from '@/hooks/useLivePrices';
import { fmtNum } from '@/lib/utils';
import { TrendingUp, TrendingDown, Minus, WifiOff } from 'lucide-react';

/**
 * Live price ticker that scrolls horizontally under the global header.
 * Polls /api/prices (via useLivePrices) every 2s and tracks intra-session
 * change vs the first observed price for that pair (so users see a live tick
 * direction, not just an absolute number). Purely a visual aid — never used
 * for any trading decision.
 *
 * Intentionally not mounted from MainLayout (global strip removed for perf).
 * Re-mount from DashboardPanel only if a compact dashboard variant is requested.
 */

type TickerRow = {
  key: string;
  price: number;
  changePct: number | null;
  tickDir: 'up' | 'down' | 'flat';
  source?: string;
};

interface TickerState {
  baseline: Record<string, number>;
  previous: Record<string, number>;
  rows: TickerRow[];
}

type TickerAction = { type: 'tick'; prices: Record<string, { price?: number; source?: unknown } | undefined> };

function tickerReducer(state: TickerState, action: TickerAction): TickerState {
  if (action.type !== 'tick') return state;
  const nextBaseline = { ...state.baseline };
  const nextPrevious: Record<string, number> = {};
  const rows: TickerRow[] = [];
  for (const [rawKey, entry] of Object.entries(action.prices || {})) {
    const priceVal = typeof entry?.price === 'number' ? entry.price : Number(entry?.price);
    if (!Number.isFinite(priceVal) || priceVal <= 0) continue;
    const key = rawKey.trim();
    if (!key) continue;
    if (nextBaseline[key] == null) nextBaseline[key] = priceVal;
    const base = nextBaseline[key];
    const changePct = base > 0 ? ((priceVal - base) / base) * 100 : null;
    const prev = state.previous[key];
    let tickDir: 'up' | 'down' | 'flat' = 'flat';
    if (prev != null) {
      if (priceVal > prev) tickDir = 'up';
      else if (priceVal < prev) tickDir = 'down';
    }
    nextPrevious[key] = priceVal;
    rows.push({
      key,
      price: priceVal,
      changePct,
      tickDir,
      source: typeof entry?.source === 'string' ? (entry.source as string) : undefined,
    });
  }
  rows.sort((a, b) => a.key.localeCompare(b.key));
  return { baseline: nextBaseline, previous: nextPrevious, rows };
}

const INITIAL_STATE: TickerState = { baseline: {}, previous: {}, rows: [] };

function formatPrice(value: number): string {
  if (!Number.isFinite(value)) return '—';
  const abs = Math.abs(value);
  if (abs >= 1000) return fmtNum(value, 2);
  if (abs >= 100) return fmtNum(value, 2);
  if (abs >= 1) return fmtNum(value, 4);
  return fmtNum(value, 5);
}

export default function PriceTickerStrip() {
  const { prices, error } = useLivePrices();
  const [state, dispatch] = useReducer(tickerReducer, INITIAL_STATE);

  useEffect(() => {
    if (!prices) return;
    dispatch({ type: 'tick', prices });
  }, [prices]);

  const entries = state.rows;

  // Adjust marquee duration to entry count so the scroll speed stays comfortable
  // regardless of pair universe size.
  const duration = useMemo(() => {
    const n = entries.length;
    if (n <= 0) return 60;
    // 2.5s per ticker entry, clamped to a sensible window
    return Math.min(240, Math.max(45, n * 2.5));
  }, [entries.length]);

  if (error && entries.length === 0) {
    return (
      <div className="ticker-strip h-7 flex items-center px-4 text-[10px] text-muted-foreground gap-2">
        <WifiOff className="w-3 h-3" />
        Live prices unavailable
      </div>
    );
  }
  if (entries.length === 0) {
    return (
      <div className="ticker-strip h-7 flex items-center px-4 text-[10px] text-muted-foreground">
        Waiting for live prices…
      </div>
    );
  }

  // Duplicate the entries so the CSS marquee can loop seamlessly with
  // a translateX(-50%) keyframe.
  const looped = [...entries, ...entries];

  return (
    <div
      className="ticker-strip h-7"
      style={{ ['--ticker-duration' as string]: `${duration}s` }}
    >
      <div className="ticker-track h-full px-4">
        {looped.map((row, idx) => {
          const change = row.changePct;
          const isUp = change != null && change > 0;
          const isDown = change != null && change < 0;
          const changeColor = isUp
            ? 'hsl(var(--long))'
            : isDown
              ? 'hsl(var(--short))'
              : 'hsl(var(--muted-foreground))';
          const TickIcon = row.tickDir === 'up'
            ? TrendingUp
            : row.tickDir === 'down'
              ? TrendingDown
              : Minus;
          const tickColor = row.tickDir === 'up'
            ? 'hsl(var(--long))'
            : row.tickDir === 'down'
              ? 'hsl(var(--short))'
              : 'hsl(var(--muted-foreground))';
          return (
            <div
              key={`${row.key}-${idx}`}
              className="flex items-center gap-2 text-[11px] font-mono shrink-0"
            >
              {/* Symbol stays neutral ink — the change figure is the only
                  thing on the ticker that should carry colour. */}
              <span className="font-semibold tracking-wide text-muted-foreground">
                {row.key}
              </span>
              <TickIcon className="w-3 h-3" style={{ color: tickColor }} />
              <span className="text-foreground/90">{formatPrice(row.price)}</span>
              {change != null && (
                <span style={{ color: changeColor }}>
                  {isUp ? '+' : ''}{change.toFixed(2)}%
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
