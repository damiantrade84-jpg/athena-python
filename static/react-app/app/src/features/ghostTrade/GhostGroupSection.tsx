import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { GhostSignalCard } from './GhostSignalCard';
import { groupLabels, sortSignals } from './display';
import type { GhostGroup, GhostSignal } from './types';

export function GhostGroupSection({ group, signals, onSelect, sort = 'score', view = 'card' }: { group: GhostGroup; signals: GhostSignal[]; onSelect: (signal: GhostSignal) => void; sort?: 'score' | 'symbol' | 'age'; view?: 'card' | 'list' }) {
  const [open, setOpen] = useState(true);
  return (
    <section className="rounded-lg border border-border/70 bg-card/30">
      <button type="button" className="flex w-full items-center gap-2 p-3 text-left" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <span className="font-semibold">{groupLabels[group.assetGroup]}</span>
        <Badge variant="outline">{group.instrumentsScored}/{group.instrumentsDiscovered} scored</Badge>
        <span className="ml-auto text-xs text-muted-foreground">↑{group.bullish} ↓{group.bearish} · eligible {group.executableDemo}</span>
      </button>
      {open && (
        <div className={`grid gap-3 border-t border-border/60 p-3 ${view === 'card' ? 'md:grid-cols-2 xl:grid-cols-3' : 'grid-cols-1'}`}>
          {sortSignals(signals, sort).map((signal) => <GhostSignalCard key={signal.signalId} signal={signal} onSelect={() => onSelect(signal)} />)}
          {signals.length === 0 && <div className="text-xs text-muted-foreground">No scored signals in this group.</div>}
        </div>
      )}
    </section>
  );
}
