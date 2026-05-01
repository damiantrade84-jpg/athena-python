// Execution feature module (compatibility-first extraction).
// Handles signal resolution, sizing, and POST to /api/quick-execute.
// All functions exposed on window.ExecutionFeature for legacy code.

import apiClient from './apiClient';

// ── Types ────────────────────────────────────────────────────────────────────

interface SignalLike {
  symbol?: string;
  display?: string;
  pair?: string;
  direction?: string;
  entry?: number;
  sl?: number;
  tp?: number;
  tp1?: number;
  tp2?: number;
  price?: number;
  type?: string;
  style?: string;
  confluenceScore?: number;
  conviction?: number;
  ts?: string;
  timestamp?: string;
  [key: string]: unknown;
}

interface ConsensusItem {
  symbol?: string;
  display?: string;
  direction?: string;
  entry?: number;
  sl?: number;
  tp?: number;
  style?: string;
  type?: string;
  conviction?: number;
  tier?: string;
  trade?: boolean;
  engine_b_raw?: Record<string, unknown>;
  sizing_override?: number;
  [key: string]: unknown;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function _normSymbol(v: unknown): string {
  return String(v || '').toUpperCase().replace(/[=^.]/g, '');
}

function _resolveSignal(symbol: string): SignalLike | null {
  const list: SignalLike[] = (window as unknown as { allSignals?: SignalLike[] }).allSignals || [];
  const target = _normSymbol(symbol);
  const sig = list.find((s: SignalLike) =>
    _normSymbol(s.symbol) === target ||
    _normSymbol(s.display) === target ||
    _normSymbol(s.pair) === target
  );
  if (sig) return sig;
  const sid = String(symbol || '').replace(/[\/=^.]/g, '_');
  return (window as unknown as Record<string, SignalLike | undefined>)['_nakedSig_' + sid] || null;
}

function _resolveSignalRef(signalRef: string): SignalLike | null {
  const ref = String(signalRef || '');
  const exact = ((window as unknown as { _execSignalsByKey?: Record<string, SignalLike> })._execSignalsByKey || {})[ref];
  if (exact) return exact;
  const naked = (window as unknown as Record<string, SignalLike | undefined>)['_nakedSig_' + ref];
  if (naked) return naked;
  return _resolveSignal(signalRef);
}

function _signalSid(sig: SignalLike, fallback?: string): string {
  const base = (sig && (sig.symbol || sig.display || sig.pair)) || String(fallback || '');
  return String(base).replace(/[\/=^.]/g, '_');
}

// ── Public functions ─────────────────────────────────────────────────────────

export function getExecutionSizingOverride(): number {
  // React app path: global override set by React components
  const reactGlobal = (window as unknown as Record<string, unknown>).__execRiskSizing;
  if (typeof reactGlobal === 'number' && reactGlobal >= 0.25 && reactGlobal <= 1.0) {
    return reactGlobal;
  }
  // Legacy DOM path
  const el = document.getElementById('execRiskSizing') as HTMLInputElement | null;
  const v = el ? parseFloat(el.value) : NaN;
  if (!(v >= 0.25 && v <= 1)) return 1.0;
  return v;
}

export async function postQuickExecute(
  payload: Record<string, unknown>,
  btn: HTMLElement | null,
  successMsg?: string
): Promise<{ ticket: string; ok?: boolean; error?: string }> {
  const old = btn ? btn.textContent : '';
  const btnEl = btn as HTMLButtonElement | null;
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = 'Executing...';
  }
  try {
    const resData = await apiClient.postJson('/api/quick-execute', payload) as { ticket: string; ok?: boolean; error?: string };
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = 'Success! ' + resData.ticket;
      setTimeout(function () {
        if (btnEl) btnEl.style.display = 'none';
      }, 2000);
    }
    const showToast = (window as unknown as { showToast?: (msg: string, err?: boolean) => void }).showToast;
    if (showToast) {
      showToast(successMsg || 'Executed successfully. Ticket: ' + resData.ticket);
    }
    return resData;
  } catch (err) {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = old || 'RETRY QUICK EXEC';
    }
    const showToast = (window as unknown as { showToast?: (msg: string, err?: boolean) => void }).showToast;
    if (showToast) {
      showToast('Execute Error: ' + (err instanceof Error ? err.message : String(err)), true);
    }
    throw err;
  }
}

