"use client"

import { useState } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { Clock, GitBranch, X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import type { ConfirmationDecision } from "@/lib/api/confirmations"
import type { SettledStatus } from "@/lib/workflow-approvals"
import type { ApprovalInboxItem } from "@/types/workflow"
import { DecisionControls } from "./decision-controls"
import { hiddenArgumentCharacters, RequestDetails } from "./request-details"

interface ApprovalCardProps {
  item: ApprovalInboxItem
  /** Set when the request is over without this page having decided it. */
  settled?: SettledStatus
  busy: boolean
  onDecide: (decision: ConfirmationDecision, note: string) => void
  onDismiss: () => void
}

/** One request of the approval inbox. */
export function ApprovalCard({ item, settled, busy, onDecide, onDismiss }: ApprovalCardProps) {
  const t = useTranslations("workflows")
  // Long arguments start collapsed; they must have been opened once
  // before the request can be approved.
  const [argumentsSeen, setArgumentsSeen] = useState(false)
  const hidden = hiddenArgumentCharacters(item)
  const { formatRelativeTime, formatDateTime } = useDateFormatter()

  const starter = item.started_by?.name
  const startedBy = !starter
    ? null
    : item.run_mode === "trigger"
      ? t("approvalStartedByTrigger", { name: starter })
      : item.run_mode === "schedule"
        ? t("approvalStartedBySchedule", { name: starter })
        : t("approvalStartedBy", { name: starter })
  const kindLabel =
    item.kind === "confirmation" ? t("approvalKind_confirmation") : t("approvalKind_workflow_approval")

  return (
    <div
      data-testid="approval-card"
      data-settled={settled ?? ""}
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-1.5 text-sm font-semibold">
            <GitBranch className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            {item.workflow_id ? (
              <Link
                href={`/workflows/${item.workflow_id}`}
                className="truncate text-foreground underline-offset-2 hover:underline"
              >
                {item.workflow_name || t("approvalUnknownWorkflow")}
              </Link>
            ) : (
              <span className="truncate">{item.workflow_name || t("approvalUnknownWorkflow")}</span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {t("approvalStepLabel", { step: item.step_label || item.node_id })}
          </p>
        </div>
        <Badge variant="secondary" className="shrink-0 text-[10px]">
          {kindLabel}
        </Badge>
      </div>

      <RequestDetails item={item} onExpanded={() => setArgumentsSeen(true)} />

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {startedBy && <span>{startedBy}</span>}
        <span>{t("approvalRequested", { time: formatRelativeTime(item.created_at) })}</span>
        {item.expires_at && (
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {t("runPanelWaitingExpires", { time: formatDateTime(item.expires_at) })}
          </span>
        )}
      </div>

      {settled ? (
        <div className="flex items-center justify-between gap-2 rounded-md bg-muted/50 px-3 py-2">
          <p className="text-sm text-muted-foreground">{t(`approvalSettled_${settled}`)}</p>
          <Button variant="ghost" size="sm" className="gap-1" onClick={onDismiss}>
            <X className="h-3.5 w-3.5" />
            {t("approvalDismiss")}
          </Button>
        </div>
      ) : (
        <DecisionControls
          busy={busy}
          onDecide={onDecide}
          approveBlockedReason={
            hidden > 0 && !argumentsSeen
              ? t("approvalExpandFirst", { count: hidden })
              : undefined
          }
        />
      )}
    </div>
  )
}
