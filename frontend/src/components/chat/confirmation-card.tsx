"use client"

/**
 * ConfirmationCard — inline approval prompt rendered in the chat
 * transcript when an agent emits an `awaiting_confirmation` SSE event.
 *
 * Event contract (FROZEN — see Phase 1 Task #3):
 *
 *   {
 *     "type": "awaiting_confirmation",
 *     "confirmation_id": "<uuid>",
 *     "tool_name": "<string>",
 *     "arguments": <object>,
 *     "timeout_at": "<ISO8601 UTC>",
 *     "agent_id": "<uuid>"
 *   }
 *
 * State machine: pending -> submitting -> {approved|rejected|error|expired}.
 */
import { useEffect, useMemo, useState } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  ShieldAlert,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  ChevronDown,
  ChevronUp,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { getErrorMessage } from "@/lib/error-utils"
import {
  respondToConfirmation,
  type ConfirmationDecision,
} from "@/lib/api/confirmations"

type CardState =
  | "pending"
  | "submitting"
  | "approved"
  | "rejected"
  | "expired"
  | "error"

export interface ConfirmationCardProps {
  confirmationId: string
  toolName: string
  /** Arbitrary JSON-compatible tool arguments shown to the approver. */
  arguments: Record<string, unknown>
  /** ISO8601 UTC timestamp of when the request expires. */
  timeoutAt: string
  agentId: string
}

const PARAMS_COLLAPSE_THRESHOLD = 10

function formatDurationShort(ms: number): string {
  const totalSec = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  if (m === 0) return `${s}s`
  return `${m}m ${s.toString().padStart(2, "0")}s`
}

function formatTimeHHMM(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    })
  } catch {
    return iso
  }
}

