import { useEffect, useRef, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll } from '@/hooks/useApiData';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Copy } from 'lucide-react';
import { aseInstrumentLabel } from '@/lib/athenaFormat';
import { fmtNum } from '@/lib/utils';
import type { ASEExecutedTrade, ASEExecutedTradesResponse } from '@/types/athena';

const POLL_MS = 15000;

export function fmtAsePrice(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 2 : abs >= 10 ? 3 : 5;
  return fmtNum(value, digits);
}

export { aseInstrumentLabel };

export function aseTradePlanText(trade: ASEExecutedTrade): string {
  const parts = [
    `${trade.direction || ''} ${aseInstrumentLabel(trade)} @ ${fmtAsePrice(trade.entryReference)}`,
    `SL ${fmtAsePrice(trade.sl)}`,
    `TP1 ${fmtAsePrice(trade.tp1)}`,
  ];
  if (trade.tp2 !== null && trade.tp2 !== undefined) parts.push(`TP2 ${fmtAsePrice(trade.tp2)}`);
  if (trade.rr1 !== null && trade.rr1 !== undefined) parts.push(`RR ${fmtNum(trade.rr1, 2)}`);
  return parts.join(' | ');
}

function tradeKey(trade: ASEExecutedTrade): string {
  return `${trade.instrument || ''}:${trade.horizon || ''}:${trade.decisionTimeMs || 0}`;
}

/**
 * App-wide watcher for new ASE demo fills. Polls the executed-trades journal
 * and pops a dialog with the trade plan so the user can mirror it manually on
 * a funded account. Seeds on the first poll so historical fills never pop.
 */
export default function ASEFillWatcher() {
  const { showToast } = useStore();
  const { data } = useApiPoll<ASEExecutedTradesResponse>(
    '/api/ase-executed-trades?limit=10',
    POLL_MS,
  );
  const lastSeenMsRef = useRef<number | null>(null);
  const [queue, setQueue] = useState<ASEExecutedTrade[]>([]);

  useEffect(() => {
    if (!data?.success || !Array.isArray(data.trades)) return;
    const maxMs = data.trades.reduce(
      (acc, t) => Math.max(acc, Number(t.decisionTimeMs) || 0),
      0,
    );
    if (lastSeenMsRef.current === null) {
      // First successful poll: baseline only, never pop historical fills.
      lastSeenMsRef.current = maxMs;
      return;
    }
    const seen = lastSeenMsRef.current;
    const fresh = data.trades
      .filter((t) => (Number(t.decisionTimeMs) || 0) > seen)
      .sort((a, b) => (Number(a.decisionTimeMs) || 0) - (Number(b.decisionTimeMs) || 0));
    if (!fresh.length) return;
    lastSeenMsRef.current = maxMs;
    setQueue((current) => {
      const known = new Set(current.map(tradeKey));
      return [...current, ...fresh.filter((t) => !known.has(tradeKey(t)))];
    });
    for (const t of fresh) {
      showToast(`ASE demo fill: ${t.direction || ''} ${aseInstrumentLabel(t)}`, 'info');
    }
  }, [data, showToast]);

  const active = queue[0];

  const copyPlan = async (trade: ASEExecutedTrade) => {
    try {
      await navigator.clipboard.writeText(aseTradePlanText(trade));
      showToast('Trade plan copied', 'success');
    } catch {
      showToast('Copy failed — clipboard unavailable', 'error');
    }
  };

  const dismiss = () => setQueue((current) => current.slice(1));

  return (
    <AlertDialog open={!!active} onOpenChange={(open) => { if (!open) dismiss(); }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            ASE demo fill — mirror manually if desired
            {active?.direction && active.direction !== 'NONE' && (
              <Badge className={active.direction === 'LONG' ? 'badge-long' : 'badge-short'}>
                {active.direction}
              </Badge>
            )}
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-xs font-mono">
              <div className="text-sm font-bold text-foreground">
                {aseInstrumentLabel(active)} · {active?.horizon} · {active?.modelFamily}
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                <span>Entry: {fmtAsePrice(active?.entryReference)}</span>
                <span>SL: {fmtAsePrice(active?.sl)}</span>
                <span>TP1: {fmtAsePrice(active?.tp1)}</span>
                <span>TP2: {fmtAsePrice(active?.tp2)}</span>
                <span>R:R1: {fmtNum(active?.rr1, 2)}</span>
                <span>
                  Ticket: {active?.brokerFill?.ticket || active?.orderId || '—'}
                </span>
                {active?.brokerFill?.volume !== undefined && (
                  <span>Demo lots: {fmtNum(active.brokerFill.volume, 2)}</span>
                )}
                <span>
                  Filled:{' '}
                  {active?.decisionTimeMs
                    ? new Date(active.decisionTimeMs).toLocaleTimeString()
                    : '—'}
                </span>
              </div>
              <div className="text-[10px] text-muted-foreground font-sans">
                Executed on the ASE demo account only. Size your funded-account
                position yourself; demo lot size does not transfer.
              </div>
              {queue.length > 1 && (
                <div className="text-[10px] text-muted-foreground font-sans">
                  {queue.length - 1} more fill{queue.length > 2 ? 's' : ''} queued.
                </div>
              )}
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => active && void copyPlan(active)}
          >
            <Copy className="h-3.5 w-3.5 mr-1" /> Copy trade plan
          </Button>
          <AlertDialogAction onClick={dismiss}>Dismiss</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
