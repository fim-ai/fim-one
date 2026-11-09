"use client"

import { useTranslations } from "next-intl"
import { Input } from "@/components/ui/input"
import { Slider } from "@/components/ui/slider"
import { Textarea } from "@/components/ui/textarea"
import { InsertVariableBar, OutputVariableField, SectionHeader } from "./fields"
import type { NodeFormProps } from "./types"

export function LLMForm({ data, setField, otherNodes }: NodeFormProps<"llm">) {
  const t = useTranslations("workflows")
  return (
    <div className="space-y-3">
      {/* Model section — the model is fully system-managed: it resolves to the
          configured Model Provider (else .env), exactly like the playground.
          The node exposes no model knob on purpose. */}
      <SectionHeader label={t("configSectionModel")} />
      <p className="text-[10px] text-muted-foreground/60">
        {t("configModelManagedHint")}
      </p>

      {/* Prompt section */}
      <SectionHeader label={t("configSectionPrompt")} />

      {/* System Prompt */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSystemPrompt")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configSystemPromptHint")}
          value={data.system_prompt ?? ""}
          onChange={(e) => setField("system_prompt", e.target.value)}
        />
      </div>

      {/* User Prompt template */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium">{t("configPromptTemplate")}</label>
        </div>
        <Textarea
          className="text-xs resize-none"
          rows={5}
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

      {/* Parameters section */}
      <SectionHeader label={t("configSectionParameters")} />
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium">{t("configTemperature")}</label>
          <span className="text-[10px] font-mono text-muted-foreground tabular-nums">
            {(data.temperature ?? 0.7).toFixed(1)}
          </span>
        </div>
        <Slider
          value={[data.temperature ?? 0.7]}
          onValueChange={([v]) => setField("temperature", v)}
          min={0}
          max={2}
          step={0.1}
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configMaxTokens")}</label>
        <Input
          className="h-7 text-xs"
          type="number"
          placeholder="4096"
          value={data.max_tokens ?? ""}
          onChange={(e) => setField("max_tokens", e.target.value ? Number(e.target.value) : undefined)}
        />
      </div>

      {/* Output section */}
      <OutputVariableField
        value={data.output_variable}
        onChange={(v) => setField("output_variable", v)}
      />
    </div>
  )
}
