// EvidenceRefsPanel — Phase 6 citations / grounding tags.
//
// Advisory-only. Fetches POST /api/ai/evidence-refs on mount (config-gated,
// default-off server-side). Renders a list of claims with grounded/ungrounded
// badges. No-op render when disabled or no refs. Memoized to avoid re-renders
// when parent state unrelated to claims/sources changes.

import { memo, useEffect, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { postEvidenceRefs } from '@/lib/apiClient';
import type { EvidenceRef, EvidenceRefClaim } from '@/types/athena';

interface EvidenceRefsPanelProps {
  claims: EvidenceRefClaim[];
  sources: Record<string, unknown>;
}

function EvidenceRefsPanelImpl({ claims, sources }: EvidenceRefsPanelProps) {
  const [refs, setRefs] = useState<EvidenceRef[]>([]);
  const [enabled, setEnabled] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Stable key so the effect only refires when claims/sources actually change.
  const claimsKey = JSON.stringify(claims);
  const sourcesKey = JSON.stringify(sources);

  useEffect(() => {
    let cancelled = false;
    if (!claims.length) {
      setRefs([]);
      setEnabled(false);
      return;
    }
    postEvidenceRefs(claims, sources)
      .then((resp) => {
        if (cancelled) return;
        setEnabled(resp.enabled);
        setRefs(resp.evidence_refs);
        setError(resp.error ?? null);
      })
      .catch((err) => {
        if (cancelled) return;
        setEnabled(false);
        setRefs([]);
        setError(err instanceof Error ? err.message : 'evidence_refs_failed');
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimsKey, sourcesKey]);

  if (!enabled && !error) return null;
  if (!refs.length && !error) return null;

  return (
    <div className="rounded-md border border-border/50 bg-card/40 p-2 space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Evidence refs
        </span>
        <Badge className="text-[9px] border border-amber-500/40 text-amber-300 bg-amber-500/10">
          advisory only
        </Badge>
      </div>
      {error ? (
        <div className="text-[10px] text-warning">evidence refs unavailable: {error}</div>
      ) : null}
      {refs.map((ref, idx) => (
        <div key={ref.claim_id ?? idx} className="text-[10px] flex items-start gap-2">
          <Badge
            className={
              ref.ungrounded
                ? 'text-[9px] border border-rose-500/40 text-rose-300 bg-rose-500/10'
                : 'text-[9px] border border-emerald-500/40 text-emerald-300 bg-emerald-500/10'
            }
          >
            {ref.ungrounded ? 'ungrounded' : 'grounded'}
          </Badge>
          <span className="text-slate-200 break-words">
            {ref.text}
            {ref.source_field ? (
              <span className="text-muted-foreground"> — {ref.source_field}: {String(ref.source_value ?? '—')}</span>
            ) : null}
          </span>
        </div>
      ))}
    </div>
  );
}

const EvidenceRefsPanel = memo(EvidenceRefsPanelImpl);
export default EvidenceRefsPanel;
