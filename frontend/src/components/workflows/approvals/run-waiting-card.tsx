"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { Hourglass } from "lucide-react"
import { workflowApi } from "@/lib/api"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { announceApprovalsChanged, type SettledStatus } from "@/lib/workflow-approvals"
import type { ApprovalInboxItem, RunWaitingItem } from "@/types/workflow"
import { DecisionControls } from "./decision-controls"
import { RequestDetails } from "./request-details"
import { useApprovalDecision } from "./use-approval-decision"

interface RunWaitingCardProps {
  /** What the run waits on. */
  waitingFor: RunWaitingItem[]
  /** The name of a step, for a request the inbox does not describe. */
  nodeLabel: (nodeId: string) => string
  /** A request was decided (here or elsewhere): the run is worth reading again. */
  onDecided: () => void
}

/**
 * The run panel's "waiting for a decision" block: the steps the run stopped
 * at, with Approve / Reject for the requests the signed-in user may decide
 * (those in their approval inbox) and a note for the ones someone else has
 * to decide.
 */
export function RunWaitingCard({ waitingFor, nodeLabel, onDecided }: RunWaitingCardProps) {
  const t = useTranslations("workflows")
  const { formatDateTime } = useDateFormatter()
  // The user's inbox by request id; null until it has been read.
  const [inbox, setInbox] = useState<Record<string, ApprovalInboxItem> | null>(null)
  const [settled, setSettled] = useState<Record<string, SettledStatus>>({})

  const ids = useMemo(() => waitingFor.map((w) => w.confirmation_id).join(","), [waitingFor])
  useEffect(() => {
    if (!ids) return
    let cancelled = false
    workflowApi
      .approvalInbox()
      .then((data) => {
        if (cancelled) return
        setInbox(Object.fromEntries(data.items.map((item) => [item.id, item])))
      })
      .catch(() => {
        // Without the inbox nothing can be decided from here; the page can.
        if (!cancelled) setInbox({})
      })
    return () => {
      cancelled = true
    }
  }, [ids])

  const handleSettled = useCallback(
    (id: string, status: SettledStatus) => {
      setSettled((prev) => ({ ...prev, [id]: status }))
      announceApprovalsChanged()
      onDecided()
    },
    [onDecided],
  )
  const { busyId, decide } = useApprovalDecision(handleSettled)

  if (waitingFor.length === 0) return null

  return (
    <div
      data-testid="run-waiting"
      className="space-y-3 rounded-md border border-amber-500/40 bg-amber-500/5 p-3"
    >
      <div className="flex items-start gap-2">
        <Hourglass className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
        <div className="space-y-0.5">
          <p className="text-xs font-medium text-foreground">{t("runPanelStatusWaiting")}</p>
          <p className="text-[11px] text-muted-foreground">{t("runPanelWaitingHint")}</p>
        </div>
      </div>

      {waitingFor.map((waiting) => {
        const id = waiting.confirmation_id
        const item = inbox?.[id]
        const expires = item?.expires_at ?? waiting.expires_at
        return (
          <div
            key={id}
            data-testid="run-waiting-item"
            className="space-y-2 rounded-md border border-border bg-background p-2"
          >
            <p className="text-xs font-medium text-foreground">
              {item?.step_label || nodeLabel(waiting.node_id)}
              {waiting.node_id && (
                <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                  ({waiting.node_id})
                </span>
              )}
            </p>
            {item && <RequestDetails item={item} compact />}
            {expires && (
              <p className="text-[11px] text-muted-foreground">
                {t("runPanelWaitingExpires", { time: formatDateTime(expires) })}
              </p>
            )}
            {settled[id] ? (
              <p className="text-xs text-muted-foreground">
                {t(`approvalSettled_${settled[id]}`)}
              </p>
            ) : item ? (
              <DecisionControls
                compact
                busy={busyId === id}
                onDecide={(decision, note) => decide(id, decision, note)}
              />
            ) : inbox ? (
              <p className="text-xs text-muted-foreground">{t("runPanelWaitingOthers")}</p>
            ) : null}
          </div>
        )
      })}

      <Link
        href="/workflows/approvals"
        className="inline-block text-xs font-medium text-primary underline-offset-2 hover:underline"
      >
        {t("runPanelOpenApprovals")}
      </Link>
    </div>
  )
}
