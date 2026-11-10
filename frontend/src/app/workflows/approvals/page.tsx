"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { ArrowLeft, UserCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { StaggerOnce, StaggerItem } from "@/components/ui/fade-in"
import { EmptyState } from "@/components/shared/empty-state"
import { ApprovalCard } from "@/components/workflows/approvals/approval-card"
import { useApprovalDecision } from "@/components/workflows/approvals/use-approval-decision"
import { useAuth } from "@/contexts/auth-context"
import { usePageTitle } from "@/hooks/use-page-title"
import { workflowApi } from "@/lib/api"
import { useModuleGuard } from "@/lib/module-flags"
import {
  announceApprovalsChanged,
  settledStatusOfRequest,
  vanishedRequests,
  type SettledStatus,
} from "@/lib/workflow-approvals"
import type { ApprovalInboxItem } from "@/types/workflow"

/** How often the inbox is read while the page is open. */
const INBOX_INTERVAL_MS = 10_000

/** A request on screen: open, or over (decided elsewhere, run out) until dismissed. */
interface ShownRequest {
  item: ApprovalInboxItem
  settled?: SettledStatus
}

export default function WorkflowApprovalsPage() {
  const t = useTranslations("workflows")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const moduleOk = useModuleGuard("workflows")
  usePageTitle(t("approvalsTitle"))

  // null until the inbox has been read once.
  const [shown, setShown] = useState<ShownRequest[] | null>(null)
  const shownRef = useRef<ShownRequest[]>([])
  shownRef.current = shown ?? []
  // Requests this page decided itself: they leave without a notice.
  const decidedHereRef = useRef<Set<string>>(new Set())
  const loadFailedRef = useRef(false)

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login")
  }, [authLoading, user, router])

  const load = useCallback(async () => {
    let fresh: ApprovalInboxItem[]
    try {
      fresh = (await workflowApi.approvalInbox()).items
      loadFailedRef.current = false
    } catch {
      // Said once, not on every read.
      if (!loadFailedRef.current) toast.error(t("approvalsLoadFailed"))
      loadFailedRef.current = true
      setShown((prev) => prev ?? [])
      return
    }
    const before = shownRef.current
    const handled = new Set([
      ...decidedHereRef.current,
      ...before.filter((s) => s.settled).map((s) => s.item.id),
    ])
    const vanished = vanishedRequests(
      before.map((s) => s.item),
      fresh,
      handled,
    )
    // Decided on another surface, or run out: say how each ended.
    const ended = await Promise.all(
      vanished.map(async (item) => ({ item, settled: await settledStatusOfRequest(item.id) })),
    )
    setShown((prev) => {
      const kept = (prev ?? []).filter((s) => s.settled)
      const keptIds = new Set(kept.map((s) => s.item.id))
      return [
        ...fresh.filter((item) => !keptIds.has(item.id)).map((item) => ({ item })),
        ...kept,
        ...ended.filter((s) => !keptIds.has(s.item.id)),
      ]
    })
  }, [t])

  const userId = user?.id ?? null
  useEffect(() => {
    if (!userId) return
    void load()
    const timer = setInterval(() => void load(), INBOX_INTERVAL_MS)
    const onFocus = () => void load()
    window.addEventListener("focus", onFocus)
    return () => {
      clearInterval(timer)
      window.removeEventListener("focus", onFocus)
    }
  }, [userId, load])

  const handleSettled = useCallback((id: string, status: SettledStatus, mine: boolean) => {
    if (mine) {
      decidedHereRef.current.add(id)
      setShown((prev) => (prev ?? []).filter((s) => s.item.id !== id))
    } else {
      setShown((prev) =>
        (prev ?? []).map((s) => (s.item.id === id ? { ...s, settled: status } : s)),
      )
    }
    announceApprovalsChanged()
  }, [])
  const { busyId, decide } = useApprovalDecision(handleSettled)

  const dismiss = useCallback((id: string) => {
    setShown((prev) => (prev ?? []).filter((s) => s.item.id !== id))
  }, [])

  if (authLoading || !user || !moduleOk) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <UserCheck className="h-5 w-5" />
            {t("approvalsTitle")}
          </h1>
          <p className="text-sm text-muted-foreground">{t("approvalsSubtitle")}</p>
        </div>
        <Button asChild variant="outline" size="sm" className="gap-1.5">
          <Link href="/workflows">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("approvalsBack")}
          </Link>
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {shown === null ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {Array.from({ length: 2 }).map((_, i) => (
              <Skeleton.AgentCard key={i} />
            ))}
          </div>
        ) : shown.length === 0 ? (
          <EmptyState
            icon={<UserCheck />}
            title={t("approvalsEmptyTitle")}
            description={t("approvalsEmptyDescription")}
          />
        ) : (
          <StaggerOnce className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {shown.map(({ item, settled }) => (
              <StaggerItem key={item.id} className="grid">
                <ApprovalCard
                  item={item}
                  settled={settled}
                  busy={busyId === item.id}
                  onDecide={(decision, note) => decide(item.id, decision, note)}
                  onDismiss={() => dismiss(item.id)}
                />
              </StaggerItem>
            ))}
          </StaggerOnce>
        )}
      </div>
    </div>
  )
}
