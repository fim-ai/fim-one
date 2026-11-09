"use client"

/**
 * Hook Playground — exercise the real approval-gate round-trip from the UI.
 *
 * This is not a mock.  Pressing "Send Approval" creates a genuine
 * ``ConfirmationRequest`` DB row and ships a production-grade card to the
 * linked Feishu group.  When someone presses Approve/Reject in Feishu, the
 * callback handler flips the row status; this component polls until the
 * terminal state is reached (or the caller closes the dialog).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslations } from "next-intl"
import {
  AlertTriangle,
  CheckCircle2,
  Clock4,
  Loader2,
  Rocket,
  Send,
  Sparkles,
  XCircle,
} from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { channelsApi } from "@/lib/api/channels"
import { getErrorMessage } from "@/lib/error-utils"
import type { Channel, ConfirmationStatus } from "@/types/channel"

interface HookPlaygroundDialogProps {
  channel: Channel | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

const DEFAULT_TOOL_NAME = "delete_customer_records"
const DEFAULT_TOOL_ARGS_JSON = JSON.stringify(
  {
    customer_id: "C-20240892",
    reason: "GDPR erasure request",
    confirmed_by: "ops@acme.com",
  },
  null,
  2,
)

const POLL_INTERVAL_MS = 2000
const POLL_TIMEOUT_MS = 120_000

type Phase = "idle" | "sending" | "waiting" | "done"

export function HookPlaygroundDialog({
  channel,
  open,
  onOpenChange,
}: HookPlaygroundDialogProps) {
  const t = useTranslations("channels.playground")
  const tError = useTranslations("errors")

  const [toolName, setToolName] = useState(DEFAULT_TOOL_NAME)
  const [toolArgsJson, setToolArgsJson] = useState(DEFAULT_TOOL_ARGS_JSON)
  const [jsonError, setJsonError] = useState<string | null>(null)

  const [phase, setPhase] = useState<Phase>("idle")
  const [confirmationId, setConfirmationId] = useState<string | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  const [status, setStatus] = useState<ConfirmationStatus | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  const pollTimerRef = useRef<number | null>(null)
  const tickTimerRef = useRef<number | null>(null)
  const startTsRef = useRef<number>(0)

  const clearTimers = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
    if (tickTimerRef.current !== null) {
      window.clearInterval(tickTimerRef.current)
      tickTimerRef.current = null
    }
  }, [])

  const resetAll = useCallback(() => {
    clearTimers()
    setPhase("idle")
    setConfirmationId(null)
    setSendError(null)
    setStatus(null)
    setElapsedSeconds(0)
  }, [clearTimers])

  // Clean up timers on unmount / dialog close.
  useEffect(() => {
    if (!open) {
      resetAll()
      // Also reset the form back to defaults so every demo run starts fresh.
      setToolName(DEFAULT_TOOL_NAME)
      setToolArgsJson(DEFAULT_TOOL_ARGS_JSON)
      setJsonError(null)
    }
    return () => clearTimers()
  }, [open, resetAll, clearTimers])

  const parsedToolArgs = useMemo(() => {
    try {
      const parsed = JSON.parse(toolArgsJson)
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return null
      }
      return parsed as Record<string, unknown>
    } catch {
      return null
    }
  }, [toolArgsJson])

  const validateJson = (raw: string) => {
    try {
      const parsed = JSON.parse(raw)
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setJsonError(t("errors.jsonNotObject"))
        return false
      }
      setJsonError(null)
      return true
    } catch {
      setJsonError(t("errors.jsonInvalid"))
      return false
    }
  }

  // --- Send + poll ---------------------------------------------------------

  const handleSend = async () => {
    if (!channel) return
    if (!validateJson(toolArgsJson)) return

    setPhase("sending")
    setSendError(null)
    setStatus(null)
    setElapsedSeconds(0)

    try {
      const result = await channelsApi.testApproval(channel.id, {
        tool_name: toolName.trim() || undefined,
        tool_args: parsedToolArgs ?? undefined,
      })
      if (!result.ok || !result.confirmation_id) {
        setSendError(result.error ?? t("errors.sendFailedUnknown"))
        setPhase("done")
        return
      }
      setConfirmationId(result.confirmation_id)
      setPhase("waiting")
      startTsRef.current = Date.now()
      startPolling(result.confirmation_id)
    } catch (err) {
      setSendError(getErrorMessage(err, tError))
      setPhase("done")
    }
  }

  const startPolling = (conf_id: string) => {
    if (!channel) return

    // Tick the elapsed-seconds counter every second.
    tickTimerRef.current = window.setInterval(() => {
      const secs = Math.floor((Date.now() - startTsRef.current) / 1000)
      setElapsedSeconds(secs)
    }, 1000)

    const poll = async () => {
      if (!channel) return
      try {
        const resp = await channelsApi.getConfirmation(channel.id, conf_id)
        setStatus(resp)
        if (resp.status !== "pending") {
          clearTimers()
          setPhase("done")
          return
        }
      } catch (err) {
        // Network blips are fine; keep polling unless deadline hit.
        console.warn("[hook-playground] poll failed", err)
      }
      if (Date.now() - startTsRef.current >= POLL_TIMEOUT_MS) {
        clearTimers()
        setPhase("done")
        setStatus(
          (prev) =>
            prev ?? {
              id: conf_id,
              status: "expired",
              test_mode: true,
              created_at: new Date().toISOString(),
              responded_at: null,
              responded_by_open_id: null,
              tool_name: toolName,
              tool_args: parsedToolArgs,
            },
        )
      }
    }

    // Fire the first poll immediately, then on an interval.
    void poll()
    pollTimerRef.current = window.setInterval(() => {
      void poll()
    }, POLL_INTERVAL_MS)
  }

  // --- Render helpers ------------------------------------------------------

  const renderStatus = () => {
    if (phase === "idle") return null

    if (phase === "sending") {
      return (
        <StatusRow
          icon={<Loader2 className="h-4 w-4 animate-spin" />}
          tone="info"
          title={t("status.sending.title")}
          description={t("status.sending.description")}
        />
      )
    }

    if (sendError) {
      return (
        <StatusRow
          icon={<AlertTriangle className="h-4 w-4" />}
          tone="error"
          title={t("status.sendFailed.title")}
          description={sendError}
        />
      )
    }

    const current = status?.status ?? (phase === "waiting" ? "pending" : undefined)

    if (current === "approved") {
      return (
        <StatusRow
          icon={<CheckCircle2 className="h-4 w-4" />}
          tone="success"
          title={t("status.approved.title")}
          description={t("status.approved.description", {
            operator: status?.responded_by_open_id ?? t("status.unknownOperator"),
          })}
        />
      )
    }
    if (current === "rejected") {
      return (
        <StatusRow
          icon={<XCircle className="h-4 w-4" />}
          tone="error"
          title={t("status.rejected.title")}
          description={t("status.rejected.description", {
            operator: status?.responded_by_open_id ?? t("status.unknownOperator"),
          })}
        />
      )
    }
    if (current === "expired") {
      return (
        <StatusRow
          icon={<Clock4 className="h-4 w-4" />}
          tone="warn"
          title={t("status.expired.title")}
          description={t("status.expired.description")}
        />
      )
    }

    // pending / waiting
    const remaining = Math.max(0, Math.ceil(POLL_TIMEOUT_MS / 1000) - elapsedSeconds)
    return (
      <StatusRow
        icon={<Loader2 className="h-4 w-4 animate-spin" />}
        tone="info"
        title={t("status.waiting.title")}
        description={t("status.waiting.description", {
          elapsed: elapsedSeconds,
          remaining,
        })}
      />
    )
  }

  const isBusy = phase === "sending" || phase === "waiting"
  const canSendAgain = phase === "done"
  const chatLabel = channel?.config.chat_name || channel?.config.chat_id || ""

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && isBusy) {
          // Allow closing mid-wait, but stop polling.
          clearTimers()
        }
        onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            {t("title")}
          </DialogTitle>
          <DialogDescription>
            {t("description", { chat: chatLabel || t("chatFallback") })}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 pt-2">
          {/* Scenario form */}
          <div className="space-y-4 rounded-md border border-border bg-muted/30 p-4">
            <div className="space-y-2">
              <Label htmlFor="playground-tool-name" className="text-sm">
                {t("form.toolName")}
              </Label>
              <Input
                id="playground-tool-name"
                value={toolName}
                onChange={(e) => setToolName(e.target.value)}
                placeholder={DEFAULT_TOOL_NAME}
                disabled={isBusy}
                className="font-mono text-sm"
              />
              <p className="text-xs text-muted-foreground">
                {t("form.toolNameHint")}
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="playground-tool-args" className="text-sm">
                {t("form.toolArgs")}
              </Label>
              <Textarea
                id="playground-tool-args"
                value={toolArgsJson}
                onChange={(e) => {
                  setToolArgsJson(e.target.value)
                  if (jsonError) validateJson(e.target.value)
                }}
                disabled={isBusy}
                rows={7}
                className="font-mono text-xs"
                aria-invalid={jsonError !== null}
              />
              {jsonError ? (
                <p className="text-xs text-destructive">{jsonError}</p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t("form.toolArgsHint")}
                </p>
              )}
            </div>
          </div>

          {/* Status block */}
          {renderStatus() && (
            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("statusHeading")}
              </div>
              {renderStatus()}
              {confirmationId && (
                <p className="font-mono text-[10px] text-muted-foreground">
                  id: {confirmationId}
                </p>
              )}
            </div>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            {isBusy ? t("actions.closeStopsPolling") : t("actions.close")}
          </Button>
          <Button
            type="button"
            onClick={() => {
              if (canSendAgain) {
                resetAll()
              }
              void handleSend()
            }}
            disabled={isBusy || !channel?.is_active}
            className="gap-1.5"
          >
            {phase === "sending" ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : phase === "waiting" ? (
              <Rocket className="h-4 w-4" />
            ) : canSendAgain ? (
              <Send className="h-4 w-4" />
            ) : (
              <Send className="h-4 w-4" />
            )}
            {phase === "sending"
              ? t("actions.sending")
              : phase === "waiting"
                ? t("actions.inFlight")
                : canSendAgain
                  ? t("actions.sendAgain")
                  : t("actions.send")}
          </Button>
        </DialogFooter>

        {!channel?.is_active && (
          <p className="pt-1 text-xs text-amber-600 dark:text-amber-400">
            {t("channelDisabledHint")}
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Status row — compact visual feedback block
// ---------------------------------------------------------------------------

type StatusTone = "info" | "success" | "error" | "warn"

function StatusRow({
  icon,
  title,
  description,
  tone,
}: {
  icon: React.ReactNode
  title: string
  description: string
  tone: StatusTone
}) {
  const toneClasses: Record<StatusTone, { wrap: string; badge: string }> = {
    info: {
      wrap: "border-primary/30 bg-primary/5 text-foreground",
      badge: "border-primary/30 bg-primary/10 text-primary",
    },
    success: {
      wrap: "border-green-500/30 bg-green-50 text-green-900 dark:bg-green-950/30 dark:text-green-300",
      badge: "border-green-500/40 bg-green-100 text-green-800 dark:bg-green-950/60 dark:text-green-300",
    },
    error: {
      wrap: "border-red-500/30 bg-red-50 text-red-900 dark:bg-red-950/30 dark:text-red-300",
      badge: "border-red-500/40 bg-red-100 text-red-800 dark:bg-red-950/60 dark:text-red-300",
    },
    warn: {
      wrap: "border-amber-500/30 bg-amber-50 text-amber-900 dark:bg-amber-950/30 dark:text-amber-300",
      badge: "border-amber-500/40 bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300",
    },
  }
  return (
    <div className={`rounded-md border px-3 py-3 ${toneClasses[tone].wrap}`}>
      <div className="flex items-start gap-3">
        <Badge
          variant="outline"
          className={`h-6 w-6 shrink-0 p-0 flex items-center justify-center ${toneClasses[tone].badge}`}
        >
          {icon}
        </Badge>
        <div className="space-y-0.5">
          <p className="text-sm font-medium">{title}</p>
          <p className="text-xs opacity-90">{description}</p>
        </div>
      </div>
    </div>
  )
}
