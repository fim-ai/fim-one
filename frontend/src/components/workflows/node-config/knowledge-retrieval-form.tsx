"use client"

import { useTranslations } from "next-intl"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { InsertVariableBar, OutputVariableField, SectionHeader } from "./fields"
import type { NodeFormProps } from "./types"

export function KnowledgeRetrievalForm({ data, setField, otherNodes }: NodeFormProps<"knowledgeRetrieval">) {
  const t = useTranslations("workflows")
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSelectKB")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="KB ID"
          value={(data.kb_ids ?? [])[0] ?? ""}
          onChange={(e) =>
            setField("kb_ids", e.target.value ? [e.target.value] : [])
          }
        />
      </div>

      <SectionHeader label={t("configSectionPrompt")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configQueryTemplate")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configPromptHint")}
          value={data.query_template ?? ""}
          onChange={(e) => setField("query_template", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => {
            const current = data.query_template ?? ""
            setField("query_template", current + ref)
          }}
        />
      </div>

      <SectionHeader label={t("configSectionParameters")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configTopK")}</label>
        <Input
          className="h-7 text-xs"
          type="number"
          placeholder="5"
          value={data.top_k ?? ""}
          onChange={(e) => setField("top_k", e.target.value ? Number(e.target.value) : undefined)}
        />
      </div>

      <OutputVariableField
        value={data.output_variable}
        onChange={(v) => setField("output_variable", v)}
      />
    </div>
  )
}
