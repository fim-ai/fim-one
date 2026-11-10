"use client"

import { useTranslations } from "next-intl"
import type { Node } from "@xyflow/react"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { VariablePicker } from "../variable-picker"
import type { SelectOption } from "./resource-options"

/** Reusable section header */
export function SectionHeader({ label }: { label: string }) {
  return (
    <>
      <Separator className="my-1" />
      <p className="text-[10px] font-semibold text-muted-foreground/70 uppercase tracking-wider">
        {label}
      </p>
    </>
  )
}

/** Reusable output variable field */
export function OutputVariableField({
  value,
  onChange,
}: {
  value: string | null | undefined
  onChange: (value: string) => void
}) {
  const t = useTranslations("workflows")
  return (
    <>
      <SectionHeader label={t("configSectionOutput")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="result"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
        <p className="text-[10px] text-muted-foreground/60">
          {t("configVariableRefHint")}
        </p>
      </div>
    </>
  )
}

/** Variable insert bar with picker and hint text */
export function InsertVariableBar({
  otherNodes,
  onInsert,
}: {
  otherNodes: Node[]
  onInsert: (reference: string) => void
}) {
  const t = useTranslations("workflows")
  return (
    <div className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
      <VariablePicker sourceNodes={otherNodes} onInsert={onInsert} />
      <span>{t("configVariableRefHint")}</span>
    </div>
  )
}

/**
 * A dropdown over the resources a step can use. `emptyLabel` is the entry
 * that clears the field, and what the field shows while nothing is chosen.
 */
export function ResourceSelect({
  label,
  emptyLabel,
  value,
  options,
  onChange,
  testId,
}: {
  label: string
  emptyLabel: string
  value: string
  options: SelectOption[]
  onChange: (value: string) => void
  testId?: string
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium">{label}</label>
      <Select
        value={value || "__default__"}
        onValueChange={(v) => {
          const next = v === "__default__" ? "" : v
          if (next !== value) onChange(next)
        }}
      >
        <SelectTrigger className="w-full h-7 text-xs" data-testid={testId}>
          <SelectValue placeholder={emptyLabel} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__default__">{emptyLabel}</SelectItem>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
