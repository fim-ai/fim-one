"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  Loader2,
  MoreHorizontal,
  CheckCircle,
  XCircle,
  Clock,
  BarChart3,
  TrendingUp,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Textarea } from "@/components/ui/textarea"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi, type AdminReview, type AdminReviewStats } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { useDateFormatter } from "@/hooks/use-date-formatter"

const PAGE_SIZE = 20

export function AdminReviews() {
  const t = useTranslations("admin.reviews")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDate } = useDateFormatter()

  // --- State ---
  const [reviews, setReviews] = useState<AdminReview[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [stats, setStats] = useState<AdminReviewStats | null>(null)

  // --- Selection ---
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  // --- Reject dialog ---
  const [rejectOpen, setRejectOpen] = useState(false)
  const [rejectReason, setRejectReason] = useState("")
  const [rejectIds, setRejectIds] = useState<string[]>([])

  const [isMutating, setIsMutating] = useState(false)

  // --- Load ---
  const loadReviews = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listPendingReviews({ page, size: PAGE_SIZE })
      setReviews(data.items ?? [])
      setTotal(data.total ?? 0)
      setPages(Math.max(1, Math.ceil((data.total ?? 0) / PAGE_SIZE)))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, tError])

  const loadStats = useCallback(async () => {
    try {
      const data = await adminApi.getReviewStats()
      setStats(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }, [tError])

  useEffect(() => { loadReviews() }, [loadReviews])
  useEffect(() => { loadStats() }, [loadStats])

  // --- Approve ---
  const handleApprove = async (ids: string[]) => {
    setIsMutating(true)
    try {
      const result = await adminApi.batchApproveReviews(ids)
      toast.success(ids.length === 1 ? t("approved") : t("batchApproved", { count: result.approved }))
      setSelectedIds(new Set())
      loadReviews()
      loadStats()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Reject ---
  const openReject = (ids: string[]) => {
    setRejectIds(ids)
    setRejectReason("")
    setRejectOpen(true)
  }

  const handleReject = async () => {
    setIsMutating(true)
    try {
      const result = await adminApi.batchRejectReviews(rejectIds, rejectReason.trim() || undefined)
      toast.success(rejectIds.length === 1 ? t("rejected") : t("batchRejected", { count: result.rejected }))
      setRejectOpen(false)
      setSelectedIds(new Set())
      loadReviews()
      loadStats()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Selection ---
  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    if (selectedIds.size === reviews.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(reviews.map((r) => r.id)))
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Clock className="h-4 w-4" />
              <p className="text-xs font-medium">{t("pendingReviews")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">{stats.pending}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <BarChart3 className="h-4 w-4" />
              <p className="text-xs font-medium">{t("avgReviewTime")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">
              {stats.avg_review_time_hours !== null ? `${stats.avg_review_time_hours.toFixed(1)}h` : "--"}
            </p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <TrendingUp className="h-4 w-4" />
              <p className="text-xs font-medium">{t("approvalRate")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">
              {stats.approval_rate !== null ? `${stats.approval_rate.toFixed(1)}%` : "--"}
            </p>
          </div>
        </div>
      )}

      {/* Batch actions */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">{t("selected", { count: selectedIds.size })}</span>
          <Button
            size="sm"
            className="gap-1.5"
            onClick={() => handleApprove(Array.from(selectedIds))}
            disabled={isMutating}
          >
            <CheckCircle className="h-4 w-4" />
            {t("approveSelected")}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            className="gap-1.5"
            onClick={() => openReject(Array.from(selectedIds))}
            disabled={isMutating}
          >
            <XCircle className="h-4 w-4" />
            {t("rejectSelected")}
          </Button>
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : reviews.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noReviews")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left">
                  <Checkbox
                    checked={selectedIds.size === reviews.length && reviews.length > 0}
                    onCheckedChange={toggleAll}
                  />
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colResourceType")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colResourceName")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOrganization")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colSubmitter")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colSubmittedAt")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {reviews.map((review) => (
                <tr key={review.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3">
                    <Checkbox
                      checked={selectedIds.has(review.id)}
                      onCheckedChange={() => toggleSelect(review.id)}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant="secondary">{review.resource_type}</Badge>
                  </td>
                  <td className="px-4 py-3 font-medium text-foreground">{review.resource_name}</td>
                  <td className="px-4 py-3 text-muted-foreground">{review.org_name || "--"}</td>
                  <td className="px-4 py-3 text-muted-foreground">{review.submitter_name || "--"}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {formatDate(review.submitted_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleApprove([review.id])}>
                          <CheckCircle className="mr-2 h-4 w-4" />
                          {t("approve")}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() => openReject([review.id])}
                        >
                          <XCircle className="mr-2 h-4 w-4" />
                          {t("reject")}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {!isLoading && reviews.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t("totalItems", { count: total })}</span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              {t("previous")}
            </Button>
            <span>{t("pageOf", { page, pages })}</span>
            <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage((p) => Math.min(pages, p + 1))}>
              {tc("next")}
            </Button>
          </div>
        </div>
      )}

      {/* --- Reject Dialog --- */}
      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("rejectTitle")}</DialogTitle>
            <DialogDescription>{t("rejectDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>{t("rejectReason")}</Label>
              <Textarea
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder={t("rejectReasonPlaceholder")}
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectOpen(false)}>{tc("cancel")}</Button>
            <Button variant="destructive" onClick={handleReject} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("reject")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
