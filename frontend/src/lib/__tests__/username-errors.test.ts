import { describe, expect, it } from "vitest"

import { ApiError } from "@/lib/api"
import type { TranslatorFn } from "@/lib/error-utils"
import { usernameSaveError } from "@/lib/username-errors"

import errors from "../../../messages/en/errors.json"
import settings from "../../../messages/en/settings.json"

// next-intl stand-ins over the real English catalogues.
function translator(messages: Record<string, unknown>): TranslatorFn {
  const fn = ((key: string, args: Record<string, unknown> = {}) => {
    const value = key.split(".").reduce<unknown>(
      (node, part) => (node as Record<string, unknown> | undefined)?.[part],
      messages,
    )
    if (typeof value !== "string") return key
    return value.replace(/\{(\w+)\}/g, (_, name: string) => String(args[name] ?? `{${name}}`))
  }) as TranslatorFn
  fn.has = (key: string) => fn(key) !== key
  return fn
}

const t = translator((settings as Record<string, unknown>).general as Record<string, unknown>)
const tError = translator(errors as Record<string, unknown>)

// What apiFetch throws for the backend's AppError: detail is the code
// with spaces ("Username reserved"); the code itself is in errorCode.
function appError(status: number, code: string, args: Record<string, unknown> = {}): ApiError {
  return new ApiError(status, code.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()), code, args)
}

describe("usernameSaveError", () => {
  it("shows the backend's reason for a reserved username next to the field", () => {
    const result = usernameSaveError(appError(400, "username_reserved"), t, tError)
    expect(result).toEqual({ field: (errors as Record<string, string>).username_reserved })
  })

  it("maps username_taken by its code, not by the message text", () => {
    expect(usernameSaveError(appError(409, "username_taken"), t, tError)).toEqual({
      field: t("usernameTaken"),
    })
  })

  it("maps username_cooldown with the remaining days", () => {
    const err = new ApiError(400, "Username can only be changed once every 7 days.", "username_cooldown", { days: 3 })
    expect(usernameSaveError(err, t, tError)).toEqual({ field: t("usernameCooldown", { days: "3" }) })
  })

  it("shows an empty-name refusal next to the field", () => {
    expect(usernameSaveError(appError(400, "username_empty"), t, tError)).toEqual({
      field: (errors as Record<string, string>).username_empty,
    })
  })

  it("toasts the translated reason for other errors, not a generic failure", () => {
    expect(usernameSaveError(appError(400, "validation_error"), t, tError)).toEqual({
      toast: tError("validation_error"),
    })
  })
})
