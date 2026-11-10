"use client"

import { useMemo } from "react"
import { useTranslations } from "next-intl"
import { Input } from "@/components/ui/input"
import { mcpServerApi } from "@/lib/api"
import { ResourceSelect } from "./fields"
import { resourceOptions } from "./resource-options"
import type { NodeFormProps } from "./types"
import { useResourceList } from "./use-resource-list"

export function MCPForm({ data, setField, setFields }: NodeFormProps<"mcp">) {
  const t = useTranslations("workflows")
  const serverId = data.server_id ?? ""
  // MCP servers this user can use
  const servers = useResourceList(mcpServerApi.list)
  const serverChoices = useMemo(() => resourceOptions(servers, serverId), [servers, serverId])
  return (
    <div className="space-y-3">
      <ResourceSelect
        label={t("configServerId")}
        emptyLabel={t("configServerPlaceholder")}
        value={serverId}
        options={serverChoices}
        // A tool belongs to one server: a new server clears it.
        onChange={(v) => setFields({ server_id: v, tool_name: "", parameters: {} })}
        testId="mcp-server-select"
      />

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