export async function executeSignalStyle(
  signalRef: string,
  pipMode?: string,
  buttonId?: string
): Promise<void> {
  const sig = _resolveSignalRef(signalRef);
  if (!sig) {
    const showToast = (window as unknown as { showToast?: (msg: string, err?: boolean) => void }).showToast;
    if (showToast) showToast('Signal not found for ' + signalRef, true);
    return;
  }
  const sid = _signalSid(sig, signalRef);
  const symbol = sig.display || sig.pair || sig.symbol || signalRef;
  const btn = document.getElementById(
    buttonId || 'exec-style-' + (pipMode || 'swing') + '-' + sid
  );
  const buildPayload = (window as unknown as { buildLiveSignalPayload?: (sig: SignalLike) => Record<string, unknown> }).buildLiveSignalPayload;
  const payload: Record<string, unknown> = {
    signal: buildPayload ? buildPayload(sig) : sig,
    engine_b: {},
    pip_mode: pipMode || 'swing',
    sizing_override: getExecutionSizingOverride(),
  };
  await postQuickExecute(
    payload,
    btn,
    'Executed ' + symbol + ' (' + (pipMode || 'swing').toUpperCase() + ')'
  );
}

export async function quickExecute(
  signalRef: string,
  engineBData?: Record<string, unknown>,
  buttonId?: string,
  pipMode?: string
): Promise<void> {
  const sig = _resolveSignalRef(signalRef);
  if (!sig) return;
  const sid = _signalSid(sig, signalRef);
  const symbol = sig.display || sig.pair || sig.symbol || signalRef;
  const btn = document.getElementById(
    buttonId || 'qexecbtn-' + (pipMode || 'swing') + '-' + sid
  );
  const buildPayload = (window as unknown as { buildLiveSignalPayload?: (sig: SignalLike) => Record<string, unknown> }).buildLiveSignalPayload;
  const payload: Record<string, unknown> = {
    signal: buildPayload ? buildPayload(sig) : sig,
    engine_b: engineBData || {},
    pip_mode: pipMode || 'swing',
    sizing_override: getExecutionSizingOverride(),
  };
  await postQuickExecute(
    payload,
    btn,
    'Quick executed ' + symbol + ' (' + (pipMode || 'swing').toUpperCase() + ')'
  );
}

export async function executeEngineC(sid: string, pipMode?: string): Promise<void> {
  const ecResults = (window as unknown as { _ecResults?: Record<string, ConsensusItem[]> })._ecResults || {};

  // Find matching consensus across all categories
  let found: ConsensusItem | undefined;
  (['aligned', 'a_only', 'b_only'] as const).forEach(function (cat) {
    const items = ecResults[cat];
    if (!items) return;
    items.forEach(function (item: ConsensusItem) {
      const cSid = (item.symbol || item.display || '').replace(/[\/=^.]/g, '_');
      if (cSid === sid) found = item;
    });
  });

  if (!found || !found.trade) {
    const showToast = (window as unknown as { showToast?: (msg: string, err?: boolean) => void }).showToast;
    if (showToast) showToast('Cannot execute — no valid consensus signal', true);
    return;
  }

  const engineB: Record<string, unknown> = found.engine_b_raw || {};
  engineB.recommended_stop_loss = found.sl;
  engineB.recommended_take_profit = found.tp;
  const userSz = getExecutionSizingOverride();
  const co = found.sizing_override;
  const mult = co != null && !isNaN(Number(co)) ? Number(co) : 1;
  const combined = Math.max(0.25, Math.min(1.0, userSz * mult));

  await postQuickExecute(
    {
      signal: {
        pair: found.display,
        display: found.display,
        symbol: found.symbol,
        type: found.type,
        direction: found.direction,
        price: found.entry,
        sl: found.sl,
        tp1: found.tp,
        tp2: found.tp,
        style: pipMode || found.style || 'swing',
        confluenceScore: found.conviction,
        ts: new Date().toISOString(),
      },
      engine_b: engineB,
      pip_mode: pipMode || 'swing',
      sizing_override: combined,
    },
    null,
    'Engine C: ' + found.direction + ' ' + found.display + ' executed (' + (pipMode || 'swing').toUpperCase() + ', ' + (found.tier || '') + ')'
  );
}

// ── Window exposure ──────────────────────────────────────────────────────────

export interface ExecutionAPI {
  postQuickExecute: typeof postQuickExecute;
  executeSignalStyle: typeof executeSignalStyle;
  quickExecute: typeof quickExecute;
  executeEngineC: typeof executeEngineC;
  getExecutionSizingOverride: typeof getExecutionSizingOverride;
}

export function initExecutionFeature(): ExecutionAPI {
  const api: ExecutionAPI = {
    postQuickExecute,
    executeSignalStyle,
    quickExecute,
    executeEngineC,
    getExecutionSizingOverride,
  };
  if (!(window as unknown as { ExecutionFeature?: ExecutionAPI }).ExecutionFeature) {
    (window as unknown as { ExecutionFeature: ExecutionAPI }).ExecutionFeature = api;
  }
  return (window as unknown as { ExecutionFeature: ExecutionAPI }).ExecutionFeature;
}

export default {
  postQuickExecute,
  executeSignalStyle,
  quickExecute,
  executeEngineC,
  getExecutionSizingOverride,
};
