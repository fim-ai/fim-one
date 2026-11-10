import { useCallback } from "react"
import { useLocale, useTranslations } from "next-intl"

import { knownAddressReasons } from "@/lib/mcp-server-notice"
import type { MCPServerResponse } from "@/types/mcp-server"

/**
 * The owner's warning for a shared MCP server whose address may carry a
 * credential, naming the parts the backend found (`warning_reasons`). A
 * personal server gets it when a shared agent or workflow of the owner's
 * uses it, and is worded for that.
 */
export function useAddressPrivateWarning(): (
  server: Pick<MCPServerResponse, "warning_reasons" | "visibility">,
) => string {
  const t = useTranslations("tools")
  const locale = useLocale()
  return useCallback(
    (server) => {
      const labels = knownAddressReasons(server.warning_reasons).map((reason) => t(`addressReasons.${reason}`))
      const parts = labels.length
        ? new Intl.ListFormat(locale, { style: "long", type: "disjunction" }).format(labels)
        : t("addressPartsUnknown")
      // Warned although not shared: a shared agent or workflow uses it.
      const shared = !!server.visibility && server.visibility !== "personal"
      return t(shared ? "addressPrivateWarning" : "addressPrivateWarningBound", { parts })
    },
    [t, locale],
  )
}
