// Core utility helper: safe JSON response parser.
// Extracted as standalone module for modular migration compatibility.

export async function safeJson(resp: Response | null): Promise<unknown> {
  if (!resp) return {};
  try {
    return await resp.json();
  } catch {
    return {};
  }
}

// Compatibility: expose on window for legacy code.
declare global {
  interface Window {
    safeJson: typeof safeJson;
  }
}

export function initCoreUtils() {
  if (!window.safeJson) {
    window.safeJson = safeJson;
  }
  return window.safeJson;
}

export default safeJson;
