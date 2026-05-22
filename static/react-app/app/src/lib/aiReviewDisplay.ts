/** ASCII-safe display helpers for AI review panels (avoids UTF-8 mojibake on some clients). */

export const AI_REVIEW_EMPTY = 'n/a';
export const AI_REVIEW_SEP = ' | ';

export function showReviewValue(value: unknown, fallback = AI_REVIEW_EMPTY): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'string') return value.trim() === '' ? fallback : value;
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : fallback;
  return String(value);
}

export function fmtReviewNum(
  value: number | null | undefined,
  digits = 0,
  fallback = AI_REVIEW_EMPTY,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  return digits > 0 ? value.toFixed(digits) : String(Math.round(value));
}

export function fmtReviewPass(passed: boolean | null | undefined): string {
  if (passed === true) return 'PASS';
  if (passed === false) return 'FAIL';
  return AI_REVIEW_EMPTY;
}

export function fmtReviewBool(value: boolean | null | undefined): string {
  if (value === true) return 'yes';
  if (value === false) return 'no';
  return AI_REVIEW_EMPTY;
}
