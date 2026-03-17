"use client"

import { useState, useEffect, useCallback, useRef, useMemo, Suspense } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Plus, GitBranch, Upload, Loader2, Clock, Search, LayoutTemplate, Layers, KeyRound } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/contexts/auth-context"
import { workflowApi, marketApi, orgApi } from "@/lib/api"
import type { UserOrg, DependencyManifest } from "@/lib/api"
import { EmptyState } from "@/components/shared/empty-state"
import { WorkflowCard } from "@/components/workflows/workflow-card"
import { TemplateGalleryDialog } from "@/components/workflows/template-gallery-dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { useScopeFilter } from "@/hooks/use-scope-filter"
import { ScopeFilter } from "@/components/shared/scope-filter"
import { ListPagination, PAGE_SIZE } from "@/components/shared/list-pagination"
import type { WorkflowResponse } from "@/types/workflow"
import { usePageTitle } from "@/hooks/use-page-title"

function WorkflowsPageInner() {
  const t = useTranslations("workflows")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const tm = useTranslations("market")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const { scope, setScope, filterByScope } = useScopeFilter()

  usePageTitle(t("title"))

  const [workflows, setWorkflows] = useState<WorkflowResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [pendingUninstallId, setPendingUninstallId] = useState<string | null>(null)
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)
  const [publishOrgId, setPublishOrgId] = useState<string>("")
  const [userOrgs, setUserOrgs] = useState<UserOrg[]>([])
  const [orgsLoading, setOrgsLoading] = useState(false)
  const [deps, setDeps] = useState<DependencyManifest | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [showTemplateGallery, setShowTemplateGallery] = useState(false)
  const [isCreatingFromTemplate, setIsCreatingFromTemplate] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const [currentPage, setCurrentPage] = useState(1)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  // Reset pagination when filters change
  useEffect(() => { setCurrentPage(1) }, [searchQuery, scope])

  const loadWorkflows = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await workflowApi.list()
      setWorkflows(data.items as WorkflowResponse[])
    } catch (err) {
      console.error("Failed to load workflows:", err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) loadWorkflows()
  }, [user, loadWorkflows])

  const handleDelete = (id: string) => setPendingDeleteId(id)
  const handlePublish = (id: string) => {
    setPendingPublishId(id)
    setPublishOrgId("")
    setOrgsLoading(true)
    orgApi.list().then((orgs) => {
      setUserOrgs(orgs)
      if (orgs.length > 0) setPublishOrgId(orgs[0].id)
    }).catch(() => {}).finally(() => setOrgsLoading(false))
    marketApi.dependencies({ resource_type: "workflow", resource_id: id })
      .then(res => setDeps(res.data))
      .catch(() => setDeps(null))
  }
  const handleUnpublish = (id: string) => setPendingUnpublishId(id)

  const handleResubmit = async (id: string) => {
    try {
      const updated = await workflowApi.resubmit(id)
      setWorkflows((prev) => prev.map((w) => (w.id === id ? updated : w)))
      toast.success(to("resubmitSuccess"))
    } catch {
      toast.error(to("resubmitFailed"))
    }
  }

  const handleUninstall = (id: string) => setPendingUninstallId(id)

  const confirmUninstall = async () => {
    if (!pendingUninstallId) return
    const id = pendingUninstallId
    setPendingUninstallId(null)
    try {
      await marketApi.unsubscribe({ resource_type: "workflow", resource_id: id })
      setWorkflows((prev) => prev.filter((w) => w.id !== id))
      toast.success(tc("uninstalled"))
    } catch {
      toast.error(tc("error"))
    }
  }

  const confirmDelete = async () => {
    if (!pendingDeleteId) return
    const id = pendingDeleteId
    setPendingDeleteId(null)
    try {
      await workflowApi.delete(id)
      setWorkflows((prev) => prev.filter((w) => w.id !== id))
      toast.success(t("workflowDeleted"))
    } catch {
      toast.error(t("workflowDeleteFailed"))
    }
  }

  const confirmPublish = async () => {
    if (!pendingPublishId || !publishOrgId) return
    const id = pendingPublishId
    setPendingPublishId(null)
    try {
      const updated = await workflowApi.publish(id, {
        scope: "org",
        org_id: publishOrgId,
      })
      setWorkflows((prev) => prev.map((w) => (w.id === id ? updated : w)))
      toast.success(t("workflowPublished"))
    } catch {
      toast.error(t("workflowPublishFailed"))
    }
  }

  const confirmUnpublish = async () => {
    if (!pendingUnpublishId) return
    const id = pendingUnpublishId
    setPendingUnpublishId(null)
    try {
      const updated = await workflowApi.unpublish(id)
      setWorkflows((prev) => prev.map((w) => (w.id === id ? updated : w)))
      toast.success(t("workflowUnpublished"))
    } catch {
      toast.error(t("workflowUnpublishFailed"))
    }
  }

  const handleCreateBlank = async () => {
    setIsCreatingFromTemplate(true)
    try {
      const workflow = await workflowApi.create({
        name: t("editorUntitled"),
        blueprint: {
          nodes: [
            { id: "start_1", type: "start", position: { x: 100, y: 200 }, data: { variables: [] } },
            { id: "end_1", type: "end", position: { x: 600, y: 200 }, data: { output_mapping: {} } },
          ],
          edges: [],
          viewport: { x: 0, y: 0, zoom: 1 },
        },
      })
      router.push(`/workflows/${workflow.id}`)
    } catch {
      toast.error(t("workflowCreateFailed"))
    } finally {
      setIsCreatingFromTemplate(false)
    }
  }

  const handleExport = async (id: string) => {
    try {
      const data = await workflowApi.export(id)
      const wf = workflows.find((w) => w.id === id)
      const slug = (wf?.name || "workflow")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `${slug}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error(t("workflowExportFailed"))
    }
  }

  const handleDuplicate = async (id: string) => {
    try {
      const duplicated = await workflowApi.duplicate(id)
      setWorkflows((prev) => [duplicated, ...prev])
      toast.success(t("workflowDuplicated"))
    } catch {
      toast.error(t("workflowDuplicateFailed"))
    }
  }

  const handleImport = () => {
    fileInputRef.current?.click()
  }

  const onFileImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      // Support both envelope format { format, workflow } and legacy bare { name, blueprint }
      const fileData =
        parsed.format === "fim_workflow_v1" || parsed.workflow || parsed.data
          ? parsed
          : { data: parsed }
      const result = await workflowApi.import(fileData)
      setWorkflows((prev) => [result.workflow, ...prev])
      if (result.unresolved_references.length > 0) {
        toast.warning(
          t("importUnresolvedWarning", { count: result.unresolved_references.length })
        )
      } else {
        toast.success(t("workflowImported"))
      }
    } catch {
      toast.error(t("workflowImportFailed"))
    }
    // Reset file input
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  // Find selected org for review notice
  const selectedOrg = publishOrgId
    ? userOrgs.find((o) => o.id === publishOrgId)
    : null

  // Filter workflows by scope + search
  const filteredWorkflows = useMemo(() => {
    let result = user ? filterByScope(workflows, user.id) : workflows
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      result = result.filter(
        (w) =>
          w.name.toLowerCase().includes(q) ||
          (w.description ?? "").toLowerCase().includes(q),
      )
    }
    return result
  }, [workflows, searchQuery, scope, user, filterByScope])

  const totalPages = Math.ceil(filteredWorkflows.length / PAGE_SIZE)
  const paginatedWorkflows = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return filteredWorkflows.slice(start, start + PAGE_SIZE)
  }, [filteredWorkflows, currentPage])

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="gap-1.5" onClick={handleImport}>
            <Upload className="h-3.5 w-3.5" />
            {tc("import")}
          </Button>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setShowTemplateGallery(true)}>
            <LayoutTemplate className="h-3.5 w-3.5" />
            {t("fromTemplate")}
          </Button>
          <Button size="sm" className="gap-1.5" onClick={handleCreateBlank} disabled={isCreatingFromTemplate}>
            {isCreatingFromTemplate ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {t("newWorkflow")}
          </Button>
        </div>
      </div>

      {/* Hidden file input for import */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json"
        className="hidden"
        onChange={onFileImport}
      />

      {/* Search + Filter bar */}
      {!isLoading && workflows.length > 0 && (
        <div className="flex items-center gap-3 px-6 py-2.5 border-b border-border/20 shrink-0">
          <ScopeFilter value={scope} onChange={setScope} />
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              className="h-8 pl-8 text-xs"
              placeholder={tc("searchPlaceholder")}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton.AgentCard key={i} />
            ))}
          </div>
        ) : workflows.length === 0 ? (
          <EmptyState
            icon={<GitBranch />}
            title={t("emptyTitle")}
            description={t("emptyDescription")}
            action={
              <Button variant="outline" size="sm" className="gap-1.5" onClick={handleCreateBlank} disabled={isCreatingFromTemplate}>
                {isCreatingFromTemplate ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                {t("createWorkflow")}
              </Button>
            }
          />
        ) : filteredWorkflows.length === 0 ? (
          <EmptyState
            icon={<Search />}
            title={tc("noResultsTitle")}
            description={t("noSearchResultsDescription")}
          />
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {paginatedWorkflows.map((workflow) => (
                <WorkflowCard
                  key={workflow.id}
                  workflow={workflow}
                  currentUserId={user.id}
                  onDelete={handleDelete}
                  onExport={handleExport}
                  onDuplicate={handleDuplicate}
                  onPublish={handlePublish}
                  onUnpublish={handleUnpublish}
                  onUninstall={handleUninstall}
                  onResubmit={handleResubmit}
                />
              ))}
            </div>
            <ListPagination currentPage={currentPage} totalPages={totalPages} onPageChange={setCurrentPage} />
          </>
        )}
      </div>

      {/* Delete Confirmation */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <GitBranch className="h-4 w-4" />
              {t("deleteDialogTitle")}
            </DialogTitle>
            <DialogDescription>
              {t("deleteDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>{tc("cancel")}</Button>
            <Button variant="destructive" className="px-6" onClick={confirmDelete}>{tc("delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Publish Confirmation */}
      <Dialog open={pendingPublishId !== null} onOpenChange={(open) => { if (!open) setPendingPublishId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("publishDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("publishDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              {orgsLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                </div>
              ) : userOrgs.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("publishNoOrgs")}</p>
              ) : (
                <>
                  <Select value={publishOrgId} onValueChange={setPublishOrgId}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder={t("publishSelectOrg")} />
                    </SelectTrigger>
                    <SelectContent>
                      {userOrgs.map((org) => (
                        <SelectItem key={org.id} value={org.id}>{org.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  {/* Review notice */}
                  {selectedOrg?.review_workflows && (
                    <div className="flex items-center gap-2 text-sm text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 p-2 rounded-md">
                      <Clock className="h-4 w-4 shrink-0" />
                      <span>{to("publishRequiresReview")}</span>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Dependency preview */}
            {deps && (deps.content_deps.length > 0 || deps.connection_deps.length > 0) && (
              <div className="space-y-2 border-t pt-3">
                <p className="text-xs font-medium text-muted-foreground">{tm("dependenciesLabel")}</p>
                {deps.content_deps.length > 0 && (
                  <div className="flex items-start gap-2 text-sm text-muted-foreground">
                    <Layers className="h-4 w-4 shrink-0 mt-0.5" />
                    <span>{tm("contentDepsIncluded", { items: deps.content_deps.map(d => d.resource_name).join(", ") })}</span>
                  </div>
                )}
                {deps.connection_deps.length > 0 && (
                  <div className="flex items-start gap-2 text-sm text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 p-2 rounded-md">
                    <KeyRound className="h-4 w-4 shrink-0 mt-0.5" />
                    <span>{tm("connectionDepsRequired", { items: deps.connection_deps.map(d => d.resource_name).join(", ") })}</span>
                  </div>
                )}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingPublishId(null)}>{tc("cancel")}</Button>
            <Button
              className="px-6"
              onClick={confirmPublish}
              disabled={orgsLoading || userOrgs.length === 0 || !publishOrgId}
            >
              {tc("publish")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unpublish Confirmation */}
      <Dialog open={pendingUnpublishId !== null} onOpenChange={(open) => { if (!open) setPendingUnpublishId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("unpublishDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("unpublishDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingUnpublishId(null)}>{tc("cancel")}</Button>
            <Button variant="secondary" className="px-6" onClick={confirmUnpublish}>{tc("unpublish")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Uninstall Confirmation */}
      <AlertDialog open={pendingUninstallId !== null} onOpenChange={(open) => { if (!open) setPendingUninstallId(null) }}>
        <AlertDialogContent className="sm:max-w-sm">
          <AlertDialogHeader>
            <AlertDialogTitle>{tc("uninstallConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {tc("uninstallConfirmDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={confirmUninstall}
            >
              {tc("uninstall")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Template Gallery */}
      <TemplateGalleryDialog
        open={showTemplateGallery}
        onOpenChange={setShowTemplateGallery}
      />
    </div>
  )
}

export default function WorkflowsPage() {
  return (
    <Suspense fallback={null}>
      <WorkflowsPageInner />
    </Suspense>
  )
}
