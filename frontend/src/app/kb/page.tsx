"use client"

import { useState, useEffect, useCallback, useMemo, Suspense } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Plus, Library, Trash2, Search } from "lucide-react"
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
import { kbApi, marketApi } from "@/lib/api"
import { KBCard } from "@/components/kb/kb-card"
import { EmptyState } from "@/components/shared/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { KBFormDialog } from "@/components/kb/kb-form-dialog"
import { useScopeFilter } from "@/hooks/use-scope-filter"
import { ScopeFilter } from "@/components/shared/scope-filter"
import { ListPagination, PAGE_SIZE } from "@/components/shared/list-pagination"
import type { KBResponse, KBCreate } from "@/types/kb"

function KBPageInner() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const t = useTranslations("kb")
  const tc = useTranslations("common")
  const { scope, setScope, filterByScope } = useScopeFilter()

  const [knowledgeBases, setKnowledgeBases] = useState<KBResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingKB, setEditingKB] = useState<KBResponse | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [pendingUninstallId, setPendingUninstallId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [currentPage, setCurrentPage] = useState(1)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const loadKBs = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await kbApi.list()
      setKnowledgeBases(data.items)
    } catch (err) {
      console.error("Failed to load knowledge bases:", err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) loadKBs()
  }, [user, loadKBs])

  const handleCreate = () => {
    setEditingKB(null)
    setDialogOpen(true)
  }

  const handleEdit = (kb: KBResponse) => {
    setEditingKB(kb)
    setDialogOpen(true)
  }

  const handleSubmit = async (data: KBCreate) => {
    setIsSubmitting(true)
    try {
      if (editingKB) {
        await kbApi.update(editingKB.id, data)
      } else {
        await kbApi.create(data)
      }
      setDialogOpen(false)
      toast.success(editingKB ? t("knowledgeBaseUpdated") : t("knowledgeBaseCreated"))
      await loadKBs()
    } catch {
      toast.error(t("failedToSaveKb"))
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleDelete = (id: string) => setPendingDeleteId(id)

  const handleUninstall = (id: string) => setPendingUninstallId(id)

  const confirmUninstall = async () => {
    if (!pendingUninstallId) return
    const id = pendingUninstallId
    setPendingUninstallId(null)
    try {
      await marketApi.unsubscribe({ resource_type: "knowledge_base", resource_id: id })
      setKnowledgeBases((prev) => prev.filter((kb) => kb.id !== id))
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
      await kbApi.delete(id)
      setKnowledgeBases((prev) => prev.filter((kb) => kb.id !== id))
      toast.success(t("knowledgeBaseDeleted"))
    } catch {
      toast.error(t("failedToDeleteKb"))
    }
  }

  // Knowledge bases are no longer shareable (Reduce Feature): the publish
  // flow was removed. Unpublish is kept as the escape hatch for reverting
  // any pre-existing org-published KB back to personal.
  const confirmUnpublish = async () => {
    if (!pendingUnpublishId) return
    const id = pendingUnpublishId
    setPendingUnpublishId(null)
    try {
      const updated = await kbApi.unpublish(id)
      setKnowledgeBases((prev) => prev.map((kb) => (kb.id === id ? updated : kb)))
      toast.success(t("unpublishSuccess"))
    } catch {
      toast.error(t("unpublishError"))
    }
  }

  const filteredKBs = useMemo(
    () => (user ? filterByScope(knowledgeBases, user.id) : knowledgeBases),
    [knowledgeBases, user, filterByScope],
  )

  const searchedKBs = useMemo(() => {
    if (!searchQuery.trim()) return filteredKBs
    const q = searchQuery.toLowerCase()
    return filteredKBs.filter(
      (kb) =>
        kb.name.toLowerCase().includes(q) ||
        (kb.description ?? "").toLowerCase().includes(q),
    )
  }, [filteredKBs, searchQuery])

  const totalPages = Math.ceil(searchedKBs.length / PAGE_SIZE)
  const paginatedKBs = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return searchedKBs.slice(start, start + PAGE_SIZE)
  }, [searchedKBs, currentPage])

  // Reset pagination when filters change
  useEffect(() => { setCurrentPage(1) }, [searchQuery, scope])

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Library className="h-5 w-5" />
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={handleCreate} size="sm" className="gap-1.5">
            <Plus className="h-4 w-4" />
            {t("newKb")}
          </Button>
        </div>
      </div>

      {/* Search + Filter bar */}
      {!isLoading && knowledgeBases.length > 0 && (
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
              <Skeleton.KbCard key={i} />
            ))}
          </div>
        ) : knowledgeBases.length === 0 ? (
          <EmptyState
            icon={<Library />}
            title={t("emptyTitle")}
            description={t("emptyDescription")}
            action={
              <Button onClick={handleCreate} variant="outline" size="sm" className="gap-1.5">
                <Plus className="h-4 w-4" />
                {t("createKnowledgeBase")}
              </Button>
            }
          />
        ) : searchedKBs.length === 0 ? (
          <EmptyState
            icon={<Search />}
            title={tc("noResultsTitle")}
            description={tc("noResultsDescription")}
          />
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {paginatedKBs.map((kb) => (
                <KBCard
                  key={kb.id}
                  kb={kb}
                  currentUserId={user.id}
                  onEdit={handleEdit}
                  onDelete={handleDelete}
                  onUninstall={handleUninstall}
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

      {/* Form Dialog */}
      <KBFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        kb={editingKB}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
      />

      {/* Delete Confirmation */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trash2 className="h-4 w-4" />
              {t("deleteKbTitle")}
            </DialogTitle>
            <DialogDescription>
              {t("deleteKbDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>{tc("cancel")}</Button>
            <Button variant="destructive" className="px-6" onClick={confirmDelete}>{tc("delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unpublish Confirmation */}
      <AlertDialog open={pendingUnpublishId !== null} onOpenChange={(open) => { if (!open) setPendingUnpublishId(null) }}>
        <AlertDialogContent className="sm:max-w-sm">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("unpublishTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("unpublishDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={confirmUnpublish}>{tc("confirm")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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

export default function KBPage() {
  return (
    <Suspense fallback={null}>
      <KBPageInner />
    </Suspense>
  )
}
