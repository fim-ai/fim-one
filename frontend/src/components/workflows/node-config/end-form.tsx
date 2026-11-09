"use client"

import { useTranslations } from "next-intl"
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { VariablePicker } from "../variable-picker"
import type { NodeFormProps } from "./types"

export function EndForm({ data, setField, otherNodes }: NodeFormProps<"end">) {
  const t = useTranslations("workflows")
  const mapping = data.output_mapping ?? {}
  const entries = Object.entries(mapping)

  const addMapping = () => {
    setField("output_mapping", { ...mapping, "": "" })
  }

  const removeMapping = (key: string) => {
    const updated = { ...mapping }
    delete updated[key]
    setField("output_mapping", updated)
  }

  const updateMapping = (oldKey: string, newKey: string, value: string) => {
    const updated: Record<string, string> = {}
    for (const [k, v] of Object.entries(mapping)) {
      if (k === oldKey) {
        updated[newKey] = value
      } else {
        updated[k] = v
      }
    }
    setField("output_mapping", updated)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configOutputMapping")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addMapping}>
          <Plus className="h-3 w-3" />
          {t("configAddMapping")}
        </Button>
      </div>
      <p className="text-[10px] text-muted-foreground/60">
        {t("configOutputMappingHint")}
      </p>
      {entries.map(([key, value], i) => (
        <div key={i} className="space-y-1 rounded-md border border-border p-2">
          <div className="flex items-center gap-2">
            <Input
              className="h-7 text-xs flex-1"
              placeholder={t("configKey")}
              value={key}
              onChange={(e) => updateMapping(key, e.target.value, value)}
            />
            <Button variant="ghost" size="icon-sm" className="h-7 w-7" onClick={() => removeMapping(key)}>
              <Trash2 className="h-3 w-3 text-destructive" />
            </Button>
          </div>
          <div className="flex items-center gap-1">
            <Input
              className="h-7 text-xs flex-1 font-mono"
              placeholder={t("configValue")}
              value={value}
              onChange={(e) => updateMapping(key, key, e.target.value)}
            />
            <VariablePicker
              sourceNodes={otherNodes}
              onInsert={(ref) => updateMapping(key, key, value + ref)}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
