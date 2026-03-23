"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import {
  ArrowLeftRight,
  Clock,
  History,
  Loader2,
  RotateCcw,
} from "lucide-react"
import { toast } from "sonner"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { workflowApi } from "@/lib/api"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { VersionDiffDialog } from "@/components/workflows/version-diff-dialog"
import type { WorkflowVersionResponse } from "@/types/workflow"

interface VersionHistorySheetProps {
  workflowId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onVersionRestored: () => void
}

export function VersionHistorySheet({
  workflowId,
  open,
  onOpenChange,
  onVersionRestored,
}: VersionHistorySheetProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")
  const { formatRelativeTime } = useDateFormatter()

  const [versions, setVersions] = useState<WorkflowVersionResponse[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [isRestoring, setIsRestoring] = useState(false)
  const [restoreTarget, setRestoreTarget] = useState<WorkflowVersionResponse | null>(null)
  const [diffTarget, setDiffTarget] = useState<WorkflowVersionResponse | null>(null)

  // Load versions when sheet opens
  useEffect(() => {
    if (!open || !workflowId) return
    let cancelled = false
    setIsLoading(true)
    setPage(1)
    workflowApi
      .getVersions(workflowId, 1, 20)
      .then((data) => {
        if (!cancelled) {
          setVersions(data.items)
          setHasMore(data.page < data.pages)
        }
      })
      .catch(() => {
        if (!cancelled) toast.error(t("versionHistoryLoadFailed"))
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, workflowId, t])

  const handleLoadMore = useCallback(async () => {
    const nextPage = page + 1
    setIsLoadingMore(true)
    try {
      const data = await workflowApi.getVersions(workflowId, nextPage, 20)
      setVersions((prev) => [...prev, ...data.items])
      setPage(nextPage)
      setHasMore(data.page < data.pages)
    } catch {
      toast.error(t("versionHistoryLoadFailed"))
    } finally {
      setIsLoadingMore(false)
    }
  }, [workflowId, page, t])

  const handleRestore = useCallback(async () => {
    if (!restoreTarget) return
    setIsRestoring(true)
    try {
      await workflowApi.restoreVersion(workflowId, restoreTarget.id)
      toast.success(t("versionHistoryRestored"))
      setRestoreTarget(null)
      onOpenChange(false)
      onVersionRestored()
    } catch {
      toast.error(t("versionHistoryRestoreFailed"))
    } finally {
      setIsRestoring(false)
    }
  }, [workflowId, restoreTarget, t, onOpenChange, onVersionRestored])

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="sm:max-w-md p-0 flex flex-col">
          <SheetHeader className="px-6 pt-6 pb-3 border-b border-border/40 shrink-0">
            <SheetTitle className="text-sm">{t("versionHistoryTitle")}</SheetTitle>
            <SheetDescription className="text-xs">
              {t("versionHistoryDescription")}
            </SheetDescription>
          </SheetHeader>

          <ScrollArea className="flex-1 min-h-0">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : versions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <History className="h-8 w-8 mb-2 opacity-40" />
                <p className="text-sm">{t("versionHistoryEmpty")}</p>
              </div>
            ) : (
              <div className="p-2 space-y-1">
                {versions.map((version, index) => (
                  <div
                    key={version.id}
                    className="rounded-md border border-border p-3 hover:bg-accent/50 transition-colors"
                  >
                    <div className="flex items-center gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <Badge
                            variant="secondary"
                            className="text-[10px] px-1.5 py-0 h-5 shrink-0 tabular-nums"
                          >
                            {t("versionHistoryVersion", { version: version.version_number })}
                          </Badge>
                          {index === 0 && (
                            <Badge
                              variant="secondary"
                              className="text-[10px] px-1.5 py-0 h-5 shrink-0 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                            >
                              {t("versionHistoryCurrent")}
                            </Badge>
                          )}
                          <span className="text-[10px] text-muted-foreground">
                            {formatRelativeTime(version.created_at)}
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1 truncate">
                          {version.change_summary || t("versionHistoryNoDescription")}
                        </p>
                      </div>
                      {index !== 0 && (
                        <div className="flex items-center gap-1 shrink-0">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() => setDiffTarget(version)}
                            title={t("versionDiffCompare")}
                          >
                            <ArrowLeftRight className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="gap-1.5 text-xs"
                            onClick={() => setRestoreTarget(version)}
                            disabled={isRestoring}
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                            {t("versionHistoryRestore")}
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>
                ))}

                {hasMore && (
                  <div className="flex justify-center pt-2 pb-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={handleLoadMore}
                      disabled={isLoadingMore}
                      className="gap-1.5 text-xs"
                    >
                      {isLoadingMore ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Clock className="h-3.5 w-3.5" />
                      )}
                      {t("versionHistoryLoadMore")}
                    </Button>
                  </div>
                )}
              </div>
            )}
          </ScrollArea>
        </SheetContent>
      </Sheet>

      {/* Version Diff - sibling of Sheet per project convention */}
      <VersionDiffDialog
        open={diffTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDiffTarget(null)
        }}
        versionA={diffTarget}
        versionB={versions.length > 0 ? versions[0] : null}
      />

      {/* Restore Confirmation - sibling of Sheet per project convention */}
      <AlertDialog
        open={restoreTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRestoreTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("versionHistoryRestoreTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {restoreTarget
                ? t("versionHistoryRestoreDescription", {
                    version: restoreTarget.version_number,
                  })
                : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isRestoring}>
              {tc("cancel")}
            </AlertDialogCancel>
            <AlertDialogAction onClick={handleRestore} disabled={isRestoring}>
              {isRestoring ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : null}
              {t("versionHistoryRestore")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
