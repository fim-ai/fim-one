import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Format seconds into a human-friendly duration string. */
export function fmtDuration(s: number): string {
  if (s < 0.1) return "< 0.1s"
  if (s < 10) return `${s.toFixed(1)}s`
  return `${Math.round(s)}s`
}
