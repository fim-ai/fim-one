import { describe, expect, it } from "vitest"

import en from "../../../messages/en/errors.json"
import zh from "../../../messages/zh/errors.json"
import ja from "../../../messages/ja/errors.json"
import ko from "../../../messages/ko/errors.json"
import de from "../../../messages/de/errors.json"
import fr from "../../../messages/fr/errors.json"

const LOCALES: Record<string, Record<string, string>> = { en, zh, ja, ko, de, fr }

describe("error messages use the arguments the server sends", () => {
  it.each(Object.keys(LOCALES))("%s: a rate limit names the seconds to wait", (locale) => {
    expect(LOCALES[locale].verification_rate_limited).toContain("{seconds}")
  })

  it.each(Object.keys(LOCALES))("%s: a refused database target is a sentence with its reason", (locale) => {
    const message = LOCALES[locale].db_target_not_allowed
    expect(message).toContain("{reason}")
    expect(message.replace("{reason}", "").trim().length).toBeGreaterThan(5)
  })
})
