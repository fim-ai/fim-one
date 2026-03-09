"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { MoreHorizontal, Loader2, Download, FileText, FolderOpen, Trash2 } from "lucide-react"
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
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import type { UserStorageStat, AdminUserFile } from "@/types/admin"

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function AdminStorage() {
  const t = useTranslations("admin.storage")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const [stats, setStats] = useState<{ total_bytes: number; users: UserStorageStat[] } | null>(null)
  const [clearTarget, setClearTarget] = useState<UserStorageStat | null>(null)
  const [showOrphanConfirm, setShowOrphanConfirm] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [filesTarget, setFilesTarget] = useState<UserStorageStat | null>(null)
  const [userFiles, setUserFiles] = useState<AdminUserFile[]>([])
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const [filePage, setFilePage] = useState(1)
  const [fileTotal, setFileTotal] = useState(0)
  const [filePages, setFilePages] = useState(0)

  const load = async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.getStorageStats()
      setStats(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
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
      toast.success(t("clearedStorage", { username: clearTarget.username || clearTarget.email || "" }))
      setClearTarget(null)
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const handleCleanOrphaned = async () => {
    try {
      await adminApi.cleanOrphanedStorage()
      toast.success(t("orphanedCleaned"))
      setShowOrphanConfirm(false)
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const handleRowClick = async (u: UserStorageStat) => {
    setFilesTarget(u)
    setUserFiles([])
    setFilePage(1)
    setFileTotal(0)
    setFilePages(0)
    setIsLoadingFiles(true)
    try {
      const res = await adminApi.listUserFiles(u.user_id, 1)
      setUserFiles(res.items)
      setFileTotal(res.total)
      setFilePages(res.pages)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoadingFiles(false)
    }
  }

  const handleLoadMore = async () => {
    if (!filesTarget) return
    const nextPage = filePage + 1
    setIsLoadingMore(true)
    try {
      const res = await adminApi.listUserFiles(filesTarget.user_id, nextPage)
      setUserFiles((prev) => [...prev, ...res.items])
      setFilePage(nextPage)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoadingMore(false)
    }
  }

  const handleDownload = async (file: AdminUserFile) => {
    if (!filesTarget) return
    setDownloadingId(file.file_id)
    try {
      await adminApi.downloadUserFile(filesTarget.user_id, file.file_id, file.filename)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setDownloadingId(null)
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
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("userColumn")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("filesColumn")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("sizeColumn")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {stats.users.map((u) => (
                <tr key={u.user_id} className="hover:bg-muted/50 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{u.username || u.email}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{u.file_count}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{formatBytes(u.total_bytes)}</td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleRowClick(u)}>
                          <FolderOpen className="mr-2 h-4 w-4" />
                          {t("viewFiles")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" onClick={() => setClearTarget(u)}>
                          <Trash2 className="mr-2 h-4 w-4" />
                          {t("clear")}
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

      {/* Clear user storage */}
      <AlertDialog open={!!clearTarget} onOpenChange={(open) => !open && setClearTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("clearTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("clearDesc", { size: clearTarget ? formatBytes(clearTarget.total_bytes) : "", username: clearTarget?.username || clearTarget?.email || "" })}
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

      {/* User files drawer */}
      <Sheet open={!!filesTarget} onOpenChange={(open) => { if (!open) setFilesTarget(null) }}>
        <SheetContent side="right" className="sm:max-w-xl w-full flex flex-col p-0">
          <SheetHeader className="px-6 pt-6 pb-4 border-b border-border/40 shrink-0">
            <SheetTitle>{t("filesTitle")}</SheetTitle>
            <SheetDescription>
              {t("filesSubtitle", { username: filesTarget?.username || filesTarget?.email || "" })}
              {fileTotal > 0 && !isLoadingFiles ? ` · ${fileTotal}` : ""}
            </SheetDescription>
          </SheetHeader>
          <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4">
            {isLoadingFiles ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : userFiles.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-12">{t("noFiles")}</p>
            ) : (
              <div className="space-y-2">
                {userFiles.map((file) => (
                  <div key={file.file_id} className="flex items-center gap-3 rounded-md border p-3">
                    <FileText className="h-5 w-5 shrink-0 text-muted-foreground" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{file.filename}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatBytes(file.size)} · {file.mime_type}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={downloadingId === file.file_id}
                      onClick={() => handleDownload(file)}
                    >
                      {downloadingId === file.file_id ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Download className="h-4 w-4" />
                      )}
                    </Button>
                  </div>
                ))}
                {filePage < filePages && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full mt-2"
                    disabled={isLoadingMore}
                    onClick={handleLoadMore}
                  >
                    {isLoadingMore && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                    {tc("loadMore")}
                  </Button>
                )}
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}
