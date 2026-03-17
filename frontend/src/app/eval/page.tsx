"use client"

import { useState, useEffect, useCallback, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { FlaskConical, MoreHorizontal, Plus, Loader2, Trash2, Eye, Pencil, Table2, Play } from "lucide-react"
import { EmptyState } from "@/components/shared/empty-state"
import { Button } from "@/components/ui/button"
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAuth } from "@/contexts/auth-context"
import { evalApi, agentApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import type { EvalDatasetResponse, EvalRunResponse } from "@/types/eval"
import type { AgentResponse } from "@/types/agent"
import { cn } from "@/lib/utils"

// Status badge helper
function RunStatusBadge({ status }: { status: string }) {
  const t = useTranslations("eval")
  const variants: Record<string, string> = {
    pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
    running: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    completed: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
    failed: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  }
  const labels: Record<string, string> = {
    pending: t("statusPending"),
    running: t("statusRunning"),
    completed: t("statusCompleted"),
    failed: t("statusFailed"),
  }
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        variants[status] ?? variants.pending,
      )}
    >
      {status === "running" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
      {labels[status] ?? status}
    </span>
  )
}

function EvalPageContent() {
  const t = useTranslations("eval")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const router = useRouter()
  const searchParams = useSearchParams()
  const { user, isLoading: authLoading } = useAuth()

  const activeTab = searchParams.get("tab") ?? "datasets"

  // Datasets state
  const [datasets, setDatasets] = useState<EvalDatasetResponse[]>([])
  const [datasetsLoading, setDatasetsLoading] = useState(true)
  const [createDatasetOpen, setCreateDatasetOpen] = useState(false)
  const [editDataset, setEditDataset] = useState<EvalDatasetResponse | null>(null)
  const [deleteDatasetId, setDeleteDatasetId] = useState<string | null>(null)
  const [dsName, setDsName] = useState("")
  const [dsDescription, setDsDescription] = useState("")
  const [dsSaving, setDsSaving] = useState(false)
  const [dsFieldError, setDsFieldError] = useState<string | null>(null)

  // Runs state
  const [runs, setRuns] = useState<EvalRunResponse[]>([])
  const [runsLoading, setRunsLoading] = useState(true)
  const [startRunOpen, setStartRunOpen] = useState(false)
  const [deleteRunId, setDeleteRunId] = useState<string | null>(null)
  const [agents, setAgents] = useState<AgentResponse[]>([])
  const [runAgentId, setRunAgentId] = useState("")
  const [runDatasetId, setRunDatasetId] = useState("")
  const [runStarting, setRunStarting] = useState(false)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) router.replace("/login")
  }, [authLoading, user, router])

  const loadDatasets = useCallback(async () => {
    try {
      setDatasetsLoading(true)
      const data = await evalApi.listDatasets()
      setDatasets((data as { items?: EvalDatasetResponse[] }).items ?? [])
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setDatasetsLoading(false)
    }
  }, [tError])

  const loadRuns = useCallback(async () => {
    try {
      setRunsLoading(true)
      const data = await evalApi.listRuns()
      setRuns((data as { items?: EvalRunResponse[] }).items ?? [])
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setRunsLoading(false)
    }
  }, [tError])

  const loadAgents = useCallback(async () => {
    try {
      const data = await agentApi.list()
      setAgents(
        ((data as { items?: AgentResponse[] }).items ?? []).filter(
          (a) => !(a as AgentResponse & { is_builder?: boolean }).is_builder,
        ),
      )
    } catch {
      // ignore — agents list is best-effort
    }
  }, [])

  useEffect(() => {
    if (user) {
      loadDatasets()
      loadRuns()
    }
  }, [user, loadDatasets, loadRuns])

  // Auto-refresh runs every 3s if any are pending/running
  useEffect(() => {
    const hasActive = runs.some((r) => r.status === "pending" || r.status === "running")
    if (!hasActive || activeTab !== "runs") return
    const interval = setInterval(loadRuns, 3000)
    return () => clearInterval(interval)
  }, [runs, activeTab, loadRuns])

  function switchTab(tab: string) {
    const params = new URLSearchParams(searchParams.toString())
    if (tab === "datasets") {
      params.delete("tab")
    } else {
      params.set("tab", tab)
    }
    router.replace(`/eval${params.toString() ? `?${params.toString()}` : ""}`)
  }

  // Dataset create/edit
  async function handleSaveDataset() {
    if (!dsName.trim()) {
      setDsFieldError(tc("required"))
      return
    }
    setDsSaving(true)
    try {
      if (editDataset) {
        await evalApi.updateDataset(editDataset.id, {
          name: dsName.trim(),
          description: dsDescription.trim() || null,
        })
        toast.success(tc("success"))
        setEditDataset(null)
      } else {
        await evalApi.createDataset({
          name: dsName.trim(),
          description: dsDescription.trim() || null,
        })
        toast.success(tc("success"))
        setCreateDatasetOpen(false)
      }
      loadDatasets()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setDsSaving(false)
    }
  }

  async function handleDeleteDataset(id: string) {
    try {
      await evalApi.deleteDataset(id)
      toast.success(tc("success"))
      setDeleteDatasetId(null)
      loadDatasets()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  async function handleStartRun() {
    if (!runAgentId || !runDatasetId) return
    setRunStarting(true)
    try {
      const run = await evalApi.createRun({ agent_id: runAgentId, dataset_id: runDatasetId })
      toast.success(t("statusRunning"))
      setStartRunOpen(false)
      router.push(`/eval/runs/${run.id}`)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setRunStarting(false)
    }
  }

  async function handleDeleteRun(id: string) {
    try {
      await evalApi.deleteRun(id)
      toast.success(tc("success"))
      setDeleteRunId(null)
      loadRuns()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="border-b px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <FlaskConical className="h-5 w-5 text-muted-foreground" />
              <h1 className="text-xl font-semibold">{t("title")}</h1>
            </div>
            <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
          </div>
          <div className="flex items-center gap-2">
            {activeTab === "datasets" && (
              <Button size="sm" onClick={() => { setDsName(""); setDsDescription(""); setDsFieldError(null); setCreateDatasetOpen(true) }}>
                <Plus className="h-4 w-4 mr-2" />
                {t("newDataset")}
              </Button>
            )}
            {activeTab === "runs" && (
              <Button size="sm" onClick={() => { setRunAgentId(""); setRunDatasetId(""); loadAgents(); setStartRunOpen(true) }}>
                <Plus className="h-4 w-4 mr-2" />
                {t("newRun")}
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b px-6">
        <nav className="flex gap-4 -mb-px">
          {(["datasets", "runs"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => switchTab(tab)}
              className={cn(
                "py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5",
                activeTab === tab
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {tab === "datasets" ? <Table2 className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
              {tab === "datasets" ? t("datasetsTab") : t("runsTab")}
            </button>
          ))}
        </nav>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        {activeTab === "datasets" && (
          <div>
            {datasetsLoading ? (
              <div className="flex justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : datasets.length === 0 ? (
              <EmptyState
                icon={<FlaskConical />}
                title={t("noDatasetsTitle")}
                description={t("noDatasetsDescription")}
              />
            ) : (
              <div className="rounded-md border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-muted/40">
                      <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                        {t("datasetName")}
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                        {t("datasetDescription")}
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                        {tc("details")}
                      </th>
                      <th className="px-4 py-2 text-right font-medium text-muted-foreground">
                        {tc("actions")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {datasets.map((ds) => (
                      <tr key={ds.id} className="border-b last:border-0">
                        <td className="px-4 py-2 font-medium">{ds.name}</td>
                        <td className="px-4 py-2 text-muted-foreground max-w-xs truncate">
                          {ds.description ?? "—"}
                        </td>
                        <td className="px-4 py-2 text-muted-foreground">{ds.case_count}</td>
                        <td className="px-4 py-2 text-right">
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" className="h-7 w-7 p-0">
                                <MoreHorizontal className="h-4 w-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem asChild>
                                <Link href={`/eval/datasets/${ds.id}`}>
                                  <Eye className="mr-2 h-4 w-4" />
                                  {t("viewCases")}
                                </Link>
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onClick={() => {
                                  setEditDataset(ds)
                                  setDsName(ds.name)
                                  setDsDescription(ds.description ?? "")
                                  setDsFieldError(null)
                                }}
                              >
                                <Pencil className="mr-2 h-4 w-4" />
                                {t("editDataset")}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                variant="destructive"
                                onClick={() => setDeleteDatasetId(ds.id)}
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
          </div>
        )}

        {activeTab === "runs" && (
          <div>
            {runsLoading ? (
              <div className="flex justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : runs.length === 0 ? (
              <EmptyState
                icon={<FlaskConical />}
                title={t("noRunsTitle")}
                description={t("noRunsDescription")}
              />
            ) : (
              <div className="rounded-md border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-muted/40">
                      <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                        {t("agent")}
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                        {t("dataset")}
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                        {tc("status")}
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                        {t("passRate")}
                      </th>
                      <th className="px-4 py-2 text-right font-medium text-muted-foreground">
                        {tc("actions")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((run) => (
                      <tr key={run.id} className="border-b last:border-0">
                        <td className="px-4 py-2 font-medium">
                          {run.agent_name ?? run.agent_id}
                        </td>
                        <td className="px-4 py-2 text-muted-foreground">
                          {run.dataset_name ?? run.dataset_id}
                        </td>
                        <td className="px-4 py-2">
                          <RunStatusBadge status={run.status} />
                        </td>
                        <td className="px-4 py-2 text-muted-foreground">
                          {run.total_cases > 0
                            ? `${run.passed_cases}/${run.total_cases}`
                            : "—"}
                        </td>
                        <td className="px-4 py-2 text-right">
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" className="h-7 w-7 p-0">
                                <MoreHorizontal className="h-4 w-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem asChild>
                                <Link href={`/eval/runs/${run.id}`}>
                                  <Eye className="mr-2 h-4 w-4" />
                                  {t("viewResults")}
                                </Link>
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                variant="destructive"
                                onClick={() => setDeleteRunId(run.id)}
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
          </div>
        )}
      </div>

      {/* Create Dataset Dialog */}
      <Dialog open={createDatasetOpen} onOpenChange={setCreateDatasetOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("newDataset")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <Label>{t("datasetName")}</Label>
              <Input
                value={dsName}
                onChange={(e) => {
                  setDsName(e.target.value)
                  setDsFieldError(null)
                }}
                placeholder={t("namePlaceholder")}
                aria-invalid={!!dsFieldError}
              />
              {dsFieldError && <p className="text-sm text-destructive">{dsFieldError}</p>}
            </div>
            <div className="space-y-1">
              <Label>{t("datasetDescription")}</Label>
              <Input
                value={dsDescription}
                onChange={(e) => setDsDescription(e.target.value)}
                placeholder={t("descriptionPlaceholder")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateDatasetOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleSaveDataset} disabled={dsSaving}>
              {dsSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Dataset Dialog */}
      <Dialog open={!!editDataset} onOpenChange={(o) => !o && setEditDataset(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("editDataset")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <Label>{t("datasetName")}</Label>
              <Input
                value={dsName}
                onChange={(e) => {
                  setDsName(e.target.value)
                  setDsFieldError(null)
                }}
                aria-invalid={!!dsFieldError}
              />
              {dsFieldError && <p className="text-sm text-destructive">{dsFieldError}</p>}
            </div>
            <div className="space-y-1">
              <Label>{t("datasetDescription")}</Label>
              <Input
                value={dsDescription}
                onChange={(e) => setDsDescription(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditDataset(null)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleSaveDataset} disabled={dsSaving}>
              {dsSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Dataset AlertDialog */}
      <AlertDialog open={!!deleteDatasetId} onOpenChange={(o) => !o && setDeleteDatasetId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteDatasetTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("deleteDatasetDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteDatasetId && handleDeleteDataset(deleteDatasetId)}
            >
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Start Eval Run Dialog */}
      <Dialog open={startRunOpen} onOpenChange={setStartRunOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("newRun")}</DialogTitle>
            <DialogDescription>{t("subtitle")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <Label>{t("selectAgent")}</Label>
              <Select value={runAgentId} onValueChange={setRunAgentId}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("selectAgent")} />
                </SelectTrigger>
                <SelectContent>
                  {agents.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t("selectDataset")}</Label>
              <Select value={runDatasetId} onValueChange={setRunDatasetId}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("selectDataset")} />
                </SelectTrigger>
                <SelectContent>
                  {datasets.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <p className="text-xs text-muted-foreground mt-1">{t("executionMode")}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setStartRunOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleStartRun}
              disabled={runStarting || !runAgentId || !runDatasetId}
            >
              {runStarting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("startEval")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Run AlertDialog */}
      <AlertDialog open={!!deleteRunId} onOpenChange={(o) => !o && setDeleteRunId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteRunTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("deleteRunDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleteRunId && handleDeleteRun(deleteRunId)}>
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

export default function EvalPage() {
  return (
    <Suspense>
      <EvalPageContent />
    </Suspense>
  )
}
