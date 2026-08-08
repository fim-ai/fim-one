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
import { useAuth } from "@/contexts/auth-context"
import { kbApi } from "@/lib/api"
import { KBCard } from "@/components/kb/kb-card"
import { EmptyState } from "@/components/shared/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { KBFormDialog } from "@/components/kb/kb-form-dialog"
import { ListPagination, PAGE_SIZE } from "@/components/shared/list-pagination"
import { StaggerOnce, StaggerItem } from "@/components/ui/fade-in"
import type { KBResponse, KBCreate } from "@/types/kb"

function KBPageInner() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const t = useTranslations("kb")
  const tc = useTranslations("common")

  const [knowledgeBases, setKnowledgeBases] = useState<KBResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingKB, setEditingKB] = useState<KBResponse | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
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

  // The KB list is owned-only (KBs are not shareable), so no scope filter.
  const searchedKBs = useMemo(() => {
    if (!searchQuery.trim()) return knowledgeBases
    const q = searchQuery.toLowerCase()
    return knowledgeBases.filter(
      (kb) =>
        kb.name.toLowerCase().includes(q) ||
        (kb.description ?? "").toLowerCase().includes(q),
    )
  }, [knowledgeBases, searchQuery])

  const totalPages = Math.ceil(searchedKBs.length / PAGE_SIZE)
  const paginatedKBs = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return searchedKBs.slice(start, start + PAGE_SIZE)
  }, [searchedKBs, currentPage])

  // Reset pagination when filters change
  useEffect(() => { setCurrentPage(1) }, [searchQuery])

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

      {/* Search bar */}
      {!isLoading && knowledgeBases.length > 0 && (
        <div className="flex items-center gap-2 px-6 py-2.5 border-b border-border/20 shrink-0">
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
            <StaggerOnce className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {paginatedKBs.map((kb) => (
                <StaggerItem key={kb.id} className="grid">
                  <KBCard
                    kb={kb}
                    currentUserId={user.id}
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                  />
                </StaggerItem>
              ))}
            </StaggerOnce>
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
