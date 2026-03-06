"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Plus, Loader2, Library, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
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
import { KBFormDialog } from "@/components/kb/kb-form-dialog"
import type { KBResponse, KBCreate } from "@/types/kb"

export default function KBPage() {
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
        <Button onClick={handleCreate} size="sm" className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("newKb")}
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : knowledgeBases.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <p className="text-sm text-muted-foreground">
              {t("emptyState")}
            </p>
            <Button
              onClick={handleCreate}
              variant="outline"
              size="sm"
              className="mt-4 gap-1.5"
            >
              <Plus className="h-4 w-4" />
              {t("createKnowledgeBase")}
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {knowledgeBases.map((kb) => (
              <KBCard
                key={kb.id}
                kb={kb}
                onEdit={handleEdit}
                onDelete={handleDelete}
              />
            ))}
          </div>
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
