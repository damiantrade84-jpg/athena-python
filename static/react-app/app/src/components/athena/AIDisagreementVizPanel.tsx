// AIDisagreementVizPanel — Phase 6 AI surface disagreement visualization.
//
// Fetches disagreement + evidence-refs in parallel (Promise.all). Advisory-only.
// Memoized; ternary conditional renders throughout.

import { memo, useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Badge } from '@/components/ui/badge';
import { postDisagreement, postEvidenceRefs } from '@/lib/apiClient';
import type {
  AIDisagreementResponse,
  AiSurfaceVerdict,
  EvidenceRef,
  EvidenceRefClaim,
} from '@/types/athena';

interface AIDisagreementVizPanelProps {
  verdicts: Record<string, AiSurfaceVerdict>;
  claims?: EvidenceRefClaim[];
  sources?: Record<string, unknown>;
}

function AIDisagreementVizPanelImpl({
  verdicts,
  claims = [],
  sources = {},
}: AIDisagreementVizPanelProps) {
  const [disagreement, setDisagreement] = useState<AIDisagreementResponse | null>(null);
  const [refs, setRefs] = useState<EvidenceRef[]>([]);

  const verdictsKey = JSON.stringify(verdicts);
  const claimsKey = JSON.stringify(claims);
  const sourcesKey = JSON.stringify(sources);

  useEffect(() => {
    let cancelled = false;
    const surfaceKeys = Object.keys(verdicts);
    if (surfaceKeys.length < 2) {
      setDisagreement(null);
      setRefs([]);
      return;
    }
    Promise.all([
      postDisagreement(verdicts),
      claims.length ? postEvidenceRefs(claims, sources) : Promise.resolve({ enabled: false, evidence_refs: [], do_not_auto_apply: true }),
    ])
      .then(([disResp, refResp]) => {
        if (cancelled) return;
        setDisagreement(disResp);
        setRefs(refResp.enabled ? refResp.evidence_refs : []);
      })
      .catch(() => {
        if (cancelled) return;
        setDisagreement(null);
        setRefs([]);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [verdictsKey, claimsKey, sourcesKey]);

  const spreadData = useMemo(() => {
    if (!disagreement?.confidences) return [];
    return Object.entries(disagreement.confidences).map(([surface, conf]) => ({
      surface,
      confidence: conf ?? 0,
    }));
  }, [disagreement?.confidences]);

  if (!disagreement?.enabled) return null;
  if (!disagreement.divergent && disagreement.decision_agreement !== false) return null;

  return (
    <div className="rounded-md border border-border/50 bg-card/40 p-2 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          AI disagreement
        </span>
        <Badge className="text-[9px] border border-amber-500/40 text-amber-300 bg-amber-500/10">
          advisory only
        </Badge>
        {disagreement.divergent ? (
          <Badge className="text-[9px] border border-rose-500/40 text-rose-300 bg-rose-500/10">
            divergent
          </Badge>
        ) : (
          <Badge className="text-[9px] border border-emerald-500/40 text-emerald-300 bg-emerald-500/10">
            aligned
          </Badge>
        )}
      </div>

      {disagreement.summary ? (
        <div className="text-[10px] text-muted-foreground">{disagreement.summary}</div>
      ) : null}

      {disagreement.decisions ? (
        <div className="flex flex-wrap gap-1">
          {Object.entries(disagreement.decisions).map(([surface, decision]) => (
            <Badge key={surface} variant="outline" className="text-[9px]">
              {surface}: {decision ?? '—'}
            </Badge>
          ))}
        </div>
      ) : null}

      {spreadData.length > 0 ? (
        <ResponsiveContainer width="100%" height={100}>
          <BarChart data={spreadData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
            <XAxis dataKey="surface" tick={{ fontSize: 9 }} />
            <YAxis domain={[0, 1]} tick={{ fontSize: 9 }} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
            <Tooltip
              contentStyle={{ fontSize: 10, background: '#1e293b', border: '1px solid #334155' }}
              formatter={(value: number) => [`${Math.round(value * 100)}%`, 'confidence']}
            />
            <Bar dataKey="confidence" fill="#6366f1" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      ) : null}

      {refs.length > 0 ? (
        <div className="space-y-1">
          {refs.slice(0, 3).map((ref, idx) => (
            <div key={ref.claim_id ?? idx} className="text-[9px] text-muted-foreground truncate">
              {ref.ungrounded ? '⚠' : '✓'} {ref.text}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const AIDisagreementVizPanel = memo(AIDisagreementVizPanelImpl);
export default AIDisagreementVizPanel;
