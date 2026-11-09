"use client"

import { useTranslations } from "next-intl"
import type { Node } from "@xyflow/react"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { VariablePicker } from "../variable-picker"

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
