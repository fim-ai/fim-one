"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Trash2, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
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
import { adminApi } from "@/lib/api"
import type { UserStorageStat } from "@/types/admin"

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function AdminStorage() {
  const t = useTranslations("admin.storage")
  const tc = useTranslations("common")
  const [stats, setStats] = useState<{ total_bytes: number; users: UserStorageStat[] } | null>(null)
  const [clearTarget, setClearTarget] = useState<UserStorageStat | null>(null)
  const [showOrphanConfirm, setShowOrphanConfirm] = useState(false)
  const [isLoading, setIsLoading] = useState(true)

  const errMsg = (err: unknown) =>
    err instanceof Error ? err.message : "Operation failed"

  const load = async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.getStorageStats()
      setStats(data)
    } catch (err) {
      toast.error(errMsg(err))
    } finally {
      setIsLoading(false)
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [])

  const handleClearUser = async () => {
    if (!clearTarget) return
    try {
      await adminApi.clearUserStorage(clearTarget.user_id)
      toast.success(t("clearedStorage", { username: clearTarget.username }))
      setClearTarget(null)
      load()
    } catch (err) {
      toast.error(errMsg(err))
    }
  }

  const handleCleanOrphaned = async () => {
    try {
      await adminApi.cleanOrphanedStorage()
      toast.success(t("orphanedCleaned"))
      setShowOrphanConfirm(false)
      load()
    } catch (err) {
      toast.error(errMsg(err))
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">
            {t("subtitle")}{stats ? ` ${t("totalSize", { size: formatBytes(stats.total_bytes) })}` : ""}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="text-destructive border-destructive/30 hover:bg-destructive/5"
          onClick={() => setShowOrphanConfirm(true)}
        >
          {t("cleanOrphanedFiles")}
        </Button>
      </div>

      {!stats?.users.length ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noUploads")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("userColumn")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("filesColumn")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("sizeColumn")}</th>
                <th className="px-4 py-2.5 w-10" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {stats.users.map((u) => (
                <tr key={u.user_id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{u.username}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{u.file_count}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{formatBytes(u.total_bytes)}</td>
                  <td className="px-4 py-3">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 w-7 p-0 text-destructive"
                      onClick={() => setClearTarget(u)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Clear user storage */}
      <AlertDialog open={!!clearTarget} onOpenChange={(open) => !open && setClearTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("clearTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("clearDesc", { size: clearTarget ? formatBytes(clearTarget.total_bytes) : "", username: clearTarget?.username ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleClearUser} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              {t("clear")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Clean orphaned */}
      <AlertDialog open={showOrphanConfirm} onOpenChange={setShowOrphanConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("cleanOrphanedTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("cleanOrphanedDesc")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleCleanOrphaned}>{t("clean")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
