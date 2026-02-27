"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import { Plus, Loader2 } from "lucide-react"
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
import { KBDetailDialog } from "@/components/kb/kb-detail-dialog"
import { KBUploadDialog } from "@/components/kb/kb-upload-dialog"
import type { KBResponse, KBCreate } from "@/types/kb"

export default function KBPage() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()

  const [knowledgeBases, setKnowledgeBases] = useState<KBResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingKB, setEditingKB] = useState<KBResponse | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [viewingKB, setViewingKB] = useState<KBResponse | null>(null)
  const [uploadingKB, setUploadingKB] = useState<KBResponse | null>(null)

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
      await loadKBs()
    } catch (err) {
      console.error("Failed to save knowledge base:", err)
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
    } catch (err) {
      console.error("Failed to delete knowledge base:", err)
    }
  }

  const handleView = (kb: KBResponse) => setViewingKB(kb)
  const handleUpload = (kb: KBResponse) => setUploadingKB(kb)

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Knowledge Base</h1>
          <p className="text-sm text-muted-foreground">
            Manage your knowledge bases and documents
          </p>
        </div>
        <Button onClick={handleCreate} size="sm" className="gap-1.5">
          <Plus className="h-4 w-4" />
          New KB
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
              No knowledge bases yet. Create your first one to get started.
            </p>
            <Button
              onClick={handleCreate}
              variant="outline"
              size="sm"
              className="mt-4 gap-1.5"
            >
              <Plus className="h-4 w-4" />
              Create Knowledge Base
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {knowledgeBases.map((kb) => (
              <KBCard
                key={kb.id}
                kb={kb}
                onView={handleView}
                onUpload={handleUpload}
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

      {/* Detail Dialog */}
      <KBDetailDialog
        open={viewingKB !== null}
        onOpenChange={(open) => { if (!open) setViewingKB(null) }}
        kb={viewingKB}
      />

      {/* Upload Dialog */}
      <KBUploadDialog
        open={uploadingKB !== null}
        onOpenChange={(open) => { if (!open) setUploadingKB(null) }}
        kb={uploadingKB}
        onUploaded={loadKBs}
      />

      {/* Delete Confirmation */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete knowledge base?</DialogTitle>
            <DialogDescription>
              This knowledge base and all its documents will be permanently deleted. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>Cancel</Button>
            <Button variant="destructive" className="px-6" onClick={confirmDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
