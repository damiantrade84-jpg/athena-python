export interface RenderReadyInput {
  candleCount: number;
  candidateLevelsReady: boolean;
  profileReady: boolean | 'unavailable';
  sourceBadgeReady: boolean;
  captureWidth: number;
  captureHeight: number;
  nativeCanvasReady: boolean;
}

export interface RenderReadyResult {
  ready: boolean;
  missing: string[];
}

export function hasScalpNativeCanvas(captureEl: HTMLElement | null): boolean {
  if (!captureEl) return false;
  return Array.from(captureEl.querySelectorAll('canvas')).some(
    (canvas) => canvas.width > 0 && canvas.height > 0,
  );
}

export function checkScalpChartRenderReady(input: RenderReadyInput): RenderReadyResult {
  const missing: string[] = [];
  if (input.candleCount <= 0) missing.push('candles');
  if (!input.candidateLevelsReady) missing.push('candidateLevels');
  if (input.profileReady === false) missing.push('profile');
  if (!input.sourceBadgeReady) missing.push('sourceBadge');
  if (input.captureWidth <= 0 || input.captureHeight <= 0) missing.push('screenshotDimensions');
  if (!input.nativeCanvasReady) missing.push('nativeCanvas');
  return { ready: missing.length === 0, missing };
}

export function waitForScalpChartRenderReady(
  getInput: () => RenderReadyInput,
  timeoutMs = 8000,
  intervalMs = 50,
): Promise<RenderReadyResult> {
  return new Promise((resolve) => {
    const started = Date.now();
    const tick = () => {
      const result = checkScalpChartRenderReady(getInput());
      if (result.ready || Date.now() - started >= timeoutMs) {
        resolve(result);
        return;
      }
      window.setTimeout(tick, intervalMs);
    };
    tick();
  });
}
