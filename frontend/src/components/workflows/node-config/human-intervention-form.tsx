"use client"

import { useTranslations } from "next-intl"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import type { NodeFormProps } from "./types"

export function HumanInterventionForm({ data, setField }: NodeFormProps<"humanIntervention">) {
  const t = useTranslations("workflows")
  return (
    <div className="space-y-3">
      {/* Prompt message */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configPromptMessage")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configPromptMessagePlaceholder")}
          value={data.prompt_message ?? ""}
          onChange={(e) => setField("prompt_message", e.target.value)}
        />
      </div>

      {/* Assignee */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configAssignee")}</label>
        <Input
          className="h-7 text-xs"
          placeholder={t("configAssigneePlaceholder")}
          value={data.assignee ?? ""}
          onChange={(e) => setField("assignee", e.target.value)}
        />
      </div>

      {/* Timeout (hours) */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configTimeoutHours")}</label>
        <Input
          className="h-7 text-xs"
          type="number"
          min={1}
          value={data.timeout_hours ?? 24}
          onChange={(e) => setField("timeout_hours", Number(e.target.value) || 24)}
        />
      </div>

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={data.output_variable ?? "approval_result"}
          onChange={(e) => setField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}
