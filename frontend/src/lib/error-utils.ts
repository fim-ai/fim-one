import { ApiError } from "./api"

/**
 * Extract a user-friendly, translated error message from an error object.
 *
 * When the backend returns a structured `error_code`, this function looks up
 * the corresponding translation via the supplied `next-intl` translator (`tError`).
 * Falls back to the raw `err.message` if no translation is found.
 *
 * Usage:
 *   const tError = useTranslations("errors")
 *   toast.error(getErrorMessage(err, tError))
 */
export function getErrorMessage(
  err: unknown,
  tError: (key: string, args?: Record<string, unknown>) => string,
): string {
  if (err instanceof ApiError && err.errorCode) {
    const translated = tError(err.errorCode, err.errorArgs)
    // next-intl returns the key itself when no translation is found
    if (translated !== err.errorCode) return translated
  }
  return err instanceof Error ? err.message : tError("_fallback")
}
