// Core API client with compatibility globals.
// Mirrors the original window.apiClient interface for legacy support.

import { safeJson } from './safeJson';
import type {
  AiLeeConfirmationRequest,
  AiLeeConfirmationResponse,
  AiStrategistBriefResponse,
  AiTradeChatRequest,
  AiTradeChatResponse,
  CompareResponse,
  EngineASignal,
  PairScanResponse,
} from '@/types/athena';

const API_BASE = import.meta.env.VITE_API_BASE || '';

interface ApiResponse<T = unknown> {
  data?: T;
  error?: string;
  reason?: string;
  [key: string]: unknown;
}

async function requestJson(url: string, options?: RequestInit): Promise<unknown> {
  const fullUrl = url.startsWith('http') ? url : `${API_BASE}${url}`;
  const resp = await fetch(fullUrl, options || {});
  const data = (await safeJson(resp)) as ApiResponse;

  if (!resp.ok) {
    const msg = (data && (data.error || data.reason)) || `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

export const apiClient = {
  getJson(url: string) {
    return requestJson(url, { method: 'GET' });
  },
  postJson(url: string, payload?: Record<string, unknown>) {
    return requestJson(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
  },
  // Typed convenience methods for React code
  // NOTE: Flask backend returns flat JSON (NOT { data: T } wrapper).
  // These methods pass through the raw response directly.
  async get<T>(url: string): Promise<T> {
    return requestJson(url, { method: 'GET' }) as Promise<T>;
  },
  async post<T>(url: string, payload?: Record<string, unknown>): Promise<T> {
    return requestJson(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    }) as Promise<T>;
  },
};

// Compatibility: expose on window for legacy code.
declare global {
  interface Window {
    apiClient: typeof apiClient;
  }
}

export function initApiClient() {
  if (!window.apiClient) {
    window.apiClient = apiClient;
  }
  return window.apiClient;
}

export function postAiTradeChat(payload: AiTradeChatRequest): Promise<AiTradeChatResponse> {
  return apiClient.post<AiTradeChatResponse>(
    '/api/ai/trade-chat',
    payload as unknown as Record<string, unknown>,
  );
}

export function postAiLeeConfirmation(
  payload: AiLeeConfirmationRequest,
  options?: { signal?: AbortSignal },
): Promise<AiLeeConfirmationResponse> {
  return requestJson('/api/ai/lee-confirmation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
    signal: options?.signal,
  }) as Promise<AiLeeConfirmationResponse>;
}

export function postPairScan(symbol: string, style = 'auto'): Promise<PairScanResponse> {
  return apiClient.post<PairScanResponse>('/api/pair-scan', { symbol, style });
}

export function postCompareEngines(signal: EngineASignal, style = 'auto'): Promise<CompareResponse> {
  return apiClient.post<CompareResponse>('/api/compare-engines', { signal, style });
}

export function getAiStrategistBrief(assetScope = 'all'): Promise<AiStrategistBriefResponse> {
  const params = new URLSearchParams({ asset_scope: assetScope || 'all' });
  return apiClient.get<AiStrategistBriefResponse>(`/api/ai/strategist/brief?${params.toString()}`);
}

export default apiClient;
