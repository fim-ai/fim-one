"use client"

import { useLocale } from "next-intl"
import { useAuth } from "@/contexts/auth-context"
import {
  formatDate as _fmtDate,
  formatDateTime as _fmtDateTime,
  formatRelativeTime as _fmtRelative,
  formatDateLabel as _fmtLabel,
} from "@/lib/date-utils"

/** Browser's IANA timezone, computed once. */
const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone

/**
 * Returns timezone-aware date formatting helpers.
 * Uses the user's saved timezone preference, falling back to browser timezone.
 */
export function useDateFormatter() {
  const { user } = useAuth()
  const locale = useLocale()
  const timezone = user?.timezone || BROWSER_TZ

  return {
    /** e.g. "Mar 23, 2026" */
    formatDate: (dateStr: string | null | undefined, fallback?: string) =>
      _fmtDate(dateStr, { locale, timezone, fallback }),

    /** e.g. "Mar 23, 2026, 3:45 PM" */
    formatDateTime: (dateStr: string | null | undefined, fallback?: string) =>
      _fmtDateTime(dateStr, { locale, timezone, fallback }),

    /** e.g. "Mar 23, 2026, 3:45:12 PM" (with seconds) */
    formatDateTimeFull: (dateStr: string | null | undefined, fallback?: string) =>
      _fmtDateTime(dateStr, { locale, timezone, fallback, seconds: true }),

    /** e.g. "3 minutes ago" */
    formatRelativeTime: (dateStr: string | null | undefined, fallback?: string) =>
      _fmtRelative(dateStr, { locale, fallback }),

    /** e.g. "Mar 23" — for chart labels */
    formatDateLabel: (dateStr: string | null | undefined) =>
      _fmtLabel(dateStr, { locale, timezone }),

    /** The resolved timezone string */
    timezone,
  }
}
