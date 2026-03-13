import { getRequestConfig } from "next-intl/server"
import { cookies, headers } from "next/headers"
import fs from "fs"
import path from "path"

const SUPPORTED_LOCALES = ["en", "zh", "ja", "ko", "de", "fr"] as const
type Locale = (typeof SUPPORTED_LOCALES)[number]
const DEFAULT_LOCALE: Locale = "en"

function isSupported(v: string): v is Locale {
  return (SUPPORTED_LOCALES as readonly string[]).includes(v)
}

/**
 * Parse Accept-Language header and return the best matching supported locale.
 * e.g. "zh-CN,zh;q=0.9,en;q=0.8" → "zh"
 */
function detectFromAcceptLanguage(header: string): Locale | null {
  const entries = header.split(",").map((part) => {
    const [tag, qPart] = part.trim().split(";")
    const q = qPart ? parseFloat(qPart.replace(/q=/, "")) : 1
    return { tag: tag.trim().toLowerCase(), q }
  })
  entries.sort((a, b) => b.q - a.q)

  for (const { tag } of entries) {
    // Exact match: "en", "zh", "ja"
    if (isSupported(tag)) return tag
    // Prefix match: "zh-cn" → "zh", "en-us" → "en"
    const prefix = tag.split("-")[0]
    if (isSupported(prefix)) return prefix
  }
  return null
}

/**
 * Auto-discover all namespace JSON files under messages/{locale}/.
 * Workers just drop a .json file — no central registry to update.
 */
function loadMessages(locale: Locale): Record<string, Record<string, string>> {
  const dir = path.join(process.cwd(), "messages", locale)
  if (!fs.existsSync(dir)) return {}

  const messages: Record<string, Record<string, string>> = {}
  for (const file of fs.readdirSync(dir)) {
    if (!file.endsWith(".json")) continue
    const ns = file.replace(/\.json$/, "")
    const content = fs.readFileSync(path.join(dir, file), "utf-8")
    messages[ns] = JSON.parse(content)
  }
  return messages
}

export default getRequestConfig(async () => {
  const cookieStore = await cookies()
  const raw = cookieStore.get("NEXT_LOCALE")?.value ?? ""

  let locale: Locale
  if (isSupported(raw)) {
    // User explicitly chose a locale
    locale = raw
  } else {
    // Auto: detect from browser Accept-Language header
    const headerStore = await headers()
    const acceptLang = headerStore.get("accept-language") ?? ""
    locale = detectFromAcceptLanguage(acceptLang) ?? DEFAULT_LOCALE
  }

  return {
    locale,
    messages: loadMessages(locale),
  }
})
