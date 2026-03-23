import { formatDistanceToNow, type Locale } from "date-fns"
import { zhCN, enUS, ja, ko, de, fr } from "date-fns/locale"

const DATE_FNS_LOCALES: Record<string, Locale> = { zh: zhCN, en: enUS, ja, ko, de, fr }

function getDateFnsLocale(locale: string): Locale {
  return DATE_FNS_LOCALES[locale] ?? DATE_FNS_LOCALES[locale.split("-")[0]] ?? enUS
}

/**
 * Format a date string as a localized date (e.g., "Mar 23, 2026").
 * Respects the user's timezone preference.
 */
export function formatDate(
  dateStr: string | null | undefined,
  opts?: { locale?: string; timezone?: string; fallback?: string },
): string {
  if (!dateStr) return opts?.fallback ?? ""
  try {
    return new Date(dateStr).toLocaleDateString(opts?.locale, {
      year: "numeric",
      month: "short",
      day: "numeric",
      timeZone: opts?.timezone || undefined,
    })
  } catch {
    return dateStr
  }
}

/**
 * Format a date string as localized date + time (e.g., "Mar 23, 2026, 3:45 PM").
 * Respects the user's timezone preference.
 */
export function formatDateTime(
  dateStr: string | null | undefined,
  opts?: { locale?: string; timezone?: string; fallback?: string; seconds?: boolean },
): string {
  if (!dateStr) return opts?.fallback ?? ""
  try {
    return new Date(dateStr).toLocaleString(opts?.locale, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      ...(opts?.seconds ? { second: "2-digit" } : {}),
      timeZone: opts?.timezone || undefined,
    })
  } catch {
    return dateStr
  }
}

/**
 * Format a date string as relative time (e.g., "3 minutes ago").
 * Timezone-independent — only depends on locale for language.
 */
export function formatRelativeTime(
  dateStr: string | null | undefined,
  opts?: { locale?: string; fallback?: string },
): string {
  if (!dateStr) return opts?.fallback ?? ""
  try {
    return formatDistanceToNow(new Date(dateStr), {
      addSuffix: true,
      locale: getDateFnsLocale(opts?.locale ?? "en"),
    })
  } catch {
    return dateStr
  }
}

/**
 * Format a date for chart/stats labels (short month + day).
 * Respects the user's timezone preference.
 */
export function formatDateLabel(
  dateStr: string | null | undefined,
  opts?: { locale?: string; timezone?: string },
): string {
  if (!dateStr) return ""
  try {
    return new Date(dateStr).toLocaleDateString(opts?.locale, {
      month: "short",
      day: "numeric",
      timeZone: opts?.timezone || undefined,
    })
  } catch {
    return dateStr ?? ""
  }
}
