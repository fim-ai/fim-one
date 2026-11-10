"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Clock, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { workflowApi } from "@/lib/api"
import type { WorkflowScheduleResponse } from "@/types/workflow"

const COMMON_TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Asia/Singapore",
  "Australia/Sydney",
  "Pacific/Auckland",
]

interface CronExample {
  label: string
  cron: string
}

interface ScheduleDialogProps {
  workflowId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onScheduleChange?: (hasSchedule: boolean) => void
}

/**
 * Validates a cron expression (5-field format: min hour day month weekday).
 * Returns true if the expression looks valid.
 */
function isValidCron(expr: string): boolean {
  const trimmed = expr.trim()
  if (!trimmed) return false
  const parts = trimmed.split(/\s+/)
  if (parts.length !== 5) return false
  // Basic validation: each field should contain valid cron characters
  const cronFieldPattern = /^(\*|[0-9]+(-[0-9]+)?(\/[0-9]+)?)(,(\*|[0-9]+(-[0-9]+)?(\/[0-9]+)?))*$|^\*\/[0-9]+$|^[A-Z]{3}(-[A-Z]{3})?(,[A-Z]{3}(-[A-Z]{3})?)*$/i
  return parts.every((p) => cronFieldPattern.test(p))
}

export function ScheduleDialog({
  workflowId,
  open,
  onOpenChange,
  onScheduleChange,
}: ScheduleDialogProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")
  const { formatDateTime } = useDateFormatter()

  const [isLoading, setIsLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isClearing, setIsClearing] = useState(false)

  const [cron, setCron] = useState("")
  const [enabled, setEnabled] = useState(true)
  const [timezone, setTimezone] = useState("UTC")
  const [inputsJson, setInputsJson] = useState("{}")
  const [nextRunAt, setNextRunAt] = useState<string | null>(null)
  const [heldByRun, setHeldByRun] = useState<WorkflowScheduleResponse["held_by_run"]>(null)
  const [hasExistingSchedule, setHasExistingSchedule] = useState(false)

  const [cronError, setCronError] = useState<string | null>(null)
  const [inputsError, setInputsError] = useState<string | null>(null)

  const cronExamples: CronExample[] = [
    { label: t("scheduleCronEveryHour"), cron: "0 * * * *" },
    { label: t("scheduleCronDaily9am"), cron: "0 9 * * *" },
    { label: t("scheduleCronWeekdays"), cron: "0 9 * * MON-FRI" },
    { label: t("scheduleCronEvery15min"), cron: "*/15 * * * *" },
    { label: t("scheduleCronWeekly"), cron: "0 9 * * MON" },
    { label: t("scheduleCronMonthly"), cron: "0 0 1 * *" },
  ]

  // Load schedule when dialog opens
  useEffect(() => {
    if (!open) return
    setIsLoading(true)
    setCronError(null)
    setInputsError(null)

    workflowApi
      .getSchedule(workflowId)
      .then((data: WorkflowScheduleResponse) => {
        setCron(data.cron)
        setEnabled(data.enabled)
        setTimezone(data.timezone)
        setInputsJson(data.inputs ? JSON.stringify(data.inputs, null, 2) : "{}")
        setNextRunAt(data.next_run_at)
        setHeldByRun(data.held_by_run ?? null)
        setHasExistingSchedule(true)
      })
      .catch(() => {
        // No schedule exists — start fresh
        setCron("")
        setEnabled(true)
        setTimezone("UTC")
        setInputsJson("{}")
        setNextRunAt(null)
        setHasExistingSchedule(false)
      })
      .finally(() => {
        setIsLoading(false)
      })
  }, [open, workflowId])

  const validateCron = useCallback(
    (value: string): boolean => {
      if (!value.trim()) {
        setCronError(null)
        return false
      }
      if (!isValidCron(value)) {
        setCronError(t("scheduleCronInvalid"))
        return false
      }
      setCronError(null)
      return true
    },
    [t],
  )

  const validateInputs = useCallback(
    (value: string): boolean => {
      const trimmed = value.trim()
      if (!trimmed || trimmed === "{}") {
        setInputsError(null)
        return true
      }
      try {
        JSON.parse(trimmed)
        setInputsError(null)
        return true
      } catch {
        setInputsError(t("scheduleInputsInvalid"))
        return false
      }
    },
    [t],
  )

  const handleCronChange = (value: string) => {
    setCron(value)
    if (cronError) {
      if (isValidCron(value) || !value.trim()) {
        setCronError(null)
      }
    }
  }

  const handleInputsChange = (value: string) => {
    setInputsJson(value)
    if (inputsError) {
      try {
        JSON.parse(value.trim() || "{}")
        setInputsError(null)
      } catch {
        // keep error
      }
    }
  }

  const handleCronExampleClick = (example: string) => {
    setCron(example)
    setCronError(null)
  }

  const handleSave = async () => {
    const cronTrimmed = cron.trim()
    if (!cronTrimmed) {
      setCronError(t("scheduleCronInvalid"))
      return
    }
    if (!validateCron(cronTrimmed)) return
    if (!validateInputs(inputsJson)) return

    let parsedInputs: Record<string, unknown> | null = null
    const jsonTrimmed = inputsJson.trim()
    if (jsonTrimmed && jsonTrimmed !== "{}") {
      try {
        parsedInputs = JSON.parse(jsonTrimmed)
      } catch {
        setInputsError(t("scheduleInputsInvalid"))
        return
      }
    }

    setIsSaving(true)
    try {
      const result = await workflowApi.updateSchedule(workflowId, {
        cron: cronTrimmed,
        enabled,
        inputs: parsedInputs,
        timezone,
      })
      setNextRunAt(result.next_run_at)
      setHasExistingSchedule(true)
      toast.success(t("scheduleSaved"))
      onScheduleChange?.(true)
      onOpenChange(false)
    } catch {
      toast.error(t("scheduleSaveFailed"))
    } finally {
      setIsSaving(false)
    }
  }

  const handleClear = async () => {
    setIsClearing(true)
    try {
      await workflowApi.deleteSchedule(workflowId)
      setHasExistingSchedule(false)
      setCron("")
      setEnabled(true)
      setTimezone("UTC")
      setInputsJson("{}")
      setNextRunAt(null)
      toast.success(t("scheduleCleared"))
      onScheduleChange?.(false)
      onOpenChange(false)
    } catch {
      toast.error(t("scheduleClearFailed"))
    } finally {
      setIsClearing(false)
    }
  }

  const isBusy = isSaving || isClearing

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Clock className="h-4 w-4" />
            {t("scheduleTitle")}
          </DialogTitle>
          <DialogDescription>{t("scheduleDescription")}</DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="space-y-5">
            {/* Status indicator */}
            <div className="flex items-center gap-2">
              <span
                className={
                  hasExistingSchedule && enabled
                    ? "h-2 w-2 rounded-full bg-emerald-500"
                    : "h-2 w-2 rounded-full bg-muted-foreground/40"
                }
              />
              <span className="text-xs text-muted-foreground">
                {hasExistingSchedule && enabled
                  ? t("scheduleStatusActive")
                  : t("scheduleStatusInactive")}
              </span>
            </div>

            {/* Enable/disable toggle */}
            <div className="flex items-center justify-between">
              <Label htmlFor="schedule-enabled" className="text-sm font-medium">
                {t("scheduleEnabledLabel")}
              </Label>
              <Switch
                id="schedule-enabled"
                checked={enabled}
                onCheckedChange={setEnabled}
              />
            </div>

            {/* Cron expression */}
            <div className="space-y-1.5">
              <Label htmlFor="schedule-cron" className="text-sm font-medium">
                {t("scheduleCronLabel")}
              </Label>
              <Input
                id="schedule-cron"
                className="text-sm font-mono"
                value={cron}
                onChange={(e) => handleCronChange(e.target.value)}
                placeholder={t("scheduleCronPlaceholder")}
                aria-invalid={!!cronError}
              />
              {cronError && (
                <p className="text-sm text-destructive">{cronError}</p>
              )}

              {/* Common pattern helpers */}
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground font-medium">
                  {t("scheduleCronHelperTitle")}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {cronExamples.map((ex) => (
                    <button
                      key={ex.cron}
                      type="button"
                      className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-muted/50 px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                      onClick={() => handleCronExampleClick(ex.cron)}
                    >
                      <span className="font-mono text-[10px]">{ex.cron}</span>
                      <span className="text-muted-foreground/70">-</span>
                      <span>{ex.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Timezone */}
            <div className="space-y-1.5">
              <Label htmlFor="schedule-timezone" className="text-sm font-medium">
                {t("scheduleTimezoneLabel")}
              </Label>
              <Select value={timezone} onValueChange={setTimezone}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {COMMON_TIMEZONES.map((tz) => (
                    <SelectItem key={tz} value={tz}>
                      {tz}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Default inputs */}
            <div className="space-y-1.5">
              <Label htmlFor="schedule-inputs" className="text-sm font-medium">
                {t("scheduleInputsLabel")}
              </Label>
              <Textarea
                id="schedule-inputs"
                className="text-sm font-mono min-h-[80px] resize-none"
                value={inputsJson}
                onChange={(e) => handleInputsChange(e.target.value)}
                placeholder={t("scheduleInputsPlaceholder")}
                aria-invalid={!!inputsError}
              />
              {inputsError && (
                <p className="text-sm text-destructive">{inputsError}</p>
              )}
            </div>

            {/* Next run display */}
            {hasExistingSchedule && nextRunAt && enabled && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground font-medium">
                  {t("scheduleNextRun")}:
                </span>
                <span className="text-foreground">
                  {formatDateTime(nextRunAt)}
                </span>
              </div>
            )}
            {hasExistingSchedule && enabled && heldByRun && (
              <p className="text-sm text-amber-600 dark:text-amber-500" data-testid="schedule-held">
                {heldByRun.status === "waiting" ? t("scheduleHeldWaiting") : t("scheduleHeldRunning")}
              </p>
            )}
            {hasExistingSchedule && !nextRunAt && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground font-medium">
                  {t("scheduleNextRun")}:
                </span>
                <span className="text-muted-foreground">
                  {t("scheduleNextRunNone")}
                </span>
              </div>
            )}
          </div>
        )}

        <DialogFooter className="flex-row gap-2 sm:justify-between">
          <div>
            {hasExistingSchedule && (
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClear}
                disabled={isBusy || isLoading}
              >
                {isClearing && (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                )}
                {t("scheduleClearButton")}
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              className="px-6"
              onClick={() => onOpenChange(false)}
            >
              {tc("cancel")}
            </Button>
            <Button
              className="px-6"
              onClick={handleSave}
              disabled={isBusy || isLoading || !!cronError || !!inputsError}
            >
              {isSaving && (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              )}
              {tc("save")}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
