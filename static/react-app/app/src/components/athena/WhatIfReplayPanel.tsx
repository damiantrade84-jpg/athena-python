// WhatIfReplayPanel — Phase 6 hypothetical context replay.
//
// Lets the user edit non-locked context fields and POST to /api/ai/whatif.
// Locked safety-field prefixes are disabled in the UI. Advisory-only; never
// calls execution or mutates live config.

import { memo, useCallback, useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { postWhatif } from '@/lib/apiClient';
import type { WhatIfReplayResponse } from '@/types/athena';

const LOCKED_PREFIXES = ['risk_', 'execution_', 'guardian_', 'kill_switch', 'broker_', 'sl_', 'tp_', 'rr_'];
const LOCKED_KEYS = new Set(['config', 'config_path', 'threshold', 'weight']);

function isLockedField(key: string): boolean {
  const kl = key.toLowerCase();
  return LOCKED_PREFIXES.some((p) => kl.startsWith(p)) || LOCKED_KEYS.has(kl);
}

interface WhatIfReplayPanelProps {
  baseContext: Record<string, unknown>;
}

function WhatIfReplayPanelImpl({ baseContext }: WhatIfReplayPanelProps) {
  const editableKeys = useMemo(
    () => Object.keys(baseContext).filter((k) => !isLockedField(k)).slice(0, 8),
    [baseContext],
  );

  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [result, setResult] = useState<WhatIfReplayResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleRun = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const parsedOverrides: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(overrides)) {
        if (!v.trim()) continue;
        const num = Number(v);
        parsedOverrides[k] = Number.isFinite(num) && v.trim() !== '' ? num : v;
      }
      const resp = await postWhatif(baseContext, parsedOverrides);
      setResult(resp);
      if (!resp.enabled) setError('what-if replay disabled (config gate off)');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'whatif_failed');
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [baseContext, overrides]);

  if (editableKeys.length === 0) return null;

  return (
    <details className="rounded-md border border-border/40 bg-background/20 px-2 py-1.5">
      <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground flex items-center gap-2">
        What-if replay
        <Badge className="text-[9px] border border-amber-500/40 text-amber-300 bg-amber-500/10">
          advisory only
        </Badge>
      </summary>
      <div className="mt-2 space-y-2">
        {editableKeys.map((key) => {
          const locked = isLockedField(key);
          const baseVal = baseContext[key];
          return (
            <div key={key} className="flex items-center gap-2">
              <label className="text-[10px] text-muted-foreground w-28 shrink-0 truncate" title={key}>
                {key}
              </label>
              <Input
                className="h-7 text-[11px] flex-1"
                placeholder={String(baseVal ?? '')}
                value={overrides[key] ?? ''}
                disabled={locked}
                onChange={(e) => setOverrides((prev) => ({ ...prev, [key]: e.target.value }))}
              />
              {locked ? (
                <Badge variant="outline" className="text-[8px] shrink-0">locked</Badge>
              ) : null}
            </div>
          );
        })}
        <Button size="sm" className="h-7 text-[10px]" onClick={() => void handleRun()} disabled={loading}>
          {loading ? 'Running…' : 'Run what-if'}
        </Button>
        {error ? <div className="text-[10px] text-warning">{error}</div> : null}
        {result?.enabled && result.hypothetical_context ? (
          <pre className="text-[9px] whitespace-pre-wrap break-words text-muted-foreground border border-border/40 rounded p-2 max-h-32 overflow-auto">
            {JSON.stringify(result.hypothetical_context, null, 2)}
          </pre>
        ) : null}
        {result?.rejected_overrides?.length ? (
          <div className="text-[9px] text-warning">
            Rejected (locked): {result.rejected_overrides.join(', ')}
          </div>
        ) : null}
      </div>
    </details>
  );
}

const WhatIfReplayPanel = memo(WhatIfReplayPanelImpl);
export default WhatIfReplayPanel;
