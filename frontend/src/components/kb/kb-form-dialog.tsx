"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Loader2, Plus, Pencil } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
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
import type { KBCreate, KBResponse } from "@/types/kb"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

interface KBFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  kb: KBResponse | null
  onSubmit: (data: KBCreate) => Promise<void>
  isSubmitting: boolean
}

export function KBFormDialog({
  open,
  onOpenChange,
  kb,
  onSubmit,
  isSubmitting,
}: KBFormDialogProps) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [chunkStrategy, setChunkStrategy] = useState("recursive")
  const [chunkSize, setChunkSize] = useState(1000)
  const [chunkOverlap, setChunkOverlap] = useState(200)
  const [retrievalMode, setRetrievalMode] = useState("hybrid")
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)

  const t = useTranslations("kb")
  const tc = useTranslations("common")

  // Pre-fill when editing or reset when creating
  useEffect(() => {
    if (!open) return
    if (kb) {
      setName(kb.name)
      setDescription(kb.description || "")
      setChunkStrategy(kb.chunk_strategy)
      setChunkSize(kb.chunk_size)
      setChunkOverlap(kb.chunk_overlap)
      setRetrievalMode(kb.retrieval_mode)
    } else {
      setName("")
      setDescription("")
      setChunkStrategy("recursive")
      setChunkSize(1000)
      setChunkOverlap(200)
      setRetrievalMode("hybrid")
    }
  }, [open, kb])

  // isDirty: create mode = any field has content; edit mode = any field differs from original
  const isDirty = kb
    ? name !== kb.name ||
      description !== (kb.description || "") ||
      chunkStrategy !== kb.chunk_strategy ||
      chunkSize !== kb.chunk_size ||
      chunkOverlap !== kb.chunk_overlap ||
      retrievalMode !== kb.retrieval_mode
    : name.trim().length > 0 || description.trim().length > 0

  const handleClose = (open: boolean) => {
    if (!open && isDirty) { setShowCloseConfirm(true); return }
    onOpenChange(open)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) return

    const trimmedDesc = description.trim()
    const data: KBCreate = {
      name: trimmedName,
      description: trimmedDesc || null,
      chunk_strategy: chunkStrategy,
      chunk_size: chunkSize,
      chunk_overlap: chunkOverlap,
      retrieval_mode: retrievalMode,
    }

    await onSubmit(data)
  }

  const isEditing = kb !== null
  return (
    <>
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className="sm:max-w-lg max-h-[90vh] overflow-y-auto"
        onInteractOutside={(e) => {
          if (isDirty) { e.preventDefault(); setShowCloseConfirm(true) }
        }}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isEditing ? <Pencil className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
            {isEditing ? t("editKnowledgeBase") : t("createKnowledgeBase")}
          </DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          {/* ── Section: General ── */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-semibold text-foreground">{t("sectionGeneral")}</legend>

            {/* Name */}
            <div className="space-y-1.5">
              <label htmlFor="kb-name" className="text-sm font-medium">
                {tc("name")} <span className="text-destructive">*</span>
              </label>
              <Input
                id="kb-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("namePlaceholder")}
                required
              />
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <label htmlFor="kb-description" className="text-sm font-medium">
                {tc("description")}
              </label>
              <Textarea
                id="kb-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("descriptionPlaceholder")}
                rows={2}
                className="resize-none"
              />
            </div>
          </fieldset>

          {/* ── Section: Chunking ── */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-semibold text-foreground">
              {t("sectionChunking")}
              {isEditing && <span className="text-xs text-muted-foreground font-normal ml-2">{t("chunkingLockedHint")}</span>}
            </legend>
            {!isEditing && (
              <p className="text-xs text-amber-500">
                {t("chunkingImmutableWarning")}
              </p>
            )}

            {/* Strategy */}
            <div className="space-y-1.5">
              <label htmlFor="kb-chunk-strategy" className="text-sm font-medium">
                {t("chunkStrategy")}
              </label>
              <Select value={chunkStrategy} onValueChange={setChunkStrategy} disabled={isEditing}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="recursive">{t("strategyRecursive")}</SelectItem>
                  <SelectItem value="markdown">{t("strategyMarkdown")}</SelectItem>
                  <SelectItem value="fixed">{t("strategyFixed")}</SelectItem>
                  <SelectItem value="semantic">{t("strategySemantic")}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {chunkStrategy === "recursive" && t("strategyRecursiveDesc")}
                {chunkStrategy === "markdown" && t("strategyMarkdownDesc")}
                {chunkStrategy === "fixed" && t("strategyFixedDesc")}
                {chunkStrategy === "semantic" && t("strategySemanticDesc")}
              </p>
            </div>

            {/* Size & Overlap */}
            {chunkStrategy === "semantic" && (
              <p className="text-xs text-muted-foreground">
                {t("semanticFallbackHint")}
              </p>
            )}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label htmlFor="kb-chunk-size" className="text-sm font-medium">
                  {t("chunkSize")}
                </label>
                <Input
                  id="kb-chunk-size"
                  type="number"
                  min={100}
                  max={6000}
                  value={chunkSize}
                  onChange={(e) => setChunkSize(Math.min(Number(e.target.value), 6000))}
                  disabled={isEditing}
                />
                <p className="text-xs text-muted-foreground">
                  {t("chunkSizeHint")}
                </p>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="kb-chunk-overlap" className="text-sm font-medium">
                  {t("chunkOverlap")}
                </label>
                <Input
                  id="kb-chunk-overlap"
                  type="number"
                  min={0}
                  max={1000}
                  value={chunkOverlap}
                  onChange={(e) => setChunkOverlap(Number(e.target.value))}
                  disabled={isEditing}
                />
              </div>
            </div>
          </fieldset>

          {/* ── Section: Retrieval ── */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-semibold text-foreground">{t("sectionRetrieval")}</legend>

            <div className="space-y-1.5">
              <label htmlFor="kb-retrieval-mode" className="text-sm font-medium">
                {t("retrievalMode")}
              </label>
              <Select value={retrievalMode} onValueChange={setRetrievalMode}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="hybrid">{t("modeHybrid")}</SelectItem>
                  <SelectItem value="dense">{t("modeDense")}</SelectItem>
                  <SelectItem value="fts">{t("modeFts")}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {retrievalMode === "hybrid" && t("modeHybridDesc")}
                {retrievalMode === "dense" && t("modeDenseDesc")}
                {retrievalMode === "fts" && t("modeFtsDesc")}
              </p>
            </div>
          </fieldset>

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => handleClose(false)}
              disabled={isSubmitting}
            >
              {tc("cancel")}
            </Button>
            <Button type="submit" disabled={isSubmitting || !name.trim()}>
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              {isEditing ? t("saveChanges") : tc("create")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>

    <AlertDialog open={showCloseConfirm} onOpenChange={setShowCloseConfirm}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("discardUnsavedTitle")}</AlertDialogTitle>
          <AlertDialogDescription>
            {t("discardUnsavedDescription")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{tc("keepEditing")}</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => onOpenChange(false)}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {t("discardAndClose")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    </>
  )
}
