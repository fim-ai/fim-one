"use client"

import { useTranslations } from "next-intl"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { InsertVariableBar, OutputVariableField, SectionHeader } from "./fields"
import type { NodeFormProps } from "./types"

export function AgentForm({ data, setField, otherNodes }: NodeFormProps<"agent">) {
  const t = useTranslations("workflows")
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSelectAgent")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="Agent ID"
          value={data.agent_id ?? ""}
          onChange={(e) => setField("agent_id", e.target.value)}
        />
      </div>

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
