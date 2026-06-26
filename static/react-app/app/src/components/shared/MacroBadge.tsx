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

type MacroTone = {
  toneLabel?: string;
  toneScore?: number | null;
  confidence?: number | null;
  executionUse?: string;
} | null;

const POLL_MS = 60_000;

function fmtCountdown(sec?: number | null): string {
  if (sec == null || sec <= 0) return '';
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h${String(m % 60).padStart(2, '0')}`;
}

function ToneChip({ tone }: { tone: MacroTone }) {
  if (!tone || !tone.toneLabel || tone.toneLabel === 'UNKNOWN') {
    if (tone && tone.toneLabel === 'UNKNOWN') {
      return (
        <div
          className="flex items-center gap-2 px-2.5 py-1 rounded-md border text-[10px] font-mono tracking-wider"
          style={{
            background: 'hsl(var(--muted) / 0.20)',
            borderColor: 'hsl(var(--muted-foreground) / 0.30)',
            color: 'hsl(var(--muted-foreground))',
          }}
          title="FOMC tone unavailable / low confidence. Context only."
        >
          FOMC TONE: LOW CONFIDENCE
        </div>
      );
    }
    return null;
  }
  const score = tone.toneScore ?? 0;
  const conf = Math.round((tone.confidence ?? 0) * 100);
  const hawk = score > 10;
  const dov = score < -10;
  const color = hawk ? 'hsl(var(--short))' : dov ? 'hsl(var(--long))' : 'hsl(var(--muted-foreground))';
  const bg = hawk ? 'hsl(var(--short) / 0.10)' : dov ? 'hsl(var(--long) / 0.10)' : 'hsl(var(--muted) / 0.20)';
  const border = hawk ? 'hsl(var(--short) / 0.40)' : dov ? 'hsl(var(--long) / 0.40)' : 'hsl(var(--muted-foreground) / 0.30)';
  const label = tone.toneLabel.replace(/_/g, ' ');
  const sign = score > 0 ? `+${score}` : `${score}`;
  return (
    <div
      className="flex items-center gap-2 px-2.5 py-1 rounded-md border text-[10px] font-mono tracking-wider"
      style={{ background: bg, borderColor: border, color }}
      title={`FOMC tone is macro context only — confirm with post-news structure. Confidence ${conf}%.`}
    >
      FOMC TONE: {label} · {sign} · {conf}%
    </div>
  );
}

export default function MacroBadge() {
  const [state, setState] = useState<MacroState | null>(null);
  const [tone, setTone] = useState<MacroTone>(null);

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
      try {
        const tResp = await fetch('/api/macro/fomc-tone');
        const tBody = await tResp.json();
        if (alive && tBody?.success !== false) setTone(tBody?.tone ?? null);
      } catch {
        /* fail-safe: tone is optional context */
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
  const lockoutInactive = risk === 'NONE' || risk === 'COMPLETED';
  // Show the tone chip even outside a lockout window (post-release context).
  if (lockoutInactive) {
    return <ToneChip tone={tone} />;
  }

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
    <div className="flex items-center gap-3">
      <div
        className={`flex items-center gap-2 px-2.5 py-1 rounded-md border text-[10px] font-mono tracking-wider ${blocking ? 'animate-pulse' : ''}`}
        style={{ background: bg, borderColor: border, color }}
        title={state?.reason || state?.title || 'FOMC macro risk active'}
      >
        <div className="w-1.5 h-1.5 rounded-full bg-current" />
        {label}
      </div>
      <ToneChip tone={tone} />
    </div>
  );
}
