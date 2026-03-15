"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Loader2, Plus, Pencil } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
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
import type { SkillResponse, SkillCreate } from "@/types/skill"

interface SkillFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  skill: SkillResponse | null // null = create mode
  onSubmit: (data: SkillCreate) => Promise<void>
  isSubmitting: boolean
}

export function SkillFormDialog({
  open,
  onOpenChange,
  skill,
  onSubmit,
  isSubmitting,
}: SkillFormDialogProps) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [content, setContent] = useState("")
  const [isActive, setIsActive] = useState(true)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)

  const t = useTranslations("skills")
  const tc = useTranslations("common")

  // Pre-fill when editing or reset when creating
  useEffect(() => {
    if (!open) return
    setShowCloseConfirm(false)
    if (skill) {
      setName(skill.name)
      setDescription(skill.description || "")
      setContent(skill.content)
      setIsActive(skill.is_active)
    } else {
      setName("")
      setDescription("")
      setContent("")
      setIsActive(true)
    }
  }, [open, skill])

  // isDirty: create mode = any field has content; edit mode = any field differs from original
  const isDirty = skill
    ? name !== skill.name ||
      description !== (skill.description || "") ||
      content !== skill.content ||
      isActive !== skill.is_active
    : name.trim().length > 0 || description.trim().length > 0 || content.trim().length > 0 || !isActive

  const handleClose = (open: boolean) => {
    if (!open && isDirty) { setShowCloseConfirm(true); return }
    onOpenChange(open)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) return

    const trimmedDesc = description.trim()
    const data: SkillCreate = {
      name: trimmedName,
      description: trimmedDesc || null,
      content,
      is_active: isActive,
    }

    await onSubmit(data)
  }

  const isEditing = skill !== null
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
            {isEditing ? t("editSkill") : t("createSkill")}
          </DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          <fieldset className="space-y-3">
            {/* Name */}
            <div className="space-y-1.5">
              <label htmlFor="skill-name" className="text-sm font-medium">
                {t("fieldName")} <span className="text-destructive">*</span>
              </label>
              <Input
                id="skill-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("fieldNamePlaceholder")}
                required
              />
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <label htmlFor="skill-description" className="text-sm font-medium">
                {t("fieldDescription")}
              </label>
              <p className="text-xs text-muted-foreground">{t("fieldDescriptionHint")}</p>
              <Input
                id="skill-description"
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("fieldDescriptionPlaceholder")}
              />
            </div>

            {/* Content */}
            <div className="space-y-1.5">
              <label htmlFor="skill-content" className="text-sm font-medium">
                {t("fieldContent")}
              </label>
              <p className="text-xs text-muted-foreground">{t("fieldContentHint")}</p>
              <Textarea
                id="skill-content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={t("fieldContentPlaceholder")}
                className="min-h-[200px] resize-y font-mono text-sm"
              />
            </div>

            {/* Active toggle */}
            <div className="flex items-center justify-between rounded-md border border-border px-3 py-2.5">
              <div>
                <Label htmlFor="skill-active" className="text-sm font-medium cursor-pointer">
                  {tc("active")}
                </Label>
                <p className="text-xs text-muted-foreground mt-0.5">{t("settingActiveHint")}</p>
              </div>
              <Switch
                id="skill-active"
                checked={isActive}
                onCheckedChange={setIsActive}
              />
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
          <AlertDialogTitle>{t("unsavedChangesTitle")}</AlertDialogTitle>
          <AlertDialogDescription>
            {t("unsavedChangesDescription")}
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
