"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations, useLocale } from "next-intl"
import { Bot, BookOpen, Loader2, MoreHorizontal, Search, Info, Trash2, FileText } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AdminAgentInfo {
  id: string
  name: string
  description: string | null
  model_name: string | null
  tools: string | null
  kb_ids: string | null
  enable_planning: boolean
  user_id: string
  username: string | null
  email: string | null
  created_at: string
}

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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Count comma-separated items in a JSON-ish string, or return 0. */
function countItems(s: string | null): number {
  if (!s || s === "[]" || s === "null") return 0
  try {
    const parsed = JSON.parse(s)
    return Array.isArray(parsed) ? parsed.length : 0
  } catch {
    // Fall back to comma splitting for plain comma-separated strings
    return s.split(",").filter(Boolean).length
  }
}

const PAGE_SIZE = 20

type View = "agents" | "kbs"

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AdminResources() {
  const t = useTranslations("admin.resources")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

  const [view, setView] = useState<View>("agents")

  // ---- Agent state ----
  const [agents, setAgents] = useState<AdminAgentInfo[]>([])
  const [agentTotal, setAgentTotal] = useState(0)
  const [agentPage, setAgentPage] = useState(1)
  const [agentSearch, setAgentSearch] = useState("")
  const [agentLoading, setAgentLoading] = useState(true)
  const [deleteAgent, setDeleteAgent] = useState<AdminAgentInfo | null>(null)

  // ---- KB state ----
  const [kbs, setKbs] = useState<AdminKBInfo[]>([])
  const [kbTotal, setKbTotal] = useState(0)
  const [kbPage, setKbPage] = useState(1)
  const [kbSearch, setKbSearch] = useState("")
  const [kbLoading, setKbLoading] = useState(true)
  const [deleteKB, setDeleteKB] = useState<AdminKBInfo | null>(null)

  // ---- KB docs dialog ----
  const [docsKB, setDocsKB] = useState<AdminKBInfo | null>(null)
  const [docs, setDocs] = useState<AdminKBDoc[]>([])
  const [docsLoading, setDocsLoading] = useState(false)

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ---- Data fetching ----

  const loadAgents = useCallback(async () => {
    setAgentLoading(true)
    try {
      const res = await adminApi.listAllAgents({ page: agentPage, size: PAGE_SIZE, q: agentSearch || undefined })
      setAgents(res.items)
      setAgentTotal(res.total)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setAgentLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentPage, agentSearch])

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
    if (view === "agents") loadAgents()
  }, [view, loadAgents])

  useEffect(() => {
    if (view === "kbs") loadKBs()
  }, [view, loadKBs])

  // ---- Search debounce ----

  const handleAgentSearch = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setAgentSearch(value)
      setAgentPage(1)
    }, 300)
  }

  const handleKBSearch = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setKbSearch(value)
      setKbPage(1)
    }, 300)
  }

  // ---- Delete handlers ----

  const handleDeleteAgent = async () => {
    if (!deleteAgent) return
    try {
      await adminApi.adminDeleteAgent(deleteAgent.id)
      toast.success(t("agentDeleted"))
      setDeleteAgent(null)
      loadAgents()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

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
  const agentPages = Math.max(1, Math.ceil(agentTotal / PAGE_SIZE))
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

      {/* Sub-section toggle */}
      <div className="flex gap-1 rounded-md border border-border p-0.5 w-fit">
        <button
          className={cn(
            "px-3 py-1 text-sm rounded inline-flex items-center gap-1.5 transition-colors",
            view === "agents" ? "bg-accent" : "hover:bg-accent/50",
          )}
          onClick={() => setView("agents")}
        >
          <Bot className="h-3.5 w-3.5" />
          {t("agentsTab")}
        </button>
        <button
          className={cn(
            "px-3 py-1 text-sm rounded inline-flex items-center gap-1.5 transition-colors",
            view === "kbs" ? "bg-accent" : "hover:bg-accent/50",
          )}
          onClick={() => setView("kbs")}
        >
          <BookOpen className="h-3.5 w-3.5" />
          {t("kbsTab")}
        </button>
      </div>

      {/* ================================================================ */}
      {/* AGENTS VIEW                                                      */}
      {/* ================================================================ */}
      {view === "agents" && (
        <>
          {/* Toolbar */}
          <div className="flex items-center gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder={t("searchAgents")}
                className="pl-9"
                onChange={(e) => handleAgentSearch(e.target.value)}
              />
            </div>
          </div>

          {/* Table */}
          {agentLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : agents.length === 0 ? (
            <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              {t("noAgents")}
            </div>
          ) : (
            <div className="rounded-md border border-border overflow-x-auto">
              <table className="w-full min-w-max text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40">
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colName")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOwner")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colModel")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colTools")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("colKBs")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colPlanning")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCreated")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {agents.map((agent) => (
                    <tr key={agent.id} className="hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3 font-medium text-foreground">{agent.name}</td>
                      <td className="px-4 py-3 text-muted-foreground">{agent.username || agent.email || "--"}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">{agent.model_name ?? "--"}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{countItems(agent.tools)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{countItems(agent.kb_ids)}</td>
                      <td className="px-4 py-3 text-muted-foreground">
                        {agent.enable_planning ? t("yes") : t("no")}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">
                        {new Date(agent.created_at).toLocaleDateString(locale)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() => setDeleteAgent(agent)}
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
          {!agentLoading && agents.length > 0 && (
            <div className="flex items-center justify-end text-sm text-muted-foreground">
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={agentPage <= 1}
                  onClick={() => setAgentPage((p) => Math.max(1, p - 1))}
                >
                  {t("previous")}
                </Button>
                <span>{t("pageOf", { page: agentPage, pages: agentPages })}</span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={agentPage >= agentPages}
                  onClick={() => setAgentPage((p) => Math.min(agentPages, p + 1))}
                >
                  {tc("next")}
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ================================================================ */}
      {/* KNOWLEDGE BASES VIEW                                             */}
      {/* ================================================================ */}
      {view === "kbs" && (
        <>
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
                      <td className="px-4 py-3 font-medium text-foreground">{kb.name}</td>
                      <td className="px-4 py-3 text-muted-foreground">{kb.username || kb.email || "--"}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{kb.document_count}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{kb.total_chunks}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">{kb.embedding_model ?? "--"}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">
                        {new Date(kb.created_at).toLocaleDateString(locale)}
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
        </>
      )}

      {/* ================================================================ */}
      {/* KB Documents Dialog                                              */}
      {/* ================================================================ */}
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

      {/* ================================================================ */}
      {/* Delete Agent AlertDialog                                         */}
      {/* ================================================================ */}
      <AlertDialog open={!!deleteAgent} onOpenChange={(open) => !open && setDeleteAgent(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteAgentTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteAgentDesc", { name: deleteAgent?.name ?? "", owner: deleteAgent?.username || deleteAgent?.email || "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteAgent}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ================================================================ */}
      {/* Delete KB AlertDialog                                            */}
      {/* ================================================================ */}
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
    </div>
  )
}
