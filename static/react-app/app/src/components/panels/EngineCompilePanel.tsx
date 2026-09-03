import { useEffect, useMemo, useState } from 'react';
import { Combine, Search, Trash2 } from 'lucide-react';

import { useStore } from '@/hooks/useStore';
import apiClient from '@/lib/apiClient';
import { cn } from '@/lib/utils';
import type { PanelId } from '@/types';
import {
  COMPILE_ENGINE_IDS,
  COMPILE_ENGINE_META,
  buildCompileSnapshots,
  compileEngineScanBoard,
  formatCompileScore,
  snapshotFromSolLikeSignals,
  snapshotFromFableSignals,
  type CompileAgreement,
  type CompileEngineHit,
  type CompileRow,
} from '@/lib/engineScanCompile';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

type FilterId = 'all' | 'agree' | 'mixed' | 'conflict' | 'fail';

function clock(iso?: string): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function agreementClass(agreement: CompileAgreement): string {
  if (agreement === 'agree') return 'border-long/40 bg-long/10 text-long';
  if (agreement === 'conflict') return 'border-short/40 bg-short/10 text-short';
  if (agreement === 'mixed') return 'border-warning/40 bg-warning/10 text-warning';
  return 'border-border text-muted-foreground';
}

function agreementLabel(row: CompileRow): string {
  if (row.agreement === 'agree' && row.agreedDirection) {
    return `${row.passedEngines.length} pass · ${row.agreedDirection}`;
  }
  if (row.agreement === 'conflict') return 'conflict';
  if (row.agreement === 'mixed') {
    const fail = row.failedEngines.map((id) => COMPILE_ENGINE_META[id].short).join(', ');
    return fail ? `fail ${fail}` : 'mixed';
  }
  if (row.agreement === 'solo' && row.agreedDirection) return `1 pass · ${row.agreedDirection}`;
  if (row.agreement === 'solo') return '1 pass';
  return 'no pass';
}

function hitClass(hit: CompileEngineHit | null): string {
  if (!hit) return 'text-muted-foreground/50';
  if (hit.stance === 'pass') return 'text-long';
  if (hit.stance === 'watch') return 'text-warning';
  if (hit.stance === 'fail' || hit.stance === 'absent') return 'text-short';
  return 'text-muted-foreground';
}

function hitLabel(hit: CompileEngineHit | null): string {
  if (!hit) return '—';
  if (hit.stance === 'absent') return 'no';
  const decision = hit.decision || hit.stance.toUpperCase();
  const dir = hit.direction && hit.direction !== 'NONE' ? hit.direction[0] : '';
  const score = formatCompileScore(hit.score, hit.maxScore);
  return [decision, dir, score !== '—' ? score : null].filter(Boolean).join(' ');
}

function matchesFilter(row: CompileRow, filter: FilterId): boolean {
  if (filter === 'all') return true;
  if (filter === 'agree') return row.agreement === 'agree';
  if (filter === 'mixed') return row.agreement === 'mixed';
  if (filter === 'conflict') return row.agreement === 'conflict';
  return row.failedEngines.length > 0;
}

function EngineCell({ hit }: { hit: CompileEngineHit | null }) {
  return (
    <div className={cn('font-mono text-[10px] leading-tight', hitClass(hit))} title={hit?.reason || undefined}>
      <div>{hitLabel(hit)}</div>
      {hit?.reason && hit.stance !== 'pass' && (
        <div className="max-w-[9rem] truncate text-[9px] text-muted-foreground">{hit.reason}</div>
      )}
    </div>
  );
}

