import { useCallback, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Check, ChevronDown, Search, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { PairListEntry } from '@/types/athena';

const PAIR_GROUP_ORDER = ['forex', 'crypto', 'stock', 'index', 'commodity', 'etf', 'other'];

const GROUP_LABELS: Record<string, string> = {
  forex: 'Forex',
  crypto: 'Crypto',
  stock: 'Stocks',
  index: 'Indices',
  commodity: 'Commodities',
  etf: 'ETFs',
  other: 'Other',
};

function pairKey(p: PairListEntry): string {
  return String(p.symbol || p.display || '').trim();
}

function normalizeToken(token: string): string {
  return token.trim().toUpperCase().replace(/\s+/g, '').replace(/\//g, '').replace(/-/g, '');
}

function resolvePairKey(token: string, allPairs: PairListEntry[]): string {
  const t = token.trim();
  if (!t) return t;
  const compact = normalizeToken(t);
  for (const p of allPairs) {
    const key = pairKey(p);
    if (!key) continue;
    const variants = new Set([
      normalizeToken(key),
      normalizeToken(String(p.display || '')),
      normalizeToken(String(p.symbol || '')),
    ]);
    if (variants.has(compact)) return key;
  }
  return t;
}

function parseSelected(value: string, allPairs: PairListEntry[]): Set<string> {
  const out = new Set<string>();
  for (const raw of value.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean)) {
    out.add(resolvePairKey(raw, allPairs));
  }
  return out;
}

function joinSelected(keys: Set<string>): string {
  return Array.from(keys).join(', ');
}

function groupPairsByType(pairs: PairListEntry[]): Record<string, PairListEntry[]> {
  const groups: Record<string, PairListEntry[]> = {};
  for (const p of pairs) {
    const g = (p.type || 'other').toLowerCase();
    (groups[g] ??= []).push(p);
  }
  for (const items of Object.values(groups)) {
    items.sort((a, b) => String(a.display || a.symbol).localeCompare(String(b.display || b.symbol)));
  }
  return groups;
}

function orderedPairGroups(groups: Record<string, PairListEntry[]>): [string, PairListEntry[]][] {
  const seen = new Set<string>();
  const out: [string, PairListEntry[]][] = [];
  for (const g of PAIR_GROUP_ORDER) {
    const items = groups[g];
    if (items?.length) {
      out.push([g, items]);
      seen.add(g);
    }
  }
  for (const [g, items] of Object.entries(groups)) {
    if (!seen.has(g) && items.length) out.push([g, items]);
  }
  return out;
}

export interface MultiPairPickerProps {
  value: string;
  onChange: (value: string) => void;
  allPairs: PairListEntry[];
  /** When set and not `all`, only pairs of this asset type are listed. */
  assetClassFilter?: string;
  placeholder?: string;
  className?: string;
}

export default function MultiPairPicker({
  value,
  onChange,
  allPairs,
  assetClassFilter,
  placeholder = 'Select pairs…',
  className,
}: MultiPairPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  const enabledPairs = useMemo(
    () => allPairs.filter((p) => p.enabled !== false && pairKey(p)),
    [allPairs],
  );

  const visiblePairs = useMemo(() => {
    const ac = (assetClassFilter || '').toLowerCase();
    if (!ac || ac === 'all') return enabledPairs;
    return enabledPairs.filter((p) => (p.type || '').toLowerCase() === ac);
  }, [enabledPairs, assetClassFilter]);

  const selectedSet = useMemo(() => parseSelected(value, enabledPairs), [value, enabledPairs]);

  const groupedPairs = useMemo(() => groupPairsByType(visiblePairs), [visiblePairs]);

  const filteredGroups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const entries = orderedPairGroups(groupedPairs);
    if (!q) return entries;
    return entries
      .map(([type, items]) => {
        const hits = items.filter((p) => {
          const hay = `${p.display || ''} ${p.symbol || ''} ${type}`.toLowerCase();
          return hay.includes(q);
        });
        return hits.length ? ([type, hits] as [string, PairListEntry[]]) : null;
      })
      .filter((row): row is [string, PairListEntry[]] => row !== null);
  }, [groupedPairs, query]);

  const labelByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of enabledPairs) {
      const key = pairKey(p);
      if (key) map.set(key, String(p.display || p.symbol || key));
    }
    return map;
  }, [enabledPairs]);

  const emitSelection = useCallback(
    (next: Set<string>) => onChange(joinSelected(next)),
    [onChange],
  );

  const togglePair = useCallback(
    (key: string) => {
      const next = new Set(selectedSet);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      emitSelection(next);
    },
    [selectedSet, emitSelection],
  );

  const toggleGroup = useCallback(
    (items: PairListEntry[]) => {
      const keys = items.map(pairKey).filter(Boolean);
      const allOn = keys.length > 0 && keys.every((k) => selectedSet.has(k));
      const next = new Set(selectedSet);
      if (allOn) {
        for (const k of keys) next.delete(k);
      } else {
        for (const k of keys) next.add(k);
      }
      emitSelection(next);
    },
    [selectedSet, emitSelection],
  );

  const clearAll = useCallback(() => emitSelection(new Set()), [emitSelection]);

  const handleOpenChange = useCallback((next: boolean) => {
    setOpen(next);
    if (!next) setQuery('');
  }, []);

  const selectedLabels = useMemo(
    () => Array.from(selectedSet).map((k) => ({ key: k, label: labelByKey.get(k) || k })),
    [selectedSet, labelByKey],
  );

  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex items-center gap-2 flex-wrap">
        <Popover open={open} onOpenChange={handleOpenChange}>
          <PopoverTrigger asChild>
            <Button variant="outline" className="h-8 text-xs gap-1.5 font-normal">
              {placeholder}
              <ChevronDown className="w-3 h-3 opacity-60" />
              {selectedSet.size > 0 && (
                <Badge variant="secondary" className="text-[9px] ml-0.5">
                  {selectedSet.size}
                </Badge>
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-[300px] p-0" align="start">
            <div className="p-2 border-b flex items-center gap-1.5">
              <Search className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
              <input
                autoFocus
                placeholder="Search pairs…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="flex-1 text-xs bg-transparent outline-none placeholder:text-muted-foreground"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery('')}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
            <ScrollArea className="h-[320px]">
              {filteredGroups.length === 0 && (
                <p className="text-[11px] text-muted-foreground p-3">No pairs found.</p>
              )}
              {filteredGroups.map(([type, items]) => {
                const keys = items.map(pairKey).filter(Boolean);
                const allOn = keys.length > 0 && keys.every((k) => selectedSet.has(k));
                const someOn = keys.some((k) => selectedSet.has(k));
                return (
                  <div key={type}>
                    <div className="flex items-center justify-between px-3 py-1.5 bg-muted/30 border-b border-border/40">
                      <span className="text-[9px] uppercase font-semibold text-muted-foreground tracking-widest">
                        {GROUP_LABELS[type] || type}
                        <span className="opacity-60 ml-1">({items.length})</span>
                      </span>
                      <button
                        type="button"
                        className="text-[9px] uppercase tracking-wide text-primary hover:underline"
                        onClick={() => toggleGroup(items)}
                      >
                        {allOn ? 'Clear group' : someOn ? 'Select rest' : 'Select group'}
                      </button>
                    </div>
                    {items.map((p) => {
                      const key = pairKey(p);
                      const checked = selectedSet.has(key);
                      return (
                        <div
                          key={`${type}:${key}`}
                          className={cn(
                            'flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-muted/50 transition-colors',
                            checked && 'bg-primary/5',
                          )}
                          onClick={() => togglePair(key)}
                        >
                          <Checkbox checked={checked} className="h-3.5 w-3.5" />
                          <span className="text-xs font-mono flex-1">{p.display || p.symbol}</span>
                          {checked && <Check className="w-3 h-3 text-primary shrink-0" />}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </ScrollArea>
            <div className="p-2 border-t flex items-center justify-between">
              <span className="text-[10px] text-muted-foreground">
                {selectedSet.size} selected · empty = entire asset class
              </span>
              <div className="flex gap-1">
                <Button size="sm" variant="ghost" className="h-6 text-[10px]" onClick={clearAll}>
                  Clear all
                </Button>
                <Button
                  size="sm"
                  className="h-6 text-[10px]"
                  onClick={() => handleOpenChange(false)}
                >
                  Done
                </Button>
              </div>
            </div>
          </PopoverContent>
        </Popover>
        {selectedSet.size > 0 && (
          <Button size="sm" variant="ghost" className="h-8 text-[10px] text-muted-foreground" onClick={clearAll}>
            Clear selection
          </Button>
        )}
      </div>
      {selectedLabels.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {selectedLabels.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-muted/30 px-2 py-0.5 text-[10px] font-mono hover:bg-muted/50"
              onClick={() => togglePair(key)}
              title="Click to remove"
            >
              {label}
              <X className="w-2.5 h-2.5 opacity-60" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
