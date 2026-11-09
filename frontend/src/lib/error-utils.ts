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
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type TranslatorFn = ((key: string, args?: Record<string, any>) => string) & {
  /** next-intl translators report whether a key has a message. */
  has?: (key: string) => boolean
}

/**
 * The message for `code`, or `null` when there is none.
 *
 * next-intl answers a missing key, or a message whose arguments are missing,
 * with the key path (`errors.<code>`), not with the bare code, so both are
 * treated as "no message" here.
 */
export function translateErrorCode(
  code: string,
  tError: TranslatorFn,
  args: Record<string, unknown> = {},
): string | null {
  if (tError.has && !tError.has(code)) return null
  const translated = tError(code, args)
  if (translated === code || translated.endsWith(`.${code}`)) return null
  return translated
}

export function getErrorMessage(
  err: unknown,
  tError: TranslatorFn,
): string {
  if (err instanceof ApiError && err.errorCode) {
    const translated = translateErrorCode(err.errorCode, tError, err.errorArgs)
    if (translated !== null) return translated
  }
  return err instanceof Error ? err.message : tError("_fallback")
}
