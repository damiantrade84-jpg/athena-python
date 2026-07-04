import { useCallback, useEffect, useRef, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorBanner } from '@/components/shared';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import type { ForexDiagnostic, ForexEnvelope } from '@/lib/forexFactorApi';
import { ForexApiError } from '@/lib/forexFactorApi';

export const FX_ACCENT = 'hsl(220 70% 55%)';

export function Stat({ label, value, warn }: { label: string; value: React.ReactNode; warn?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className={`text-sm font-mono ${warn ? 'text-amber-500' : ''}`}>{value ?? '—'}</span>
    </div>
  );
}

export function actionBadgeClass(action?: string): string {
  switch ((action || '').toUpperCase()) {
    case 'BUY':
    case 'OPEN_LONG':
      return 'badge-long';
    case 'SELL':
    case 'OPEN_SHORT':
      return 'badge-short';
    case 'REDUCE':
      return 'badge-neutral';
    case 'FLATTEN':
    case 'NO_TRADE':
      return 'badge-short';
    default:
      return 'badge-neutral';
  }
}

export function gateResultClass(result?: string): string {
  switch ((result || '').toLowerCase()) {
    case 'pass':
      return 'badge-long';
    case 'reduced':
      return 'badge-neutral';
    case 'fail':
      return 'badge-short';
    default:
      return 'badge-neutral';
  }
}

export function DiagnosticsList({ items }: { items?: ForexDiagnostic[] }) {
  if (!items?.length) return null;
  return (
    <ul className="text-[11px] text-muted-foreground space-y-1 mt-2">
      {items.map((d, i) => (
        <li key={`diag-${i}`} className="font-mono">
          {(d.code || d.field || 'diag')}: {d.message || JSON.stringify(d)}
        </li>
      ))}
    </ul>
  );
}

export function WarningsBanner({ warnings }: { warnings?: string[] }) {
  if (!warnings?.length) return null;
  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200 space-y-0.5">
      {warnings.map((w) => (
        <div key={w} className="flex items-center gap-1.5">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span>{w}</span>
        </div>
      ))}
    </div>
  );
}

export function TabLoading() {
  return (
    <div className="space-y-3 p-2">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

export function TabEmpty({ message }: { message: string }) {
  return <p className="text-sm text-muted-foreground p-4">{message}</p>;
}

interface FetchState<T> {
  envelope: ForexEnvelope<T> | null;
  loading: boolean;
  error: string | null;
  diagnostics: ForexDiagnostic[];
  refresh: () => void;
}

export function useForexTabFetch<T>(
  loader: () => Promise<ForexEnvelope<T>>,
  deps: unknown[] = [],
  pollMs = 0,
): FetchState<T> {
  const [envelope, setEnvelope] = useState<ForexEnvelope<T> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<ForexDiagnostic[]>([]);
  const staleRef = useRef(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await loader();
      if (!staleRef.current) {
        setEnvelope(result);
        setDiagnostics(result.diagnostics || []);
      }
    } catch (err) {
      if (!staleRef.current) {
        const fxErr = err instanceof ForexApiError ? err : null;
        setError(err instanceof Error ? err.message : 'Unknown error');
        setDiagnostics(fxErr?.envelope?.diagnostics || []);
        setEnvelope(fxErr?.envelope as ForexEnvelope<T> | null ?? null);
      }
    } finally {
      if (!staleRef.current) setLoading(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    staleRef.current = false;
    void refresh();
    if (pollMs > 0) {
      const id = setInterval(refresh, pollMs);
      return () => {
        staleRef.current = true;
        clearInterval(id);
      };
    }
    return () => { staleRef.current = true; };
  }, [refresh, pollMs]);

  return { envelope, loading, error, diagnostics, refresh };
}

export function TabShell({
  loading,
  error,
  diagnostics,
  warnings,
  onRefresh,
  children,
}: {
  loading: boolean;
  error: string | null;
  diagnostics?: ForexDiagnostic[];
  warnings?: string[];
  onRefresh?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        {onRefresh && (
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={loading}>
            <RefreshCw className="h-3.5 w-3.5 mr-1" /> Refresh
          </Button>
        )}
      </div>
      {error && <ErrorBanner message={error} onRetry={onRefresh} />}
      <DiagnosticsList items={diagnostics} />
      <WarningsBanner warnings={warnings} />
      {loading ? <TabLoading /> : children}
    </div>
  );
}

export function percentileColor(pct: number | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return 'text-muted-foreground';
  if (pct >= 80) return 'text-short';
  if (pct <= 20) return 'text-long';
  return 'text-foreground';
}
