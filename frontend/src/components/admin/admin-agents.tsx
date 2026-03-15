"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations, useLocale } from "next-intl"
import { Bot, Loader2, MoreHorizontal, Search, Info, Trash2, Power } from "lucide-react"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"

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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AdminAgents() {
  const t = useTranslations("admin.agents")
  const tb = useTranslations("admin.resourcesBatch")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

  // ---- Agent state ----
  const [agents, setAgents] = useState<AdminAgentInfo[]>([])
  const [agentTotal, setAgentTotal] = useState(0)
  const [agentPage, setAgentPage] = useState(1)
  const [agentSearch, setAgentSearch] = useState("")
  const [agentLoading, setAgentLoading] = useState(true)
  const [deleteAgent, setDeleteAgent] = useState<AdminAgentInfo | null>(null)
  const [selectedAgentIds, setSelectedAgentIds] = useState<Set<string>>(new Set())
  const [batchAgentDeleteOpen, setBatchAgentDeleteOpen] = useState(false)

  // ---- Batch mutation loading ----
  const [isBatchMutating, setIsBatchMutating] = useState(false)

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

  useEffect(() => {
    loadAgents()
  }, [loadAgents])

  // ---- Search debounce ----

  const handleAgentSearch = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setAgentSearch(value)
      setAgentPage(1)
    }, 300)
  }

  // ---- Delete handler ----

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

  // ---- Batch operations ----
  const handleBatchDeleteAgents = async () => {
    if (selectedAgentIds.size === 0) return
    setIsBatchMutating(true)
    try {
      const result = await adminApi.batchDeleteAgents(Array.from(selectedAgentIds))
      toast.success(tb("batchDeleted", { count: result.deleted }))
      setBatchAgentDeleteOpen(false)
      setSelectedAgentIds(new Set())
      loadAgents()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsBatchMutating(false)
    }
  }

  const handleBatchToggleAgents = async () => {
    if (selectedAgentIds.size === 0) return
    setIsBatchMutating(true)
    try {
      const result = await adminApi.batchToggleAgents(Array.from(selectedAgentIds), true)
      toast.success(tb("batchToggled", { count: result.toggled }))
      setSelectedAgentIds(new Set())
      loadAgents()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsBatchMutating(false)
    }
  }

  // ---- Pagination ----
  const agentPages = Math.max(1, Math.ceil(agentTotal / PAGE_SIZE))

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
            placeholder={t("searchAgents")}
            className="pl-9"
            onChange={(e) => handleAgentSearch(e.target.value)}
          />
        </div>
        {selectedAgentIds.size > 0 && (
          <>
            <span className="text-sm text-muted-foreground">{tb("selected", { count: selectedAgentIds.size })}</span>
            <Button variant="outline" size="sm" className="gap-1.5" onClick={handleBatchToggleAgents} disabled={isBatchMutating}>
              <Power className="h-4 w-4" />
              {tb("batchToggle")}
            </Button>
            <Button variant="destructive" size="sm" className="gap-1.5" onClick={() => setBatchAgentDeleteOpen(true)} disabled={isBatchMutating}>
              <Trash2 className="h-4 w-4" />
              {tb("batchDelete")}
            </Button>
          </>
        )}
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
                <th className="px-4 py-2.5 text-left">
                  <Checkbox
                    checked={selectedAgentIds.size === agents.length && agents.length > 0}
                    onCheckedChange={() => {
                      if (selectedAgentIds.size === agents.length) setSelectedAgentIds(new Set())
                      else setSelectedAgentIds(new Set(agents.map((a) => a.id)))
                    }}
                  />
                </th>
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
                  <td className="px-4 py-3">
                    <Checkbox
                      checked={selectedAgentIds.has(agent.id)}
                      onCheckedChange={() => {
                        setSelectedAgentIds((prev) => {
                          const next = new Set(prev)
                          if (next.has(agent.id)) next.delete(agent.id)
                          else next.add(agent.id)
                          return next
                        })
                      }}
                    />
                  </td>
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

      {/* Delete Agent AlertDialog */}
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

      {/* Batch Delete Agents AlertDialog */}
      <AlertDialog open={batchAgentDeleteOpen} onOpenChange={setBatchAgentDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{tb("batchDeleteConfirm", { count: selectedAgentIds.size })}</AlertDialogTitle>
            <AlertDialogDescription>
              {tb("batchDeleteConfirmDesc", { count: selectedAgentIds.size })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleBatchDeleteAgents}
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
