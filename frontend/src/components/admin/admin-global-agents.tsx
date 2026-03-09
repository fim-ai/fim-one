"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  Loader2,
  Search,
  Plus,
  MoreHorizontal,
  Pencil,
  Trash2,
  ToggleLeft,
  Bot,
  Info,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { adminApi } from "@/lib/api"
import type { AdminAgentInfo } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import type { AdminGlobalAgentInfo } from "@/types/admin"

export function AdminGlobalAgents() {
  const t = useTranslations("admin.globalAgents")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  // --- List state ---
  const [agents, setAgents] = useState<AdminGlobalAgentInfo[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [search, setSearch] = useState("")
  const [isLoading, setIsLoading] = useState(true)

  // --- Dialog states ---
  const [showPicker, setShowPicker] = useState(false)
  const [editTarget, setEditTarget] = useState<AdminGlobalAgentInfo | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AdminGlobalAgentInfo | null>(null)

  // --- Debounce ---
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const loadAgents = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await adminApi.listGlobalAgents(page, 20, search || undefined)
      setAgents(data.items)
      setTotal(data.total)
      setPages(data.pages)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, search, tError])

  useEffect(() => {
    loadAgents()
  }, [loadAgents])

  const handleSearchChange = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(value)
      setPage(1)
    }, 300)
  }

  const handleToggle = async (agent: AdminGlobalAgentInfo) => {
    try {
      await adminApi.toggleGlobalAgent(agent.id)
      toast.success(agent.status === "published" ? t("agentDisabled") : t("agentEnabled"))
      loadAgents()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await adminApi.deleteGlobalAgent(deleteTarget.id)
      toast.success(t("globalAgentDeleted"))
      setDeleteTarget(null)
      loadAgents()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Button size="sm" onClick={() => setShowPicker(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("publishGlobalAgent")}
        </Button>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            className="pl-9"
            onChange={(e) => handleSearchChange(e.target.value)}
          />
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : agents.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-8 text-center">
          <p className="text-sm text-muted-foreground">{t("noGlobalAgents")}</p>
          <p className="text-xs text-muted-foreground/70 mt-1">{t("noGlobalAgentsDesc")}</p>
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{tc("name")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("mode")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("model")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("source")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{tc("status")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {agents.map((agent) => (
                <tr key={agent.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {agent.icon ? (
                        <span className="text-base leading-none">{agent.icon}</span>
                      ) : (
                        <Bot className="h-4 w-4 text-muted-foreground" />
                      )}
                      <div>
                        <p className="font-medium text-foreground">{agent.name}</p>
                        {agent.description && (
                          <p className="text-xs text-muted-foreground line-clamp-1">{agent.description}</p>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant="outline">{agent.execution_mode}</Badge>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {agent.model_name || "-"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {agent.cloned_from_username ? `@${agent.cloned_from_username}` : "-"}
                  </td>
                  <td className="px-4 py-3">
                    {agent.status === "published" ? (
                      <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">
                        {tc("active")}
                      </Badge>
                    ) : (
                      <Badge variant="secondary">
                        {tc("inactive")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => setEditTarget(agent)}>
                          <Pencil className="mr-2 h-4 w-4" />
                          {tc("edit")}
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => handleToggle(agent)}>
                          <ToggleLeft className="mr-2 h-4 w-4" />
                          {agent.status === "published" ? t("disableAgent") : t("enableAgent")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(agent)}>
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
      {!isLoading && agents.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t("totalGlobalAgents", { count: total })}</span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              {t("previous")}
            </Button>
            <span>{t("pageOf", { page, pages })}</span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            >
              {tc("next")}
            </Button>
          </div>
        </div>
      )}

      {/* Agent Picker Dialog */}
      <AgentPickerDialog
        open={showPicker}
        onOpenChange={setShowPicker}
        onSuccess={() => {
          setShowPicker(false)
          loadAgents()
        }}
      />

      {/* Edit Dialog */}
      <EditGlobalAgentDialog
        open={!!editTarget}
        onOpenChange={(open) => { if (!open) setEditTarget(null) }}
        agent={editTarget}
        onSuccess={() => {
          setEditTarget(null)
          loadAgents()
        }}
      />

      {/* Delete Confirm */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteDesc", { name: deleteTarget?.name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/* -- Agent Picker Dialog -- */

function AgentPickerDialog({
  open,
  onOpenChange,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const t = useTranslations("admin.globalAgents")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [agents, setAgents] = useState<AdminAgentInfo[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [publishingId, setPublishingId] = useState<string | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const loadAgents = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listAllAgents({ page, size: 10, q: search || undefined })
      setAgents(data.items)
      setPages(data.pages)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, search, tError])

  useEffect(() => {
    if (open) {
      loadAgents()
    }
  }, [open, loadAgents])

  const handleSearchChange = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(value)
      setPage(1)
    }, 300)
  }

  const handlePublish = async (agentId: string) => {
    setPublishingId(agentId)
    try {
      await adminApi.cloneAgentToGlobal(agentId)
      toast.success(t("agentPublishedGlobal"))
      onSuccess()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setPublishingId(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg flex flex-col max-h-[85vh]">
        <DialogHeader>
          <DialogTitle>{t("selectAgent")}</DialogTitle>
          <DialogDescription>{t("selectAgentDesc")}</DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("searchAgentsPlaceholder")}
            className="pl-9"
            onChange={(e) => handleSearchChange(e.target.value)}
          />
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : agents.length === 0 ? (
            <div className="text-center py-8 text-sm text-muted-foreground">
              {t("noAgentsFound")}
            </div>
          ) : (
            <div className="divide-y divide-border">
              {agents.map((agent) => (
                <div key={agent.id} className="flex items-center gap-3 py-3 px-1">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{agent.name}</p>
                    <p className="text-xs text-muted-foreground truncate">
                      @{agent.username ?? agent.email ?? "unknown"}
                      {agent.model_name && ` \u00B7 ${agent.model_name}`}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="shrink-0 text-xs"
                    disabled={publishingId === agent.id}
                    onClick={() => handlePublish(agent.id)}
                  >
                    {publishingId === agent.id ? (
                      <>
                        <Loader2 className="h-3 w-3 animate-spin mr-1" />
                        {t("publishing")}
                      </>
                    ) : (
                      t("publishAsGlobal")
                    )}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Picker pagination */}
        {!isLoading && agents.length > 0 && pages > 1 && (
          <div className="flex items-center justify-center gap-2 pt-2 border-t border-border">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              {t("previous")}
            </Button>
            <span className="text-xs text-muted-foreground">
              {t("pageOf", { page, pages })}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            >
              {tc("next")}
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

/* -- Edit Global Agent Dialog -- */

function EditGlobalAgentDialog({
  open,
  onOpenChange,
  agent,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  agent: AdminGlobalAgentInfo | null
  onSuccess: () => void
}) {
  const t = useTranslations("admin.globalAgents")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [instructions, setInstructions] = useState("")
  const [executionMode, setExecutionMode] = useState<string>("react")
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    if (open && agent) {
      setName(agent.name)
      setDescription(agent.description ?? "")
      setInstructions(agent.instructions ?? "")
      setExecutionMode(agent.execution_mode)
    }
  }, [open, agent])

  const handleSubmit = async () => {
    if (!agent || !name.trim()) return
    setIsSaving(true)
    try {
      const body: Record<string, unknown> = {
        name: name.trim(),
        description: description.trim() || null,
        instructions: instructions.trim() || null,
        execution_mode: executionMode,
      }
      await adminApi.updateGlobalAgent(agent.id, body)
      toast.success(t("globalAgentUpdated"))
      onSuccess()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg flex flex-col max-h-[85vh]">
        <DialogHeader>
          <DialogTitle>{t("editGlobalAgent")}</DialogTitle>
          <DialogDescription>{t("editGlobalAgentDesc")}</DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto">
          {/* Edit limitation notice */}
          <div className="flex gap-2.5 rounded-md border border-border bg-muted/40 px-3 py-2.5 mb-3">
            <Info className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
            <p className="text-xs text-muted-foreground leading-relaxed">{t("editLimitationNotice")}</p>
          </div>

          <div className="grid gap-4 py-2">
            {/* Name */}
            <div className="grid gap-1.5">
              <label className="text-sm font-medium">
                {tc("name")} <span className="text-destructive">*</span>
              </label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={tc("name")}
              />
            </div>

            {/* Description */}
            <div className="grid gap-1.5">
              <label className="text-sm font-medium">{tc("description")}</label>
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
              />
            </div>

            {/* Instructions */}
            <div className="grid gap-1.5">
              <label className="text-sm font-medium">{t("instructions")}</label>
              <Textarea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                placeholder={t("instructionsPlaceholder")}
                rows={4}
              />
            </div>

            {/* Execution Mode */}
            <div className="grid gap-1.5">
              <label className="text-sm font-medium">{t("mode")}</label>
              <Select value={executionMode} onValueChange={setExecutionMode}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="react">ReAct</SelectItem>
                  <SelectItem value="dag">DAG</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSaving}>
            {tc("cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={!name.trim() || isSaving}>
            {isSaving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
            {tc("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
