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

/** Check whether a file represents an image based on mime_type or filename extension. */
export function isImageFile(file: { filename: string; mime_type?: string | null }): boolean {
  if (file.mime_type?.startsWith("image/")) return true
  const ext = file.filename.split(".").pop()?.toLowerCase() ?? ""
  return ["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(ext)
}

/** Format token count into a human-friendly string (e.g. 1.23M, 3.4K). */
export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "0"
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toString()
}

/** Format bytes into a human-friendly file size string. */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B"
  const units = ["B", "KB", "MB", "GB"]
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const size = bytes / Math.pow(1024, i)
  return `${size < 10 ? size.toFixed(1) : Math.round(size)} ${units[i]}`
}
