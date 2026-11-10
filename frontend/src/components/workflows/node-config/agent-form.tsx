"use client"

import { useMemo } from "react"
import { useTranslations } from "next-intl"
import { Textarea } from "@/components/ui/textarea"
import { agentApi } from "@/lib/api"
import { InsertVariableBar, OutputVariableField, ResourceSelect, SectionHeader } from "./fields"
import { resourceOptions } from "./resource-options"
import { useResourceList } from "./use-resource-list"
import type { NodeFormProps } from "./types"

export function AgentForm({ data, setField, otherNodes }: NodeFormProps<"agent">) {
  const t = useTranslations("workflows")
  const agentId = data.agent_id ?? ""
  // Agents this user can use. The builder agents behind the AI-assisted
  // editors are not for a step.
  const agents = useResourceList(agentApi.list)
  const agentChoices = useMemo(
    () => resourceOptions(agents.filter((a) => !a.is_builder || a.id === agentId), agentId),
    [agents, agentId],
  )
  return (
    <div className="space-y-3">
      <ResourceSelect
        label={t("configSelectAgent")}
        emptyLabel={t("configAgentNone")}
        value={agentId}
        options={agentChoices}
        onChange={(v) => setField("agent_id", v)}
        testId="agent-select"
      />
      {!agentId && (
        <p className="text-[10px] text-muted-foreground/60">{t("configAgentNoneHint")}</p>
      )}

      <SectionHeader label={t("configSectionPrompt")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configPromptTemplate")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configPromptHint")}
          value={data.prompt_template ?? ""}
          onChange={(e) => setField("prompt_template", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => {
            const current = data.prompt_template ?? ""
            setField("prompt_template", current + ref)
          }}
        />
      </div>

      <OutputVariableField
        value={data.output_variable}
        onChange={(v) => setField("output_variable", v)}
      />
    </div>
  )
}
