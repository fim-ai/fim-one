"use client"

import { useMemo } from "react"
import { useTranslations } from "next-intl"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useAuth } from "@/contexts/auth-context"
import { kbApi } from "@/lib/api"
import { InsertVariableBar, OutputVariableField, ResourceSelect, SectionHeader } from "./fields"
import { resourceOptions } from "./resource-options"
import { useResourceList } from "./use-resource-list"
import type { NodeFormProps } from "./types"

export function KnowledgeRetrievalForm({ data, setField, otherNodes }: NodeFormProps<"knowledgeRetrieval">) {
  const t = useTranslations("workflows")
  const { user } = useAuth()
  // The step reads one knowledge base; the field is a list in the schema.
  const kbId = (data.kb_ids ?? [])[0] ?? ""
  // A knowledge base cannot be shared: a step reads only its author's own.
  const knowledgeBases = useResourceList(kbApi.list)
  const kbChoices = useMemo(
    () =>
      resourceOptions(
        knowledgeBases.filter((kb) => kb.user_id === user?.id || kb.id === kbId),
        kbId,
      ),
    [knowledgeBases, kbId, user?.id],
  )
  return (
    <div className="space-y-3">
      <ResourceSelect
        label={t("configSelectKB")}
        emptyLabel={t("configKBPlaceholder")}
        value={kbId}
        options={kbChoices}
        onChange={(v) => setField("kb_ids", v ? [v] : [])}
        testId="kb-select"
      />

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
