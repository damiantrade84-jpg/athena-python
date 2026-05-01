import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Safely format a possibly-null/undefined/NaN value with toFixed().
 * Backend may emit `null` for fields the math could not resolve (NaN/Inf are
 * scrubbed to None by `_json_safe`), so calling `.toFixed()` directly on those
 * fields blows up the whole React tree. Use this helper at every boundary
 * between the API payload and rendered text.
 */
export function fmtNum(
  value: unknown,
  decimals: number = 2,
  fallback: string = "—",
): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return fallback;
  return n.toFixed(decimals);
}

/** Coerce to finite number or return the fallback (default 0). */
export function toNum(value: unknown, fallback: number = 0): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}
