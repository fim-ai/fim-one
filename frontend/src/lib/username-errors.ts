import { ApiError } from "./api"
import { getErrorMessage, type TranslatorFn } from "./error-utils"

/**
 * Where to show why saving a username failed, and what to say.
 *
 * Read by the backend's error code: the message of an AppError is the code
 * written as words ("Username reserved"), so matching the code in the message
 * text never fires. Refusals about the name itself go next to the field; any
 * other error is a toast with its translated reason.
 *
 * `t` is the `settings.general` translator, `tError` the `errors` one.
 */
export function usernameSaveError(
  err: unknown,
  t: TranslatorFn,
  tError: TranslatorFn,
): { field: string } | { toast: string } {
  const code = err instanceof ApiError ? err.errorCode : null
  if (code === "username_taken") return { field: t("usernameTaken") }
  if (code === "username_reserved" || code === "username_empty") {
    return { field: getErrorMessage(err, tError) }
  }
  if (code === "username_cooldown") {
    const days = (err as ApiError).errorArgs?.days ?? 7
    return { field: t("usernameCooldown", { days: String(days) }) }
  }
  return { toast: getErrorMessage(err, tError) }
}
