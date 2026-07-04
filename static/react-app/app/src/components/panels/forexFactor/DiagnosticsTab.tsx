import { useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { AlertTriangle, Download, Info, XCircle } from 'lucide-react';
import { getForexDiagnostics } from '@/lib/forexFactorApi';
import { TabEmpty, TabShell, useForexTabFetch } from './shared';

function severityIcon(severity?: string) {
  const s = (severity || '').toLowerCase();
  if (s === 'error' || s === 'critical') return <XCircle className="h-3.5 w-3.5 text-destructive" />;
  if (s === 'warning') return <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />;
  return <Info className="h-3.5 w-3.5 text-muted-foreground" />;
}

export default function DiagnosticsTab() {
  const { envelope, loading, error, diagnostics: fetchDiag, refresh } = useForexTabFetch(
    () => getForexDiagnostics(),
    [],
    30000,
  );

  const data = envelope?.data;
  const items = data?.diagnostics || [];

  const grouped = useMemo(() => {
    const map = new Map<string, typeof items>();
    for (const d of items) {
      const code = String(d.code || d.source || 'unknown');
      if (!map.has(code)) map.set(code, []);
      map.get(code)!.push(d);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [items]);

  const handleDownload = () => {
    const blob = new Blob([JSON.stringify(envelope, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `forex-diagnostics-${data?.as_of_date || 'latest'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <TabShell
      loading={loading}
      error={error}
      diagnostics={fetchDiag}
      warnings={envelope?.warnings}
      onRefresh={refresh}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs text-muted-foreground space-y-0.5">
          {data?.as_of_date && <div>As-of: <span className="font-mono">{data.as_of_date}</span></div>}
          {data?.parity && (
            <div>
              Parity: <span className="font-mono">{String(data.parity.parity)}</span>
              {' · '}
              <span className="font-mono text-[10px]">{String(data.parity.adapter)}</span>
            </div>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={handleDownload} disabled={!envelope}>
          <Download className="h-3.5 w-3.5 mr-1" /> JSON
        </Button>
      </div>

      {items.length === 0 ? (
        <TabEmpty message="No diagnostics on record." />
      ) : (
        <div className="space-y-3">
          {grouped.map(([code, rows]) => (
            <div key={code} className="rounded border border-border/60 overflow-hidden">
              <div className="px-3 py-1.5 bg-muted/30 flex items-center gap-2">
                {severityIcon(rows[0]?.severity as string | undefined)}
                <Badge variant="outline" className="font-mono text-[10px]">{code}</Badge>
                <span className="text-[10px] text-muted-foreground">×{rows.length}</span>
              </div>
              <ul className="p-2 space-y-1">
                {rows.map((d, i) => (
                  <li key={`${code}-${i}`} className="text-[11px] font-mono text-muted-foreground">
                    {d.symbol ? `[${d.symbol}] ` : ''}
                    {d.message || JSON.stringify(d)}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </TabShell>
  );
}
