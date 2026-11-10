import { formatDistanceToNow, type Locale } from "date-fns"
import { zhCN, enUS, ja, ko, de, fr } from "date-fns/locale"

const DATE_FNS_LOCALES: Record<string, Locale> = { zh: zhCN, en: enUS, ja, ko, de, fr }

function getDateFnsLocale(locale: string): Locale {
  return DATE_FNS_LOCALES[locale] ?? DATE_FNS_LOCALES[locale.split("-")[0]] ?? enUS
}

/** A date and a time of day with no zone: "2026-03-23T07:45:12.123456", or with a space for the T. */
const ZONELESS_DATE_TIME = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/

/**
 * Parse a timestamp the API sent.
 *
 * The backend stores UTC. On SQLite the zone is lost on the way out, and the
 * API sends "2026-03-23T07:45:12" where PostgreSQL sends
 * "2026-03-23T07:45:12+00:00". `new Date` reads a zone-less date-time as
 * local time, which is off by the viewer's UTC offset ("about 8 hours ago"
 * for something made just now, in UTC+8). A zone-less date-time is UTC here;
 * a string that names its zone, and a date without a time, are read as is.
 */
export function parseApiDate(dateStr: string): Date {
  const zoneless = ZONELESS_DATE_TIME.exec(dateStr.trim())
  return new Date(zoneless ? `${zoneless[1]}T${zoneless[2]}Z` : dateStr)
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
    return parseApiDate(dateStr).toLocaleDateString(opts?.locale, {
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
    return parseApiDate(dateStr).toLocaleString(opts?.locale, {
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
    return formatDistanceToNow(parseApiDate(dateStr), {
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
    return parseApiDate(dateStr).toLocaleDateString(opts?.locale, {
      month: "short",
      day: "numeric",
      timeZone: opts?.timezone || undefined,
    })
  } catch {
    return dateStr ?? ""
  }
}
