import { useEffect, useState } from 'react';

/**
 * FOMC / macro-risk header chip. Read-only: polls the server-trusted macro state
 * and surfaces an advisory banner when an FOMC lockout/caution window is active or
 * upcoming. It never executes, scores, or blocks anything itself — the deterministic
 * gate lives on the backend (macro guard + majorEventRisk.blocksAutoExecution).
 */

type MacroState = {
  macroRisk?: string;
  reason?: string;
  timeToEventSec?: number | null;
  title?: string | null;
};

const POLL_MS = 60_000;

function fmtCountdown(sec?: number | null): string {
  if (sec == null || sec <= 0) return '';
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h${String(m % 60).padStart(2, '0')}`;
}

export default function MacroBadge() {
  const [state, setState] = useState<MacroState | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const resp = await fetch('/api/macro/state');
        const body = await resp.json();
        if (alive && body?.success !== false) setState(body?.state ?? null);
      } catch {
        /* fail-safe: leave last known state, never throw into the header */
      }
    };
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const risk = state?.macroRisk ?? 'NONE';
  if (risk === 'NONE' || risk === 'COMPLETED') return null;

  const blocking = risk === 'LOCKOUT' || risk === 'ACTIVE_RELEASE';
  const caution = risk === 'POST_RELEASE_CAUTION';
  const color = blocking
    ? 'hsl(var(--short))'
    : caution
    ? 'hsl(var(--warning))'
    : 'hsl(var(--gold-light))';
  const bg = blocking
    ? 'hsl(var(--short) / 0.10)'
    : caution
    ? 'hsl(var(--warning) / 0.10)'
    : 'hsl(var(--gold) / 0.10)';
  const border = blocking
    ? 'hsl(var(--short) / 0.40)'
    : caution
    ? 'hsl(var(--warning) / 0.40)'
    : 'hsl(var(--gold) / 0.40)';

  const countdown = risk === 'UPCOMING' ? fmtCountdown(state?.timeToEventSec) : '';
  const label = `FOMC · ${risk.replace(/_/g, ' ')}${countdown ? ` · ${countdown}` : ''}`;

  return (
    <div
      className={`flex items-center gap-2 px-2.5 py-1 rounded-md border text-[10px] font-mono tracking-wider ${blocking ? 'animate-pulse' : ''}`}
      style={{ background: bg, borderColor: border, color }}
      title={state?.reason || state?.title || 'FOMC macro risk active'}
    >
      <div className="w-1.5 h-1.5 rounded-full bg-current" />
      {label}
    </div>
  );
}
