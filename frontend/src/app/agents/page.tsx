"use client"

import { useState, useEffect, useCallback, Suspense, useMemo } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Plus, Loader2, Bot, Trash2, Clock, Search, LayoutTemplate, Layers, KeyRound } from "lucide-react"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/contexts/auth-context"
import { agentApi, marketApi, orgApi } from "@/lib/api"
import type { UserOrg, DependencyManifest } from "@/lib/api"
import { AgentCard } from "@/components/agents/agent-card"
import { EmptyState } from "@/components/shared/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import type { AgentResponse } from "@/types/agent"
import { useScopeFilter } from "@/hooks/use-scope-filter"
import { ScopeFilter } from "@/components/shared/scope-filter"
import { AgentTemplateGallery } from "@/components/agents/agent-template-gallery"

function AgentsPageInner() {
  const t = useTranslations("agents")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const tm = useTranslations("market")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const { scope, setScope, filterByScope } = useScopeFilter()

  const [agents, setAgents] = useState<AgentResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [templateGalleryOpen, setTemplateGalleryOpen] = useState(false)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)
  const [publishOrgId, setPublishOrgId] = useState<string>("")
  const [userOrgs, setUserOrgs] = useState<UserOrg[]>([])
  const [orgsLoading, setOrgsLoading] = useState(false)
  const [deps, setDeps] = useState<DependencyManifest | null>(null)

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
    marketApi.dependencies({ resource_type: "agent", resource_id: id })
      .then(res => setDeps(res.data))
      .catch(() => setDeps(null))
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

  const handleUninstall = async (id: string) => {
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
          <div className="mb-4">
            <ScopeFilter value={scope} onChange={setScope} />
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
        ) : filteredAgents.length === 0 ? (
          <EmptyState
            icon={<Search />}
            title={tc("noResultsTitle")}
            description={tc("noResultsDescription")}
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filteredAgents.map((agent) => (
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
                  {selectedOrg?.review_agents && (
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
