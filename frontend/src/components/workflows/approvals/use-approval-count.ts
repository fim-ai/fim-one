import { useCallback, useEffect, useState } from "react"

import { workflowApi } from "@/lib/api"
import { APPROVALS_CHANGED_EVENT } from "@/lib/workflow-approvals"

/** How often the pending count is read. */
export const APPROVAL_COUNT_INTERVAL_MS = 30_000

/**
 * How many requests wait for the signed-in user's decision. Read on mount,
 * every {@link APPROVAL_COUNT_INTERVAL_MS}, when the window gets focus and
 * right after a decision was sent from this page. 0 until first read, and
 * when it cannot be read.
 */
export function useApprovalCount(enabled = true): { count: number; refresh: () => void } {
  const [count, setCount] = useState(0)

  const refresh = useCallback(() => {
    workflowApi
      .approvalInboxCount()
      .then((n) => setCount(typeof n === "number" && n > 0 ? n : 0))
      .catch(() => {
        // Keep the last count; the next read may succeed.
      })
  }, [])

  useEffect(() => {
    if (!enabled) return
    refresh()
    const timer = setInterval(refresh, APPROVAL_COUNT_INTERVAL_MS)
    window.addEventListener("focus", refresh)
    window.addEventListener(APPROVALS_CHANGED_EVENT, refresh)
    return () => {
      clearInterval(timer)
      window.removeEventListener("focus", refresh)
      window.removeEventListener(APPROVALS_CHANGED_EVENT, refresh)
    }
  }, [enabled, refresh])

  return { count, refresh }
}
