import { AlertTriangle, ShieldCheck } from 'lucide-react';
import { bannerForHealth } from './display';
import type { GhostHealth } from './types';

export function GhostSafetyBanner({ health }: { health: GhostHealth }) {
  const verified = health.executionStatus === 'DEMO VERIFIED' && health.mode !== 'SHADOW';
  return (
    <div className={`flex items-center gap-2 rounded-md border p-3 text-sm font-semibold ${verified ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' : 'border-amber-500/40 bg-amber-500/10 text-amber-200'}`}>
      {verified ? <ShieldCheck className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
      <span>{bannerForHealth(health)}</span>
    </div>
  );
}
