// src/lib/sseStream.ts — SSE client over fetch + ReadableStream.
//
// Phase 6 streaming token UI. EventSource cannot POST, so we parse the
// text/event-stream wire format manually from a fetch ReadableStream.
//
// Wire format emitted by the Python backend (ai_streaming.sse_format):
//   event: <name>
//   data: <json-string>
//
// (a blank line terminates each event). This module is framework-agnostic
// and abortable via AbortController.

const API_BASE = import.meta.env.VITE_API_BASE || '';

export interface SseEvent {
  event: string;
  data: unknown;
}

export interface SseHandlers {
  onStart?: (data: unknown) => void;
  onToken?: (data: { text?: string } & Record<string, unknown>) => void;
  onError?: (data: unknown) => void;
  onDone?: (data: unknown) => void;
  /** Called for every event in addition to the typed handlers above. */
  onEvent?: (event: SseEvent) => void;
}

/**
 * Parse a buffer of SSE text into complete events plus any trailing partial
 * fragment. Pure function — safe to unit test without a network.
 *
 * Events are separated by a blank line (\n\n). Within an event, lines
 * starting with "event:" or "data:" carry the field; everything else is
 * ignored per the SSE spec.
 */
export function parseSseChunk(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  let rest = buffer;
  // Split on the first blank line; keep leftover that has no trailing blank line.
  while (true) {
    const idx = rest.indexOf('\n\n');
    if (idx === -1) break;
    const block = rest.slice(0, idx);
    rest = rest.slice(idx + 2);
    const event = parseEventBlock(block);
    if (event) events.push(event);
  }
  return { events, rest };
}

function parseEventBlock(block: string): SseEvent | null {
  let eventName = 'message';
  const dataLines: string[] = [];
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  const dataStr = dataLines.join('\n');
  let data: unknown = dataStr;
  try {
    data = JSON.parse(dataStr);
  } catch {
    // Keep raw string if not JSON.
  }
  return { event: eventName, data };
}

/**
 * Stream an SSE endpoint that expects a POST body. Returns a promise that
 * resolves when the stream ends (done event or stream close) and rejects on
 * network error or non-2xx response.
 *
 * On HTTP 410 (streaming_disabled), resolves with `{ fallback: true }` so the
 * caller can fall back to the non-streaming endpoint.
 */
export async function streamSse(
  url: string,
  body: Record<string, unknown>,
  handlers: SseHandlers,
  signal?: AbortSignal,
): Promise<{ fallback: boolean }> {
  const fullUrl = url.startsWith('http') ? url : `${API_BASE}${url}`;
  const resp = await fetch(fullUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
    signal,
  });

  if (resp.status === 410) {
    // Streaming disabled on the server — caller should fall back.
    return { fallback: true };
  }
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE request failed: HTTP ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { events, rest } = parseSseChunk(buffer);
      buffer = rest;
      for (const evt of events) {
        handlers.onEvent?.(evt);
        switch (evt.event) {
          case 'start':
            handlers.onStart?.(evt.data);
            break;
          case 'token':
            handlers.onToken?.(evt.data as { text?: string } & Record<string, unknown>);
            break;
          case 'error':
            handlers.onError?.(evt.data);
            break;
          case 'done':
            handlers.onDone?.(evt.data);
            break;
          default:
            // Unknown event — already forwarded via onEvent.
            break;
        }
      }
    }
    // Flush any trailing decoder bytes.
    buffer += decoder.decode();
    if (buffer.trim()) {
      const { events } = parseSseChunk(buffer + '\n\n');
      for (const evt of events) {
        handlers.onEvent?.(evt);
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // ignore
    }
  }
  return { fallback: false };
}
