import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind class composition — same convention shadcn/ui uses. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Format a millisecond duration for compact UI display. */
export function fmtMs(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (value < 1) return `${(value * 1000).toFixed(0)}μs`;
  if (value < 1000) return `${value.toFixed(1)}ms`;
  return `${(value / 1000).toFixed(2)}s`;
}

/** Truncate a hash for inline display while keeping the prefix legible. */
export function truncHash(hash: string | null | undefined, head = 10): string {
  if (!hash) return "—";
  if (hash.length <= head + 3) return hash;
  return `${hash.slice(0, head)}…`;
}

/** Format a 0–1 score as a 2-decimal percentage. */
export function fmtScore(value: number): string {
  if (Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

/** Stable JSON for diffing / hashing. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(value, replacer, 2);
}

function replacer(_key: string, value: unknown): unknown {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const sorted: Record<string, unknown> = {};
    for (const k of Object.keys(value as Record<string, unknown>).sort()) {
      sorted[k] = (value as Record<string, unknown>)[k];
    }
    return sorted;
  }
  return value;
}

/** Compute a stable SHA-256 hex digest in the browser. */
export async function sha256Hex(input: string): Promise<string> {
  const enc = new TextEncoder().encode(input);
  const buf = await crypto.subtle.digest("SHA-256", enc);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export function asPercent(numerator: number, denominator: number): string {
  if (!denominator) return "—";
  return `${((numerator / denominator) * 100).toFixed(1)}%`;
}

/** Stable badge variant from a status string. */
export function statusVariant(status: string | undefined): "ok" | "warn" | "bad" | "muted" {
  switch (status) {
    case "ok":
    case "success":
      return "ok";
    case "degraded":
    case "partial":
    case "warn":
      return "warn";
    case "failed":
    case "down":
    case "bad":
      return "bad";
    default:
      return "muted";
  }
}

/** Small helper — copies text to clipboard, returns success bool. */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
