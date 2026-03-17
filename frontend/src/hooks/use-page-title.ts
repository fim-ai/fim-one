import { useEffect } from "react"
import { APP_NAME } from "@/lib/constants"

/**
 * Sets the browser tab title to "{pageTitle} — {APP_NAME}".
 * Falls back to just APP_NAME when no title is provided.
 */
export function usePageTitle(title: string | undefined) {
  useEffect(() => {
    document.title = title ? `${title} — ${APP_NAME}` : APP_NAME
  }, [title])
}
