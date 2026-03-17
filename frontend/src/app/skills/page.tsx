"use client"

import { useState, useEffect, useCallback, Suspense, useMemo } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Plus, BookOpen, Search, LayoutTemplate } from "lucide-react"
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
import { useAuth } from "@/contexts/auth-context"
import { skillApi, marketApi, orgApi } from "@/lib/api"
import type { UserOrg } from "@/lib/api"
import { SkillCard } from "@/components/skills/skill-card"
import { SkillFormDialog } from "@/components/skills/skill-form-dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { PublishDialog } from "@/components/shared/publish-dialog"
import type { SkillResponse, SkillCreate } from "@/types/skill"
import { useScopeFilter } from "@/hooks/use-scope-filter"
import { ScopeFilter } from "@/components/shared/scope-filter"
import { EmptyState } from "@/components/shared/empty-state"
import { SkillTemplateGallery } from "@/components/skills/skill-template-gallery"
import { ListPagination, PAGE_SIZE } from "@/components/shared/list-pagination"

function SkillsPageInner() {
  const t = useTranslations("skills")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const { scope, setScope, filterByScope } = useScopeFilter()

  const [skills, setSkills] = useState<SkillResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [pendingUninstallId, setPendingUninstallId] = useState<string | null>(null)
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)
  const [publishOrgId, setPublishOrgId] = useState<string>("")
  const [userOrgs, setUserOrgs] = useState<UserOrg[]>([])
  const [orgsLoading, setOrgsLoading] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingSkill, setEditingSkill] = useState<SkillResponse | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [templateGalleryOpen, setTemplateGalleryOpen] = useState(false)
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

  const loadSkills = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await skillApi.list()
      setSkills(data.items as SkillResponse[])
    } catch (err) {
      console.error("Failed to load skills:", err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) loadSkills()
  }, [user, loadSkills])

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

  const handleResubmit = async (id: string) => {
    try {
      const updated = await skillApi.resubmit(id)
      setSkills((prev) => prev.map((s) => (s.id === id ? updated : s)))
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
      await marketApi.unsubscribe({ resource_type: "skill", resource_id: id })
      setSkills((prev) => prev.filter((s) => s.id !== id))
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
      await skillApi.delete(id)
      setSkills((prev) => prev.filter((s) => s.id !== id))
      toast.success(t("skillDeleted"))
    } catch {
      toast.error(t("skillDeleteFailed"))
    }
  }

  const confirmPublish = async () => {
    if (!pendingPublishId || !publishOrgId) return
    const id = pendingPublishId
    setPendingPublishId(null)
    try {
      const updated = await skillApi.publish(id, {
        scope: "org",
        org_id: publishOrgId,
      })
      setSkills((prev) => prev.map((s) => (s.id === id ? updated : s)))
      toast.success(t("skillPublished"))
    } catch {
      toast.error(t("skillPublishFailed"))
    }
  }

  const confirmUnpublish = async () => {
    if (!pendingUnpublishId) return
    const id = pendingUnpublishId
    setPendingUnpublishId(null)
    try {
      const updated = await skillApi.unpublish(id)
      setSkills((prev) => prev.map((s) => (s.id === id ? updated : s)))
      toast.success(t("skillUnpublished"))
    } catch {
      toast.error(t("skillUnpublishFailed"))
    }
  }

  const handleCreate = () => {
    setEditingSkill(null)
    setDialogOpen(true)
  }

  const handleEdit = (skill: SkillResponse) => {
    setEditingSkill(skill)
    setDialogOpen(true)
  }

  const handleSubmit = async (data: SkillCreate) => {
    setIsSubmitting(true)
    try {
      if (editingSkill) {
        const updated = await skillApi.update(editingSkill.id, data)
        setSkills((prev) => prev.map((s) => (s.id === editingSkill.id ? updated : s)))
        if ((updated as unknown as Record<string, unknown>).publish_status_reverted) {
          toast.info(t("publishStatusReverted"))
        } else {
          toast.success(t("skillSaved"))
        }
      } else {
        const created = await skillApi.create(data)
        setSkills((prev) => [created, ...prev])
        toast.success(t("skillCreated"))
      }
      setDialogOpen(false)
    } catch {
      toast.error(editingSkill ? t("skillSaveFailed") : t("skillCreateFailed"))
    } finally {
      setIsSubmitting(false)
    }
  }

  // Find selected org for review notice
  const selectedOrg = publishOrgId
    ? userOrgs.find((o) => o.id === publishOrgId)
    : null

  const filteredSkills = useMemo(
    () => (user ? filterByScope(skills, user.id) : skills),
    [skills, scope, user, filterByScope],
  )

  const searchedSkills = useMemo(() => {
    if (!searchQuery.trim()) return filteredSkills
    const q = searchQuery.toLowerCase()
    return filteredSkills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.description ?? "").toLowerCase().includes(q),
    )
  }, [filteredSkills, searchQuery])

  const totalPages = Math.ceil(searchedSkills.length / PAGE_SIZE)
  const paginatedSkills = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return searchedSkills.slice(start, start + PAGE_SIZE)
  }, [searchedSkills, currentPage])

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <BookOpen className="h-5 w-5" />
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={() => setTemplateGalleryOpen(true)}
          >
            <LayoutTemplate className="h-4 w-4" />
            {t("fromTemplate")}
          </Button>
          <Button size="sm" className="gap-1.5" onClick={handleCreate}>
            <Plus className="h-4 w-4" />
            {t("newSkill")}
          </Button>
        </div>
      </div>

      {/* Search + Filter bar */}
      {!isLoading && skills.length > 0 && (
        <div className="flex items-center gap-2 px-6 py-2.5 border-b border-border/20 shrink-0">
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
        ) : skills.length === 0 ? (
          <EmptyState
            icon={<BookOpen />}
            title={t("emptyTitle")}
            description={t("emptyDescription")}
            action={
              <Button variant="outline" size="sm" className="gap-1.5" onClick={handleCreate}>
                <Plus className="h-4 w-4" />
                {t("createSkill")}
              </Button>
            }
          />
        ) : searchedSkills.length === 0 ? (
          <EmptyState
            icon={<Search />}
            title={tc("noResultsTitle")}
            description={tc("noResultsDescription")}
          />
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {paginatedSkills.map((skill) => (
                <SkillCard
                  key={skill.id}
                  skill={skill}
                  currentUserId={user.id}
                  onEdit={handleEdit}
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

      <SkillFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        skill={editingSkill}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
      />

      <SkillTemplateGallery
        open={templateGalleryOpen}
        onOpenChange={setTemplateGalleryOpen}
        onCreated={loadSkills}
      />

      {/* Delete Confirmation */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BookOpen className="h-4 w-4" />
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

      {/* Publish Confirmation — shared dialog with Marketplace + Org targets */}
      <PublishDialog
        open={pendingPublishId !== null}
        onOpenChange={(open) => { if (!open) setPendingPublishId(null) }}
        title={t("publishDialogTitle")}
        description={t("publishDialogDescription")}
        orgs={userOrgs}
        orgsLoading={orgsLoading}
        selectedOrgId={publishOrgId}
        onOrgChange={setPublishOrgId}
        requiresReview={!!selectedOrg?.review_skills}
        noOrgsText={t("publishNoOrgs")}
        selectOrgPlaceholder={t("publishSelectOrg")}
        onConfirm={confirmPublish}
        resourceType="skill"
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
    </div>
  )
}

export default function SkillsPage() {
  return (
    <Suspense fallback={null}>
      <SkillsPageInner />
    </Suspense>
  )
}
