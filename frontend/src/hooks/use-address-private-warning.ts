import { useCallback } from "react"
import { useLocale, useTranslations } from "next-intl"

import { knownAddressReasons } from "@/lib/mcp-server-notice"

/**
 * The owner's warning for a shared MCP server whose address may carry a
 * credential, naming the parts the backend found (`warning_reasons`).
 */
export function useAddressPrivateWarning(): (reasons?: readonly string[] | null) => string {
  const t = useTranslations("tools")
  const locale = useLocale()
  return useCallback(
    (reasons) => {
      const labels = knownAddressReasons(reasons).map((reason) => t(`addressReasons.${reason}`))
      const parts = labels.length
        ? new Intl.ListFormat(locale, { style: "long", type: "disjunction" }).format(labels)
        : t("addressPartsUnknown")
      return t("addressPrivateWarning", { parts })
    },
    [t, locale],
  )
}
