import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { canShowExecute, pct } from './display';
import type { GhostHealth, GhostSignal } from './types';

const labels: Record<string, string> = {
  D1Structure: 'D1 Structure', H4Momentum: 'H4 Momentum',
  H4Volatility: 'H4 Volatility', H1EntryQuality: 'H1 Entry Quality',
  H1Exhaustion: 'H1 Exhaustion', M15LiveTrigger: 'M15 Live Trigger',
};

export function GhostSignalDrawer({ health, signal, onClose, onExecute, onDismiss }: { health: GhostHealth; signal: GhostSignal | null; onClose: () => void; onExecute: (id: string) => void; onDismiss: (id: string) => void }) {
  return (
    <Sheet open={Boolean(signal)} onOpenChange={(open) => { if (!open) onClose(); }}>
      {signal && (
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>{signal.instrument.canonicalSymbol} · {signal.direction}</SheetTitle>
            <SheetDescription>Transparent Ghost Trade decision trace. Scores are not probabilities.</SheetDescription>
          </SheetHeader>
          <div className="space-y-4 px-4 pb-6">
            <div className="grid grid-cols-3 gap-2 rounded-md border p-3 text-sm font-mono">
              <span>Confirmed {pct(signal.confirmedScore)}</span>
              <span>Live {signal.liveAdjustment >= 0 ? '+' : ''}{pct(signal.liveAdjustment)}</span>
              <span>Display {pct(signal.displayScore)}</span>
            </div>
            <div className="space-y-2">
              {Object.entries(signal.components).map(([key, component]) => (
                <div key={key} className="rounded-md border p-3 text-xs">
                  <div className="font-semibold">{labels[key] || key}</div>
                  <div className="mt-1 break-words font-mono text-muted-foreground">{JSON.stringify(component)}</div>
                  <div className="mt-1 text-[10px] text-muted-foreground">Confirmed: {signal.confirmedTimes[key.slice(0, 2)] || 'diagnostic'}</div>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2 rounded-md border p-3 text-xs font-mono">
              <span>Entry {signal.entry ?? '—'}</span><span>Stop {signal.stop ?? '—'}</span>
              <span>Target {signal.target ?? '—'}</span><span>R:R {signal.structuralRR?.toFixed(2) ?? '—'}</span>
              <span>Freshness {signal.dataFreshness}</span><span>Spread {signal.spread ?? '—'}</span>
            </div>
            <div className="rounded-md border p-3 text-xs"><div className="font-semibold">Decision trace</div><ul className="mt-1 list-disc pl-4 text-muted-foreground">{signal.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>
            <div className="flex gap-2">
              {canShowExecute(health, signal) && <Button onClick={() => onExecute(signal.signalId)}>Execute Demo</Button>}
              <Button variant="outline" onClick={() => onDismiss(signal.signalId)}>Dismiss</Button>
            </div>
          </div>
        </SheetContent>
      )}
    </Sheet>
  );
}