export function ConfirmationCard({
  confirmationId,
  toolName,
  arguments: args,
  timeoutAt,
  agentId: _agentId,
}: ConfirmationCardProps) {
  void _agentId // reserved for future multi-agent display
  const t = useTranslations("playground")
  const tError = useTranslations("errors")

  const expiryMs = useMemo(() => {
    const parsed = new Date(timeoutAt).getTime()
    return Number.isFinite(parsed) ? parsed : Date.now()
  }, [timeoutAt])

  const initiallyExpired = Date.now() >= expiryMs
  const [state, setState] = useState<CardState>(
    initiallyExpired ? "expired" : "pending",
  )
  const [pendingDecision, setPendingDecision] =
    useState<ConfirmationDecision | null>(null)
  const [decidedAt, setDecidedAt] = useState<string | null>(null)
  const [now, setNow] = useState<number>(() => Date.now())
  const [paramsOpen, setParamsOpen] = useState(false)

  const prettyJson = useMemo(() => {
    try {
      return JSON.stringify(args ?? {}, null, 2)
    } catch {
      return String(args)
    }
  }, [args])

  const paramsLineCount = useMemo(
    () => (prettyJson ? prettyJson.split("\n").length : 0),
    [prettyJson],
  )
  const paramsCollapsible = paramsLineCount > PARAMS_COLLAPSE_THRESHOLD

  // Countdown tick — rerender once per second while pending.
  // Acceptable churn given the 5-minute max window.
  useEffect(() => {
    if (state !== "pending" && state !== "error" && state !== "submitting") {
      return
    }
    const id = window.setInterval(() => {
      setNow(Date.now())
    }, 1000)
    return () => window.clearInterval(id)
  }, [state])

  // Expiry transition (client-side timer).
  useEffect(() => {
    if (state === "pending" || state === "error") {
      if (now >= expiryMs) {
        setState("expired")
      }
    }
  }, [now, expiryMs, state])

  const remainingMs = Math.max(0, expiryMs - now)
  const remainingLabel = formatDurationShort(remainingMs)

  async function handleDecision(decision: ConfirmationDecision) {
    if (state === "submitting") return
    if (state === "expired") return
    setPendingDecision(decision)
    setState("submitting")
    try {
      const res = await respondToConfirmation(confirmationId, decision)
      setDecidedAt(res.decided_at)
      setState(res.status === "approved" ? "approved" : "rejected")
    } catch (err) {
      const msg = getErrorMessage(err, tError) || t("confirmation.errorToast")
      toast.error(msg)
      setState("error")
    } finally {
      setPendingDecision(null)
    }
  }

  // ------ visual variants -------------------------------------------------
  const cardPalette: Record<CardState, string> = {
    pending:
      "border-amber-300/70 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-950/30",
    submitting:
      "border-amber-300/70 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-950/30",
    approved:
      "border-emerald-300/70 bg-emerald-50/60 dark:border-emerald-500/40 dark:bg-emerald-950/30",
    rejected:
      "border-destructive/40 bg-destructive/10",
    expired:
      "border-border bg-muted",
    error:
      "border-amber-300/70 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-950/30",
  }

  const headerIcon = (() => {
    switch (state) {
      case "approved":
        return (
          <CheckCircle2
            className="h-4 w-4 text-emerald-600 dark:text-emerald-400"
            aria-hidden
          />
        )
      case "rejected":
        return <XCircle className="h-4 w-4 text-destructive" aria-hidden />
      case "expired":
        return (
          <Clock
            className="h-4 w-4 text-muted-foreground"
            aria-hidden
          />
        )
      default:
        return (
          <ShieldAlert
            className="h-4 w-4 text-amber-600 dark:text-amber-400"
            aria-hidden
          />
        )
    }
  })()

  const statusBadge = (() => {
    switch (state) {
      case "approved":
        return (
          <Badge
            variant="outline"
            className="border-emerald-400/60 bg-emerald-100/60 text-emerald-700 dark:border-emerald-500/40 dark:bg-emerald-950/40 dark:text-emerald-300"
          >
            {t("confirmation.approved")}
          </Badge>
        )
      case "rejected":
        return (
          <Badge variant="destructive">{t("confirmation.rejected")}</Badge>
        )
      case "expired":
        return (
          <Badge
            variant="outline"
            className="text-muted-foreground"
          >
            {t("confirmation.expired")}
          </Badge>
        )
      default:
        return null
    }
  })()

  const mutedText = state === "expired"

  return (
    <div
      className={cn(
        "rounded-lg border px-4 py-3 space-y-3 min-w-0 w-full",
        cardPalette[state],
      )}
      role="group"
      aria-label={t("confirmation.title")}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        {headerIcon}
        <span
          className={cn(
            "text-sm font-medium",
            mutedText && "text-muted-foreground",
          )}
        >
          {t("confirmation.title")}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {statusBadge}
        </div>
      </div>

      {/* Tool name */}
      <div className="space-y-1">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">
          {t("confirmation.toolLabel")}
        </div>
        <div
          className={cn(
            "text-sm font-mono font-semibold break-all",
            mutedText && "text-muted-foreground",
          )}
        >
          {toolName}
        </div>
      </div>

      {/* Parameters */}
      <div className="space-y-1">
        <div className="flex items-center justify-between gap-2">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">
            {t("confirmation.parametersLabel")}
          </div>
          {paramsCollapsible && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => setParamsOpen((v) => !v)}
              aria-expanded={paramsOpen}
            >
              {paramsOpen ? (
                <>
                  <ChevronUp className="mr-1 h-3 w-3" />
                  {t("confirmation.collapse")}
                </>
              ) : (
                <>
                  <ChevronDown className="mr-1 h-3 w-3" />
                  {t("confirmation.expand")}
                </>
              )}
            </Button>
          )}
        </div>
        <pre
          className={cn(
            "text-xs font-mono bg-muted/50 rounded p-2 overflow-auto whitespace-pre-wrap break-words",
            paramsCollapsible && !paramsOpen ? "max-h-40" : "max-h-96",
          )}
        >
          {prettyJson}
        </pre>
      </div>

      {/* Countdown / resolved info */}
      {(state === "pending" ||
        state === "submitting" ||
        state === "error") && (
        <div
          className="text-xs text-muted-foreground tabular-nums"
          // Announce countdown sparingly — polite and only when user interacts
          // with the card. Continuous updates would be noisy for screen readers.
          aria-live="polite"
        >
          {t("confirmation.expiresIn", { time: remainingLabel })}
        </div>
      )}
      {state === "approved" && decidedAt && (
        <div className="text-xs text-muted-foreground">
          {t("confirmation.approvedBy", { time: formatTimeHHMM(decidedAt) })}
        </div>
      )}
      {state === "rejected" && decidedAt && (
        <div className="text-xs text-muted-foreground">
          {t("confirmation.rejectedAt", { time: formatTimeHHMM(decidedAt) })}
        </div>
      )}

      {/* Action buttons */}
      {(state === "pending" ||
        state === "submitting" ||
        state === "error") && (
        <div className="flex items-center gap-2 pt-1">
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={() => handleDecision("approve")}
            disabled={state === "submitting"}
            aria-label={t("confirmation.approveButton")}
          >
            {state === "submitting" && pendingDecision === "approve" ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : null}
            {t("confirmation.approveButton")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => handleDecision("reject")}
            disabled={state === "submitting"}
            aria-label={t("confirmation.rejectButton")}
            className="border-destructive/60 text-destructive hover:bg-destructive/10 hover:text-destructive"
          >
            {state === "submitting" && pendingDecision === "reject" ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : null}
            {t("confirmation.rejectButton")}
          </Button>
        </div>
      )}
    </div>
  )
}
