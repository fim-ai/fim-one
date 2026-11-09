"use client"

import { useTranslations } from "next-intl"
import { Input } from "@/components/ui/input"
import type { NodeFormProps } from "./types"

export function MCPForm({ data, setField }: NodeFormProps<"mcp">) {
  const t = useTranslations("workflows")
  return (
    <div className="space-y-3">
      {/* Server ID */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configServerId")}</label>
        <Input
          className="h-7 text-xs"
          placeholder={t("configServerIdPlaceholder")}
          value={data.server_id ?? ""}
          onChange={(e) => setField("server_id", e.target.value)}
        />
      </div>

      {/* Tool Name */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configToolName")}</label>
        <Input
          className="h-7 text-xs"
          placeholder={t("configToolNamePlaceholder")}
          value={data.tool_name ?? ""}
          onChange={(e) => setField("tool_name", e.target.value)}
        />
      </div>

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={data.output_variable ?? "mcp_result"}
          onChange={(e) => setField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}
