"use client"

import { useTranslations } from "next-intl"
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import type { BranchCondition } from "@/types/workflow"
import { SectionHeader } from "./fields"
import type { NodeFormProps } from "./types"

export function ConditionBranchForm({ data, setField }: NodeFormProps<"conditionBranch">) {
  const t = useTranslations("workflows")
  const mode = data.mode ?? "expression"
  const conditions = data.conditions ?? []

  const addCondition = () => {
    const newId = `cond_${Date.now()}`
    setField("conditions", [...conditions, { id: newId, label: "", variable: "", operator: "==", value: "" }])
  }

  const removeCondition = (idx: number) => {
    setField("conditions", conditions.filter((_, i) => i !== idx))
  }

  const updateCondition = (idx: number, field: keyof BranchCondition, value: unknown) => {
    const updated = conditions.map((c, i) => (i === idx ? { ...c, [field]: value } : c))
    setField("conditions", updated)
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configMode")}</label>
        <Select value={mode} onValueChange={(v) => setField("mode", v as "expression" | "llm")}>
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="expression">{t("configModeExpression")}</SelectItem>
            <SelectItem value="llm">{t("configModeLLM")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <SectionHeader label={t("configConditions")} />
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configConditions")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addCondition}>
          <Plus className="h-3 w-3" />
          {t("configAddCondition")}
        </Button>
      </div>
      {conditions.map((c, i) => (
        <div key={c.id ?? i} className="space-y-2 rounded-md border border-border p-2">
          <div className="flex items-center gap-2">
            <Input
              className="h-7 text-xs flex-1"
              placeholder={t("configConditionLabel")}
              value={c.label}
              onChange={(e) => updateCondition(i, "label", e.target.value)}
            />
            <Button variant="ghost" size="icon-sm" onClick={() => removeCondition(i)}>
              <Trash2 className="h-3 w-3 text-destructive" />
            </Button>
          </div>
          {mode === "expression" ? (
            <div className="space-y-1.5">
              {/* Variable selector */}
              <Input
                className="h-7 text-xs"
                placeholder={t("configConditionVariable")}
                value={c.variable ?? ""}
                onChange={(e) => updateCondition(i, "variable", e.target.value)}
              />
              {/* Operator selector */}
              <Select
                value={c.operator ?? "=="}
                onValueChange={(v) => updateCondition(i, "operator", v)}
              >
                <SelectTrigger className="w-full h-7 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="==">== (equals)</SelectItem>
                  <SelectItem value="!=">!= (not equals)</SelectItem>
                  <SelectItem value=">">&gt; (greater than)</SelectItem>
                  <SelectItem value="<">&lt; (less than)</SelectItem>
                  <SelectItem value="contains">contains</SelectItem>
                  <SelectItem value="not_contains">not contains</SelectItem>
                  <SelectItem value="is_empty">is empty</SelectItem>
                  <SelectItem value="is_not_empty">is not empty</SelectItem>
                </SelectContent>
              </Select>
              {/* Value input (hidden for is_empty/is_not_empty) */}
              {c.operator !== "is_empty" && c.operator !== "is_not_empty" && (
                <Input
                  className="h-7 text-xs"
                  placeholder={t("configConditionValue")}
                  value={(c.value ?? "") as string}
                  onChange={(e) => updateCondition(i, "value", e.target.value)}
                />
              )}
            </div>
          ) : (
            <Textarea
              className="text-xs resize-none"
              rows={2}
              placeholder={t("configLLMPrompt")}
              value={c.llm_prompt ?? ""}
              onChange={(e) => updateCondition(i, "llm_prompt", e.target.value)}
            />
          )}
        </div>
      ))}

      {/* Default (else) branch */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configDefaultBranchLabel")}</label>
        <Input
          className="h-7 text-xs"
          placeholder={t("configDefaultBranchPlaceholder")}
          value={data.default_branch_label ?? ""}
          onChange={(e) => setField("default_branch_label", e.target.value)}
        />
      </div>
    </div>
  )
}
