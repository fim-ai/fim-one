import { getRequestConfig } from "next-intl/server"
import { cookies } from "next/headers"
import fs from "fs"
import path from "path"

const SUPPORTED_LOCALES = ["en", "zh"] as const
type Locale = (typeof SUPPORTED_LOCALES)[number]
const DEFAULT_LOCALE: Locale = "en"

function isSupported(v: string): v is Locale {
  return (SUPPORTED_LOCALES as readonly string[]).includes(v)
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

  // "auto" or missing → fall back to default
  const locale: Locale = isSupported(raw) ? raw : DEFAULT_LOCALE

  return {
    locale,
    messages: loadMessages(locale),
  }
})
