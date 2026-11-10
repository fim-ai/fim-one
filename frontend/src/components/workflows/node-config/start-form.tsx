"use client"

import { useTranslations } from "next-intl"
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import type { StartVariable } from "@/types/workflow"
import type { NodeFormProps } from "./types"

/** The types a Start variable can have, as the blueprint stores them. */
export const START_VARIABLE_TYPES = ["string", "number", "boolean", "object", "array"] as const

export function StartForm({ data, setField }: NodeFormProps<"start">) {
  const t = useTranslations("workflows")
  const variables = data.variables ?? []

  const addVariable = () => {
    setField("variables", [...variables, { name: "", type: "string", required: false }])
  }

  const removeVariable = (idx: number) => {
    setField("variables", variables.filter((_, i) => i !== idx))
  }

  const updateVariable = (idx: number, field: keyof StartVariable, value: unknown) => {
    const updated = variables.map((v, i) => (i === idx ? { ...v, [field]: value } : v))
    setField("variables", updated)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configVariables")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addVariable}>
          <Plus className="h-3 w-3" />
          {t("configAddVariable")}
        </Button>
      </div>

      {/* Compact table header */}
      {variables.length > 0 && (
        <div className="grid grid-cols-[1fr_88px_28px] gap-1 px-0.5">
          <span className="text-[10px] font-medium text-muted-foreground/70 uppercase tracking-wider">
            {t("configStartName")}
          </span>
          <span className="text-[10px] font-medium text-muted-foreground/70 uppercase tracking-wider">
            {t("configStartType")}
          </span>
          <span />
        </div>
      )}

      {/* Compact inline rows */}
      {variables.map((v, i) => (
        <div key={i} className="space-y-1.5 rounded-md border border-border p-2">
          {/* Row 1: Name + Type + Remove */}
          <div className="grid grid-cols-[1fr_88px_28px] gap-1 items-center">
            <Input
              className="h-7 text-xs"
              placeholder={t("configVariableName")}
              value={v.name}
              onChange={(e) => updateVariable(i, "name", e.target.value)}
            />
            <Select value={v.type} onValueChange={(val) => updateVariable(i, "type", val)}>
              <SelectTrigger className="w-full h-7 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {START_VARIABLE_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    {t(`configStartType_${type}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="ghost" size="icon-sm" className="h-7 w-7" onClick={() => removeVariable(i)}>
              <Trash2 className="h-3 w-3 text-destructive" />
            </Button>
          </div>
          {/* Row 2: Default + Required toggle */}
          <div className="flex items-center gap-2">
            <Input
              className="h-7 text-xs flex-1"
              placeholder={t("configStartDefault")}
              value={(v.default_value ?? "") as string}
              onChange={(e) => updateVariable(i, "default_value", e.target.value)}
            />
            <div className="flex items-center gap-1.5 shrink-0">
              <label className="text-[10px] text-muted-foreground">{t("configStartRequired")}</label>
              <Switch
                checked={v.required ?? false}
                onCheckedChange={(checked) => updateVariable(i, "required", checked)}
              />
            </div>
          </div>
        </div>
      ))}

      {/* Have the server check a run's inputs against the variables above */}
      <div className="space-y-1 border-t border-border pt-3">
        <div className="flex items-center justify-between gap-2">
          <label htmlFor="start-validate-inputs" className="text-xs font-medium">
            {t("configStartValidateInputs")}
          </label>
          <Switch
            id="start-validate-inputs"
            checked={data.validate_inputs === true}
            onCheckedChange={(checked) => setField("validate_inputs", checked)}
          />
        </div>
        <p className="text-[11px] text-muted-foreground">{t("configStartValidateInputsHint")}</p>
      </div>
    </div>
  )
}
