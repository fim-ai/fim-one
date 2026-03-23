"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations } from "next-intl"
import { Loader2, MoreHorizontal, Search, Info, Trash2, FileText, Power } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
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
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { cn, formatFileSize } from "@/lib/utils"
import { useDateFormatter } from "@/hooks/use-date-formatter"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AdminKBInfo {
  id: string
  name: string
  description: string | null
  embedding_model: string | null
  chunk_size: number
  document_count: number
  total_chunks: number
  user_id: string
  username: string | null
  email: string | null
  created_at: string
}

interface AdminKBDoc {
  id: string
  filename: string
  file_size: number | null
  chunk_count: number
  status: string
  error_message: string | null
  created_at: string
}

const PAGE_SIZE = 20

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AdminKnowledgeBases() {
  const t = useTranslations("admin.knowledgeBases")
  const tb = useTranslations("admin.resourcesBatch")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDate } = useDateFormatter()

  // ---- KB state ----
  const [kbs, setKbs] = useState<AdminKBInfo[]>([])
  const [kbTotal, setKbTotal] = useState(0)
  const [kbPage, setKbPage] = useState(1)
  const [kbSearch, setKbSearch] = useState("")
  const [kbLoading, setKbLoading] = useState(true)
  const [deleteKB, setDeleteKB] = useState<AdminKBInfo | null>(null)
  const [selectedKbIds, setSelectedKbIds] = useState<Set<string>>(new Set())
  const [batchKbDeleteOpen, setBatchKbDeleteOpen] = useState(false)

  // ---- KB docs dialog ----
  const [docsKB, setDocsKB] = useState<AdminKBInfo | null>(null)
  const [docs, setDocs] = useState<AdminKBDoc[]>([])
  const [docsLoading, setDocsLoading] = useState(false)

  // ---- Batch mutation loading ----
  const [isBatchMutating, setIsBatchMutating] = useState(false)

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ---- Data fetching ----

  const loadKBs = useCallback(async () => {
    setKbLoading(true)
    try {
      const res = await adminApi.listAllKBs({ page: kbPage, size: PAGE_SIZE, q: kbSearch || undefined })
      setKbs(res.items)
      setKbTotal(res.total)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setKbLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kbPage, kbSearch])

  useEffect(() => {
    loadKBs()
  }, [loadKBs])

  // ---- Search debounce ----

  const handleKBSearch = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setKbSearch(value)
      setKbPage(1)
    }, 300)
  }

  // ---- Delete handler ----

  const handleDeleteKB = async () => {
    if (!deleteKB) return
    try {
      await adminApi.adminDeleteKB(deleteKB.id)
      toast.success(t("kbDeleted"))
      setDeleteKB(null)
      loadKBs()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  // ---- Batch operations ----
  const handleBatchDeleteKBs = async () => {
    if (selectedKbIds.size === 0) return
    setIsBatchMutating(true)
    try {
      const result = await adminApi.batchDeleteKBs(Array.from(selectedKbIds))
      toast.success(tb("batchDeleted", { count: result.deleted }))
      setBatchKbDeleteOpen(false)
      setSelectedKbIds(new Set())
      loadKBs()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsBatchMutating(false)
    }
  }

  const handleBatchToggleKBs = async () => {
    if (selectedKbIds.size === 0) return
    setIsBatchMutating(true)
    try {
      const result = await adminApi.batchToggleKBs(Array.from(selectedKbIds), true)
      toast.success(tb("batchToggled", { count: result.toggled }))
      setSelectedKbIds(new Set())
      loadKBs()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsBatchMutating(false)
    }
  }

  // ---- View docs ----

  const handleViewDocs = async (kb: AdminKBInfo) => {
    setDocsKB(kb)
    setDocs([])
    setDocsLoading(true)
    try {
      const detail = await adminApi.getKBDetail(kb.id)
      setDocs(detail.documents)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setDocsLoading(false)
    }
  }

  // ---- Pagination ----
  const kbPages = Math.max(1, Math.ceil(kbTotal / PAGE_SIZE))

  // ---- Render ----

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Admin view notice */}
      <div className="rounded-md border border-blue-500/30 bg-blue-50 dark:bg-blue-950/20 px-4 py-3 flex items-start gap-3">
        <Info className="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" />
        <div>
          <p className="text-sm font-medium text-blue-700 dark:text-blue-300">{t("adminNoticeTitle")}</p>
          <p className="text-xs text-blue-600/80 dark:text-blue-400/80 mt-0.5">{t("adminNoticeDesc")}</p>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("searchKBs")}
            className="pl-9"
            onChange={(e) => handleKBSearch(e.target.value)}
          />
        </div>
        {selectedKbIds.size > 0 && (
          <>
            <span className="text-sm text-muted-foreground">{tb("selected", { count: selectedKbIds.size })}</span>
            <Button variant="outline" size="sm" className="gap-1.5" onClick={handleBatchToggleKBs} disabled={isBatchMutating}>
              <Power className="h-4 w-4" />
              {tb("batchToggle")}
            </Button>
            <Button variant="destructive" size="sm" className="gap-1.5" onClick={() => setBatchKbDeleteOpen(true)} disabled={isBatchMutating}>
              <Trash2 className="h-4 w-4" />
              {tb("batchDelete")}
            </Button>
          </>
        )}
      </div>

      {/* Table */}
      {kbLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : kbs.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noKBs")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left">
                  <Checkbox
                    checked={selectedKbIds.size === kbs.length && kbs.length > 0}
                    onCheckedChange={() => {
                      if (selectedKbIds.size === kbs.length) setSelectedKbIds(new Set())
                      else setSelectedKbIds(new Set(kbs.map((k) => k.id)))
                    }}
                  />
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colName")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOwner")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colDocs")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colChunks")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colEmbedding")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCreated")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {kbs.map((kb) => (
                <tr key={kb.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3">
                    <Checkbox
                      checked={selectedKbIds.has(kb.id)}
                      onCheckedChange={() => {
                        setSelectedKbIds((prev) => {
                          const next = new Set(prev)
                          if (next.has(kb.id)) next.delete(kb.id)
                          else next.add(kb.id)
                          return next
                        })
                      }}
                    />
                  </td>
                  <td className="px-4 py-3 font-medium text-foreground">{kb.name}</td>
                  <td className="px-4 py-3 text-muted-foreground">{kb.username || kb.email || "--"}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{kb.document_count}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{kb.total_chunks}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{kb.embedding_model ?? "--"}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {formatDate(kb.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleViewDocs(kb)}>
                          <FileText className="mr-2 h-4 w-4" />
                          {t("viewDocs")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() => setDeleteKB(kb)}
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          {tc("delete")}
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
      {!kbLoading && kbs.length > 0 && (
        <div className="flex items-center justify-end text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={kbPage <= 1}
              onClick={() => setKbPage((p) => Math.max(1, p - 1))}
            >
              {t("previous")}
            </Button>
            <span>{t("pageOf", { page: kbPage, pages: kbPages })}</span>
            <Button
              variant="outline"
              size="sm"
              disabled={kbPage >= kbPages}
              onClick={() => setKbPage((p) => Math.min(kbPages, p + 1))}
            >
              {tc("next")}
            </Button>
          </div>
        </div>
      )}

      {/* KB Documents Dialog */}
      <Dialog open={!!docsKB} onOpenChange={(open) => !open && setDocsKB(null)}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("docsTitle")}</DialogTitle>
            <DialogDescription>
              {t("docsSubtitle", { name: docsKB?.name ?? "", count: docs.length })}
            </DialogDescription>
          </DialogHeader>

          {docsLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : docs.length === 0 ? (
            <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              {t("noKBs")}
            </div>
          ) : (
            <div className="rounded-md border border-border overflow-x-auto max-h-[60vh] overflow-y-auto">
              <table className="w-full min-w-max text-sm">
                <thead className="sticky top-0 z-10">
                  <tr className="border-b border-border bg-muted/40">
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colFilename")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colSize")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colDocChunks")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colStatus")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {docs.map((doc) => (
                    <tr key={doc.id} className="hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3 font-medium text-foreground max-w-[250px] truncate" title={doc.filename}>
                        {doc.filename}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                        {doc.file_size != null ? formatFileSize(doc.file_size) : "--"}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">{doc.chunk_count}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">
                        <span
                          className={cn(
                            "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium",
                            doc.status === "ready"
                              ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300"
                              : doc.status === "error"
                                ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
                                : "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
                          )}
                          title={doc.error_message ?? undefined}
                        >
                          {doc.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Delete KB AlertDialog */}
      <AlertDialog open={!!deleteKB} onOpenChange={(open) => !open && setDeleteKB(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteKBTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteKBDesc", { name: deleteKB?.name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteKB}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Batch Delete KBs AlertDialog */}
      <AlertDialog open={batchKbDeleteOpen} onOpenChange={setBatchKbDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{tb("batchDeleteConfirm", { count: selectedKbIds.size })}</AlertDialogTitle>
            <AlertDialogDescription>
              {tb("batchDeleteConfirmDesc", { count: selectedKbIds.size })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleBatchDeleteKBs}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={isBatchMutating}
            >
              {isBatchMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
