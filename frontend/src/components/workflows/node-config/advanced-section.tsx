"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { ChevronDown } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"
import type { WorkflowNodeType, ErrorStrategy } from "@/types/workflow"

/** Per-type default timeout; mirrors `default_timeout_ms` in core/workflow/types.py. */
export function defaultTimeoutMs(nodeType: WorkflowNodeType): number {
  switch (nodeType) {
    case "agent":
      return 300000
    case "llm":
      return 120000
    case "mcp":
      return 60000
    default:
      return 30000
  }
}

// --- Advanced Section (error strategy + timeout) ---

const ERROR_STRATEGIES: ErrorStrategy[] = ["stop_workflow", "continue", "fail_branch"]

export function AdvancedSection({
  errorStrategy,
  timeoutMs,
  retryCount,
  retryDelayMs,
  onChangeErrorStrategy,
  onChangeTimeout,
  onChangeRetryCount,
  onChangeRetryDelay,
}: {
  errorStrategy: ErrorStrategy
  timeoutMs: number
  retryCount: number
  retryDelayMs: number
  onChangeErrorStrategy: (v: ErrorStrategy) => void
  onChangeTimeout: (v: number) => void
  onChangeRetryCount: (v: number) => void
  onChangeRetryDelay: (v: number) => void
}) {
  const t = useTranslations("workflows")
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <Separator className="my-1" />
      <button
        type="button"
        className="flex items-center justify-between w-full text-xs font-medium text-muted-foreground hover:text-foreground transition-colors py-1"
        onClick={() => setExpanded((v) => !v)}
      >
        {t("configSectionAdvanced")}
        <ChevronDown
          className={cn(
            "h-3 w-3 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded && (
        <div className="space-y-3 pb-1">
          {/* Error strategy */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t("configErrorStrategy")}</label>
            <Select
              value={errorStrategy}
              onValueChange={(v) => onChangeErrorStrategy(v as ErrorStrategy)}
            >
              <SelectTrigger className="w-full h-7 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ERROR_STRATEGIES.map((s) => (
                  <SelectItem key={s} value={s} className="text-xs">
                    {t(`configErrorStrategy_${s}` as Parameters<typeof t>[0])}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[10px] text-muted-foreground">
              {t(`configErrorStrategyHint_${errorStrategy}` as Parameters<typeof t>[0])}
            </p>
          </div>
          {/* Timeout */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t("configTimeout")}</label>
            <Input
              type="number"
              className="h-7 text-xs"
              value={timeoutMs}
              min={1000}
              max={600000}
              step={1000}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10)
                if (!isNaN(v) && v > 0) onChangeTimeout(v)
              }}
            />
            <p className="text-[10px] text-muted-foreground">
              {t("configTimeoutHint")}
            </p>
          </div>
          {/* Retry */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t("configRetryCount")}</label>
            <Input
              type="number"
              className="h-7 text-xs"
              value={retryCount}
              min={0}
              max={10}
              step={1}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10)
                if (!isNaN(v) && v >= 0) onChangeRetryCount(v)
              }}
            />
            <p className="text-[10px] text-muted-foreground">
              {t("configRetryCountHint")}
            </p>
          </div>
          {retryCount > 0 && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">{t("configRetryDelay")}</label>
              <Input
                type="number"
                className="h-7 text-xs"
                value={retryDelayMs}
                min={100}
                max={60000}
                step={500}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10)
                  if (!isNaN(v) && v >= 100) onChangeRetryDelay(v)
                }}
              />
              <p className="text-[10px] text-muted-foreground">
                {t("configRetryDelayHint")}
              </p>
            </div>
          )}
        </div>
      )}
    </>
  )
}
