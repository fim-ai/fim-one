"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Loader2, FilePlus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetFooter,
} from "@/components/ui/sheet"
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
import { kbApi } from "@/lib/api"

interface MdCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  kbId: string
  onCreated: () => void
}

export function MdCreateDialog({
  open,
  onOpenChange,
  kbId,
  onCreated,
}: MdCreateDialogProps) {
  const [filename, setFilename] = useState("")
  const [content, setContent] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)

  const t = useTranslations("kb")
  const tc = useTranslations("common")

  useEffect(() => {
    if (open) {
      setFilename("")
      setContent("")
      setError(null)
    }
  }, [open])

  const isDirty = filename.trim().length > 0 || content.trim().length > 0

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = filename.trim()
    const trimmedContent = content.trim()

    if (!trimmedName || !trimmedContent) return

    // Ensure .md extension
    const finalName = trimmedName.endsWith(".md")
      ? trimmedName
      : `${trimmedName}.md`

    setIsSubmitting(true)
    setError(null)
    try {
      await kbApi.createDocument(kbId, {
        filename: finalName,
        content: trimmedContent,
      })
      onOpenChange(false)
      onCreated()
      toast.success(t("markdownDocumentCreated"))
    } catch (err) {
      const message = err instanceof Error ? err.message : t("failedToCreateDocument")
      setError(message)
      toast.error(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleClose = (open: boolean) => {
    if (!open && isDirty) {
      setShowCloseConfirm(true)
      return
    }
    onOpenChange(open)
  }

  const handleForceClose = () => {
    onOpenChange(false)
  }

  return (
    <>
      <Sheet open={open} onOpenChange={handleClose}>
        <SheetContent
          side="right"
          className="sm:max-w-xl w-full flex flex-col"
          onInteractOutside={(e) => {
            if (isDirty) { e.preventDefault(); setShowCloseConfirm(true) }
          }}
        >
          <SheetHeader>
            <SheetTitle>{t("writeNote")}</SheetTitle>
          </SheetHeader>

          <form onSubmit={handleSubmit} className="flex-1 flex flex-col gap-4 overflow-hidden">
            <div className="space-y-1.5">
              <label htmlFor="md-filename" className="text-sm font-medium">
                {t("noteTitle")} <span className="text-destructive">*</span>
              </label>
              <Input
                id="md-filename"
                value={filename}
                onChange={(e) => setFilename(e.target.value)}
                placeholder={t("noteTitlePlaceholder")}
                required
                disabled={isSubmitting}
              />
            </div>

            <div className="flex-1 flex flex-col gap-1.5 min-h-0">
              <label htmlFor="md-content" className="text-sm font-medium">
                {t("noteContent")} <span className="text-destructive">*</span>
              </label>
              <Textarea
                id="md-content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={t("noteContentPlaceholder")}
                className="font-mono text-sm flex-1 resize-none min-h-0"
                required
                disabled={isSubmitting}
              />
            </div>

            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}

            <SheetFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => handleClose(false)}
                disabled={isSubmitting}
              >
                {tc("cancel")}
              </Button>
              <Button
                type="submit"
                disabled={isSubmitting || !filename.trim() || !content.trim()}
                className="gap-1.5"
              >
                {isSubmitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <FilePlus className="h-4 w-4" />
                )}
                {tc("create")}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      {/* Discard confirmation */}
      <AlertDialog open={showCloseConfirm} onOpenChange={setShowCloseConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("discardUnsavedNoteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("discardUnsavedNoteDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("keepEditing")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleForceClose}
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
