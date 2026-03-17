"use client"

import { useState, useEffect, useCallback, Suspense, useMemo } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Plus, Bot, Trash2, Search, LayoutTemplate } from "lucide-react"
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
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { useAuth } from "@/contexts/auth-context"
import { agentApi, marketApi, orgApi } from "@/lib/api"
import type { UserOrg } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { AgentCard } from "@/components/agents/agent-card"
import { PublishDialog } from "@/components/shared/publish-dialog"
import { EmptyState } from "@/components/shared/empty-state"
import { ListPagination, PAGE_SIZE } from "@/components/shared/list-pagination"
import { Skeleton } from "@/components/ui/skeleton"
import type { AgentResponse } from "@/types/agent"
import { useScopeFilter } from "@/hooks/use-scope-filter"
import { ScopeFilter } from "@/components/shared/scope-filter"
import { AgentTemplateGallery } from "@/components/agents/agent-template-gallery"
import { usePageTitle } from "@/hooks/use-page-title"

function AgentsPageInner() {
  const t = useTranslations("agents")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const { scope, setScope, filterByScope } = useScopeFilter()

  usePageTitle(t("title"))

  const [agents, setAgents] = useState<AgentResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState("")
  const [currentPage, setCurrentPage] = useState(1)
  const [templateGalleryOpen, setTemplateGalleryOpen] = useState(false)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [pendingUninstallId, setPendingUninstallId] = useState<string | null>(null)
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)
  const [publishOrgId, setPublishOrgId] = useState<string>("")
  const [userOrgs, setUserOrgs] = useState<UserOrg[]>([])
  const [orgsLoading, setOrgsLoading] = useState(false)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const loadAgents = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await agentApi.list()
      setAgents((data.items as AgentResponse[]).filter((a) => !a.is_builder))
    } catch (err) {
      console.error("Failed to load agents:", err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) loadAgents()
  }, [user, loadAgents])

  const handleDelete = (id: string) => setPendingDeleteId(id)
  const handlePublish = (id: string) => {
    setPendingPublishId(id)
    setPublishOrgId("")
    setOrgsLoading(true)
    orgApi.list().then((orgs) => {
      setUserOrgs(orgs)
      if (orgs.length > 0) setPublishOrgId(orgs[0].id)
    }).catch(() => {}).finally(() => setOrgsLoading(false))
  }
  const handleUnpublish = (id: string) => setPendingUnpublishId(id)

  const handleToggleActive = async (id: string, isActive: boolean) => {
    try {
      const updated = await agentApi.toggleActive(id, isActive)
      setAgents((prev) => prev.map((a) => (a.id === id ? updated : a)))
      toast.success(isActive ? t("agentEnabled") : t("agentDisabled"))
    } catch {
      toast.error(t("agentToggleFailed"))
    }
  }

  const handleResubmit = async (id: string) => {
    try {
      const updated = await agentApi.resubmit(id)
      setAgents((prev) => prev.map((a) => (a.id === id ? updated : a)))
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
      await marketApi.unsubscribe({ resource_type: "agent", resource_id: id })
      setAgents((prev) => prev.filter((a) => a.id !== id))
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
      await agentApi.delete(id)
      setAgents((prev) => prev.filter((a) => a.id !== id))
      toast.success(t("agentDeleted"))
    } catch {
      toast.error(t("agentDeleteFailed"))
    }
  }

  const confirmPublish = async () => {
    if (!pendingPublishId || !publishOrgId) return
    const id = pendingPublishId
    setPendingPublishId(null)
    try {
      const updated = await agentApi.publish(id, {
        scope: "org",
        org_id: publishOrgId,
      })
      setAgents((prev) => prev.map((a) => (a.id === id ? updated : a)))
      toast.success(t("agentPublished"))
    } catch {
      toast.error(t("agentPublishFailed"))
    }
  }

  const confirmUnpublish = async () => {
    if (!pendingUnpublishId) return
    const id = pendingUnpublishId
    setPendingUnpublishId(null)
    try {
      const updated = await agentApi.unpublish(id)
      setAgents((prev) => prev.map((a) => (a.id === id ? updated : a)))
      toast.success(t("agentUnpublished"))
    } catch {
      toast.error(t("agentUnpublishFailed"))
    }
  }

  // Find selected org for review notice
  const selectedOrg = publishOrgId
    ? userOrgs.find((o) => o.id === publishOrgId)
    : null

  const filteredAgents = useMemo(
    () => (user ? filterByScope(agents, user.id) : agents),
    [agents, scope, user, filterByScope],
  )

  const searchedAgents = useMemo(() => {
    if (!searchQuery.trim()) return filteredAgents
    const q = searchQuery.toLowerCase()
    return filteredAgents.filter(a =>
      a.name.toLowerCase().includes(q) ||
      (a.description ?? '').toLowerCase().includes(q)
    )
  }, [filteredAgents, searchQuery])

  const totalPages = Math.ceil(searchedAgents.length / PAGE_SIZE)
  const paginatedAgents = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return searchedAgents.slice(start, start + PAGE_SIZE)
  }, [searchedAgents, currentPage])

  useEffect(() => { setCurrentPage(1) }, [searchQuery, scope])

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Bot className="h-5 w-5" />
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={() => setTemplateGalleryOpen(true)}
          >
            <LayoutTemplate className="h-4 w-4" />
            {t("fromTemplate")}
          </Button>
          <Button size="sm" className="gap-1.5" asChild>
            <Link href="/agents/new">
              <Plus className="h-4 w-4" />
              {t("newAgent")}
            </Link>
          </Button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {!isLoading && agents.length > 0 && (
          <div className="mb-4 flex items-center gap-3">
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
        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton.AgentCard key={i} />
            ))}
          </div>
        ) : agents.length === 0 ? (
          <EmptyState
            icon={<Bot />}
            title={t("emptyTitle")}
            description={t("emptyDescription")}
            action={
              <Button variant="outline" size="sm" className="gap-1.5" asChild>
                <Link href="/agents/new">
                  <Plus className="h-4 w-4" />
                  {t("createAgent")}
                </Link>
              </Button>
            }
          />
        ) : searchedAgents.length === 0 ? (
          <EmptyState
            icon={<Search />}
            title={tc("noResultsTitle")}
            description={tc("noResultsDescription")}
          />
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {paginatedAgents.map((agent) => (
                <AgentCard
                  key={agent.id}
                  agent={agent}
                  currentUserId={user.id}
                  onDelete={handleDelete}
                  onPublish={handlePublish}
                  onUnpublish={handleUnpublish}
                  onUninstall={handleUninstall}
                  onResubmit={handleResubmit}
                />
              ))}
            </div>
            <ListPagination
              currentPage={currentPage}
              totalPages={totalPages}
              onPageChange={setCurrentPage}
            />
          </>
        )}
      </div>

      {/* Delete Confirmation */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trash2 className="h-4 w-4" />
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

      {/* Publish Confirmation — shared dialog with org/marketplace toggle */}
      <PublishDialog
        open={pendingPublishId !== null}
        onOpenChange={(open) => { if (!open) setPendingPublishId(null) }}
        title={t("publishDialogTitle")}
        description={t("publishDialogDescription")}
        orgs={userOrgs}
        orgsLoading={orgsLoading}
        selectedOrgId={publishOrgId}
        onOrgChange={setPublishOrgId}
        requiresReview={!!selectedOrg?.review_agents}
        noOrgsText={t("publishNoOrgs")}
        selectOrgPlaceholder={t("publishSelectOrg")}
        onConfirm={confirmPublish}
        resourceType="agent"
        resourceId={pendingPublishId ?? undefined}
      />

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
      <AgentTemplateGallery
        open={templateGalleryOpen}
        onOpenChange={setTemplateGalleryOpen}
      />
    </div>
  )
}

export default function AgentsPage() {
  return (
    <Suspense fallback={null}>
      <AgentsPageInner />
    </Suspense>
  )
}
