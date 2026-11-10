import { useCallback, useState } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"

import type { ConfirmationDecision } from "@/lib/api/confirmations"
import { getErrorMessage } from "@/lib/error-utils"
import { decideApproval, type SettledStatus } from "@/lib/workflow-approvals"

/**
 * Sending decisions from a list of requests. `onSettled(id, status, mine)`
 * is called when a request is over: `mine` when this click decided it, not
 * when someone else had (or it had run out), which is not an error and gets
 * no toast. A refusal (not allowed, gone, network) is a toast and the
 * request stays as it was.
 */
export function useApprovalDecision(
  onSettled: (id: string, status: SettledStatus, mine: boolean) => void,
) {
  const t = useTranslations("workflows")
  const tError = useTranslations("errors")
  const [busyId, setBusyId] = useState<string | null>(null)

  const decide = useCallback(
    async (id: string, decision: ConfirmationDecision, note: string) => {
      setBusyId(id)
      try {
        const result = await decideApproval(id, decision, note)
        if (result.outcome === "decided") {
          toast.success(
            result.status === "approved" ? t("approvalApprovedToast") : t("approvalRejectedToast"),
          )
        }
        onSettled(id, result.status, result.outcome === "decided")
      } catch (err) {
        toast.error(getErrorMessage(err, tError))
      } finally {
        setBusyId(null)
      }
    },
    [onSettled, t, tError],
  )

  return { busyId, decide }
}