export default function EngineCompilePanel() {
  const {
    scanCacheA,
    scanCacheB,
    scanCacheAMeta,
    scanCacheBMeta,
    engineScanSnapshots,
    engineCompilePending,
    setEngineScanSnapshot,
    clearEngineScanSnapshots,
    setActivePanel,
  } = useStore();
  const [filter, setFilter] = useState<FilterId>('all');
  const [query, setQuery] = useState('');

  const snapshots = useMemo(
    () => buildCompileSnapshots({
      scanCacheA,
      scanCacheAMeta,
      scanCacheB,
      scanCacheBMeta,
      extra: engineScanSnapshots,
    }),
    [scanCacheA, scanCacheAMeta, scanCacheB, scanCacheBMeta, engineScanSnapshots],
  );
  const board = useMemo(() => compileEngineScanBoard(snapshots), [snapshots]);

  useEffect(() => {
    let cancelled = false;
    const pendingSol = Boolean(engineCompilePending.sol) && !engineScanSnapshots.sol;
    const pendingGrok = Boolean(engineCompilePending.grok) && !engineScanSnapshots.grok;
    const pendingFable = Boolean(engineCompilePending.fable) && !engineScanSnapshots.fable;
    if (!pendingSol && !pendingGrok && !pendingFable) return undefined;

    const ingest = async () => {
      if (pendingSol) {
        try {
          const scan = await apiClient.get<{ status?: string; completedAt?: string }>('/api/sol/scan/current');
          if (!cancelled && scan?.status === 'COMPLETED') {
            const response = await apiClient.get<{ signals?: unknown[] }>('/api/sol/signals?decisions=READY,WATCH,BLOCKED&limit=500');
            if (!cancelled) {
              setEngineScanSnapshot(snapshotFromSolLikeSignals('sol', response.signals, scan.completedAt));
            }
          }
        } catch {
          /* last-scan ingest only */
        }
      }
      if (pendingGrok) {
        try {
          const scan = await apiClient.get<{ status?: string; completedAt?: string }>('/api/grok/scan/current');
          if (!cancelled && scan?.status === 'COMPLETED') {
            const response = await apiClient.get<{ signals?: unknown[] }>('/api/grok/signals?decisions=READY,WATCH,BLOCKED&limit=500');
            if (!cancelled) {
              setEngineScanSnapshot(snapshotFromSolLikeSignals('grok', response.signals, scan.completedAt));
            }
          }
        } catch {
          /* last-scan ingest only */
        }
      }
      if (pendingFable) {
        try {
          const scan = await apiClient.get<{ status?: string; completedAt?: string }>('/api/fable/scan/current');
          if (!cancelled && scan?.status === 'COMPLETED') {
            const response = await apiClient.get<{ signals?: unknown[] }>('/api/fable/signals?decisions=EXECUTE,STAGE,OBSERVE,VOID&limit=500');
            if (!cancelled) {
              setEngineScanSnapshot(snapshotFromFableSignals(response.signals, scan.completedAt));
            }
          }
        } catch {
          /* last-scan ingest only */
        }
      }
    };

    void ingest();
    const timer = window.setInterval(() => { void ingest(); }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    engineCompilePending.sol,
    engineCompilePending.grok,
    engineCompilePending.fable,
    engineScanSnapshots.sol,
    engineScanSnapshots.grok,
    engineScanSnapshots.fable,
    setEngineScanSnapshot,
  ]);

  const rows = useMemo(() => {
    const needle = query.trim().toUpperCase();
    return board.rows.filter((row) => {
      if (!matchesFilter(row, filter)) return false;
      if (!needle) return true;
      return `${row.display} ${row.key}`.toUpperCase().includes(needle);
    });
  }, [board.rows, filter, query]);

  const counts = useMemo(() => ({
    all: board.rows.length,
    agree: board.rows.filter((row) => row.agreement === 'agree').length,
    mixed: board.rows.filter((row) => row.agreement === 'mixed').length,
    conflict: board.rows.filter((row) => row.agreement === 'conflict').length,
    fail: board.rows.filter((row) => row.failedEngines.length > 0).length,
  }), [board.rows]);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end gap-3">
        <div className="min-w-0">
          <h2 className="panel-title flex items-center gap-2">
            <Combine className="h-3.5 w-3.5" />
            Scan Board
          </h2>
          <p className="mt-1 max-w-2xl text-[11px] leading-snug text-muted-foreground">
            Compiles the last scan you ran on each engine. This tab does not scan,
            score, or execute. Scores stay native to each engine.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          onClick={() => clearEngineScanSnapshots()}
          disabled={!Object.keys(engineScanSnapshots).length && !engineCompilePending.sol && !engineCompilePending.grok && !engineCompilePending.fable}
        >
          <Trash2 className="mr-1 h-3.5 w-3.5" />
          Clear SOL–GROK
        </Button>
      </header>

      <div className="flex flex-wrap gap-1.5">
        {COMPILE_ENGINE_IDS.map((engine) => {
          const scanned = board.scannedEngines.includes(engine);
          const pending = Boolean(engineCompilePending[engine]);
          return (
            <button
              key={engine}
              type="button"
              onClick={() => setActivePanel(COMPILE_ENGINE_META[engine].panel as PanelId)}
              className={cn(
                'rounded border px-2 py-1 text-[10px] uppercase tracking-wide',
                scanned ? 'border-primary/40 bg-primary/10 text-foreground' : 'border-border text-muted-foreground',
              )}
              title={`Open ${COMPILE_ENGINE_META[engine].label}`}
            >
              {COMPILE_ENGINE_META[engine].short}
              {scanned ? ` · ${clock(board.scannedAt[engine])}` : pending ? ' · scanning' : ' · —'}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {(['all', 'agree', 'mixed', 'conflict', 'fail'] as const).map((id) => (
          <Button
            key={id}
            size="sm"
            variant={filter === id ? 'default' : 'outline'}
            className="h-7 px-2 text-[11px] capitalize"
            onClick={() => setFilter(id)}
          >
            {id} {counts[id]}
          </Button>
        ))}
        <div className="relative ml-auto w-full max-w-xs">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter pair"
            className="h-8 pl-7 text-xs"
            aria-label="Filter compiled pairs"
          />
        </div>
      </div>

      {board.scannedEngines.length === 0 && (
        <div className="surface-raised px-4 py-8 text-center text-sm text-muted-foreground">
          Scan Engine A, Engine B, SOL, OPUS, KIMI, OX Alpha, or GROK on their own tabs,
          then come back. The board only joins those last results.
        </div>
      )}

      {board.scannedEngines.length > 0 && rows.length === 0 && (
        <div className="surface-raised px-4 py-8 text-center text-sm text-muted-foreground">
          No pairs match this filter.
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="hidden md:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="sticky left-0 z-10 bg-background">Pair</TableHead>
                  <TableHead>Agree</TableHead>
                  {COMPILE_ENGINE_IDS.map((engine) => (
                    <TableHead key={engine}>{COMPILE_ENGINE_META[engine].short}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.key}>
                    <TableCell className="sticky left-0 z-10 bg-background font-medium">{row.display}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={cn('text-[9px] uppercase', agreementClass(row.agreement))}>
                        {agreementLabel(row)}
                      </Badge>
                    </TableCell>
                    {COMPILE_ENGINE_IDS.map((engine) => (
                      <TableCell key={engine}>
                        <EngineCell hit={row.hits[engine]} />
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="space-y-2 md:hidden">
            {rows.map((row) => (
              <article key={row.key} className="surface-raised space-y-2 p-3">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-medium">{row.display}</h3>
                  <Badge variant="outline" className={cn('text-[9px] uppercase', agreementClass(row.agreement))}>
                    {agreementLabel(row)}
                  </Badge>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {COMPILE_ENGINE_IDS.map((engine) => (
                    <div key={engine} className="rounded border border-border/60 px-2 py-1.5">
                      <div className="label">{COMPILE_ENGINE_META[engine].short}</div>
                      <EngineCell hit={row.hits[engine]} />
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
