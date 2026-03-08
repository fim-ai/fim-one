"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations, useLocale } from "next-intl"
import { FileText, ShieldAlert, Plus, Loader2, Trash2, Pencil, Upload, Search } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"

// ---- Types ----

interface PromptTemplate {
  id: string
  name: string
  description: string | null
  content: string
  category: string
  is_active: boolean
  use_count: number
  created_at: string
}

interface SensitiveWord {
  id: string
  word: string
  category: string
  severity: string
  is_active: boolean
  created_at: string
}

interface MatchedWord {
  word: string
  category: string
  severity: string
}

// ---- Sub-section type ----

type SubSection = "templates" | "moderation"

// ---- Template Form Dialog ----

interface TemplateFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  template?: PromptTemplate | null
  onSuccess: () => void
}

function TemplateFormDialog({ open, onOpenChange, template, onSuccess }: TemplateFormDialogProps) {
  const isEdit = !!template
  const t = useTranslations("admin.content")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [content, setContent] = useState("")
  const [category, setCategory] = useState("general")
  const [isSaving, setIsSaving] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<{ name?: string; content?: string }>({})

  useEffect(() => {
    if (open) {
      if (template) {
        setName(template.name)
        setDescription(template.description ?? "")
        setContent(template.content)
        setCategory(template.category)
      } else {
        setName("")
        setDescription("")
        setContent("")
        setCategory("general")
      }
      setShowCloseConfirm(false)
      setFieldErrors({})
    }
  }, [open, template])

  const isDirty =
    !isEdit &&
    (name.trim().length > 0 ||
      description.trim().length > 0 ||
      content.trim().length > 0)

  const handleClose = (nextOpen: boolean) => {
    if (!nextOpen && isDirty) {
      setShowCloseConfirm(true)
      return
    }
    onOpenChange(nextOpen)
  }

  const handleSubmit = async () => {
    const errors: { name?: string; content?: string } = {}
    if (!name.trim()) errors.name = t("templateNameRequired")
    if (!content.trim()) errors.content = t("templateContentRequired")
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setIsSaving(true)
    try {
      if (isEdit && template) {
        await adminApi.updatePromptTemplate(template.id, {
          name: name.trim(),
          description: description.trim() || undefined,
          content: content.trim(),
          category,
        })
        toast.success(t("templateUpdated"))
      } else {
        await adminApi.createPromptTemplate({
          name: name.trim(),
          content: content.trim(),
          description: description.trim() || undefined,
          category,
        })
        toast.success(t("templateCreated"))
      }
      onSuccess()
      onOpenChange(false)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={handleClose}>
        <DialogContent
          className="max-w-lg flex flex-col max-h-[90vh]"
          onInteractOutside={(e) => {
            if (isDirty) {
              e.preventDefault()
              setShowCloseConfirm(true)
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>
              {isEdit ? t("editTemplate") : t("addTemplate")}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto">
            <div className="space-y-4 py-2">
              <div className="space-y-1.5">
                <Label htmlFor="tpl-name">
                  {t("templateName")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="tpl-name"
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value)
                    if (fieldErrors.name) setFieldErrors((prev) => ({ ...prev, name: undefined }))
                  }}
                  aria-invalid={!!fieldErrors.name}
                />
                {fieldErrors.name && (
                  <p className="text-xs text-destructive">{fieldErrors.name}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="tpl-category">{t("templateCategory")}</Label>
                <Select value={category} onValueChange={setCategory}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="general">{t("categoryGeneral")}</SelectItem>
                    <SelectItem value="coding">{t("categoryCoding")}</SelectItem>
                    <SelectItem value="writing">{t("categoryWriting")}</SelectItem>
                    <SelectItem value="analysis">{t("categoryAnalysis")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="tpl-desc">
                  {t("templateDesc")}{" "}
                  <span className="text-xs font-normal text-muted-foreground">({tc("optional")})</span>
                </Label>
                <Textarea
                  id="tpl-desc"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={2}
                  className="resize-none text-sm"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="tpl-content">
                  {t("templateContent")} <span className="text-destructive">*</span>
                </Label>
                <Textarea
                  id="tpl-content"
                  value={content}
                  onChange={(e) => {
                    setContent(e.target.value)
                    if (fieldErrors.content) setFieldErrors((prev) => ({ ...prev, content: undefined }))
                  }}
                  rows={8}
                  className="resize-none text-sm font-mono"
                  aria-invalid={!!fieldErrors.content}
                />
                {fieldErrors.content && (
                  <p className="text-xs text-destructive">{fieldErrors.content}</p>
                )}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => handleClose(false)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleSubmit} disabled={isSaving}>
              {isSaving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
              {isEdit ? tc("save") : tc("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={showCloseConfirm} onOpenChange={setShowCloseConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{tc("unsavedChangesTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {tc("unsavedChanges")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("keepEditing")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => onOpenChange(false)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {tc("discardChanges")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// ---- Word Form Dialog ----

interface WordFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}

function WordFormDialog({ open, onOpenChange, onSuccess }: WordFormDialogProps) {
  const t = useTranslations("admin.content")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [word, setWord] = useState("")
  const [category, setCategory] = useState("")
  const [severity, setSeverity] = useState("warn")
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setWord("")
      setCategory("")
      setSeverity("warn")
    }
  }, [open])

  const handleSubmit = async () => {
    if (!word.trim()) return
    setIsSaving(true)
    try {
      await adminApi.createSensitiveWord({
        word: word.trim(),
        category: category.trim() || undefined,
        severity,
      })
      toast.success(t("wordCreated"))
      onSuccess()
      onOpenChange(false)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("addWord")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="sw-word">
              {t("word")} <span className="text-destructive">*</span>
            </Label>
            <Input
              id="sw-word"
              value={word}
              onChange={(e) => setWord(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="sw-cat">
              {t("wordCategory")}{" "}
              <span className="text-xs font-normal text-muted-foreground">({tc("optional")})</span>
            </Label>
            <Input
              id="sw-cat"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("severity")}</Label>
            <Select value={severity} onValueChange={setSeverity}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="warn">{t("severityWarn")}</SelectItem>
                <SelectItem value="block">{t("severityBlock")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tc("cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={isSaving || !word.trim()}>
            {isSaving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
            {tc("create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---- Batch Import Dialog ----

interface BatchImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}

function BatchImportDialog({ open, onOpenChange, onSuccess }: BatchImportDialogProps) {
  const t = useTranslations("admin.content")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [text, setText] = useState("")
  const [category, setCategory] = useState("")
  const [severity, setSeverity] = useState("warn")
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setText("")
      setCategory("")
      setSeverity("warn")
    }
  }, [open])

  const handleSubmit = async () => {
    const words = text
      .split("\n")
      .map((w) => w.trim())
      .filter(Boolean)
    if (words.length === 0) return
    setIsSaving(true)
    try {
      const res = await adminApi.batchImportWords({
        words,
        category: category.trim() || undefined,
        severity,
      })
      toast.success(t("wordsImported", { count: res.added }))
      onSuccess()
      onOpenChange(false)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("batchTitle")}</DialogTitle>
          <DialogDescription>{t("batchDesc")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={t("batchPlaceholder")}
              rows={8}
              className="resize-none text-sm font-mono"
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("batchCategory")}</Label>
            <Input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("batchSeverity")}</Label>
            <Select value={severity} onValueChange={setSeverity}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="warn">{t("severityWarn")}</SelectItem>
                <SelectItem value="block">{t("severityBlock")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tc("cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={isSaving || !text.trim()}>
            {isSaving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
            {t("batchImport")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---- Test Text Dialog ----

interface TestTextDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function TestTextDialog({ open, onOpenChange }: TestTextDialogProps) {
  const t = useTranslations("admin.content")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [text, setText] = useState("")
  const [isChecking, setIsChecking] = useState(false)
  const [result, setResult] = useState<{ matched: MatchedWord[]; clean: boolean } | null>(null)

  useEffect(() => {
    if (open) {
      setText("")
      setResult(null)
    }
  }, [open])

  const handleCheck = async () => {
    if (!text.trim()) return
    setIsChecking(true)
    try {
      const res = await adminApi.checkText({ text: text.trim() })
      setResult(res)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsChecking(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("testTitle")}</DialogTitle>
          <DialogDescription>{t("testDesc")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <Textarea
            value={text}
            onChange={(e) => { setText(e.target.value); setResult(null) }}
            placeholder={t("testPlaceholder")}
            rows={5}
            className="resize-none text-sm"
          />
          <Button onClick={handleCheck} disabled={isChecking || !text.trim()} className="w-full">
            {isChecking && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
            {t("checkBtn")}
          </Button>
          {result && (
            <div className="rounded-md border border-border p-3">
              {result.clean ? (
                <p className="text-sm text-green-600 dark:text-green-400 font-medium">
                  {t("testClean")}
                </p>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm font-medium text-destructive">
                    {t("testMatched")}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {result.matched.map((m, i) => (
                      <Badge
                        key={i}
                        className={cn(
                          "text-xs",
                          m.severity === "block"
                            ? "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30"
                            : "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
                        )}
                        variant="outline"
                      >
                        {m.word}
                        {m.category && (
                          <span className="text-muted-foreground ml-1">({m.category})</span>
                        )}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tc("close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---- Prompt Templates Sub-section ----

function PromptTemplatesSection() {
  const t = useTranslations("admin.content")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

  const [templates, setTemplates] = useState<PromptTemplate[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [editTarget, setEditTarget] = useState<PromptTemplate | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<PromptTemplate | null>(null)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listPromptTemplates()
      setTemplates(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [tError])

  useEffect(() => { load() }, [load])

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await adminApi.deletePromptTemplate(deleteTarget.id)
      toast.success(t("templateDeleted"))
      setDeleteTarget(null)
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleDateString(locale, {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    } catch {
      return iso
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div />
        <Button size="sm" onClick={() => setShowCreate(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("addTemplate")}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : templates.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-6 text-sm text-muted-foreground text-center">
          {t("noTemplates")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colName")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCategory")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colDesc")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colUsage")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colActive")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCreated")}</th>
                <th className="px-4 py-2.5 w-24" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {templates.map((tpl) => (
                <tr key={tpl.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{tpl.name}</td>
                  <td className="px-4 py-3">
                    <Badge variant="secondary" className="text-xs">{tpl.category}</Badge>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs max-w-[200px] truncate">
                    {tpl.description || "\u2014"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground tabular-nums">{tpl.use_count}</td>
                  <td className="px-4 py-3">
                    <Badge variant={tpl.is_active ? "default" : "secondary"}>
                      {tpl.is_active ? tc("active") : tc("inactive")}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                    {formatDate(tpl.created_at)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        title={tc("edit")}
                        onClick={() => setEditTarget(tpl)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0 text-destructive"
                        title={tc("delete")}
                        onClick={() => setDeleteTarget(tpl)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Delete confirm */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteTemplateTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteTemplateDesc", { name: deleteTarget?.name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Create dialog */}
      <TemplateFormDialog
        open={showCreate}
        onOpenChange={setShowCreate}
        onSuccess={() => { setShowCreate(false); load() }}
      />

      {/* Edit dialog */}
      <TemplateFormDialog
        open={!!editTarget}
        onOpenChange={(open) => { if (!open) setEditTarget(null) }}
        template={editTarget}
        onSuccess={() => { setEditTarget(null); load() }}
      />
    </div>
  )
}

// ---- Sensitive Words Sub-section ----

function SensitiveWordsSection() {
  const t = useTranslations("admin.content")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [words, setWords] = useState<SensitiveWord[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [showAddWord, setShowAddWord] = useState(false)
  const [showBatchImport, setShowBatchImport] = useState(false)
  const [showTestText, setShowTestText] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<SensitiveWord | null>(null)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listSensitiveWords()
      setWords(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [tError])

  useEffect(() => { load() }, [load])

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await adminApi.deleteSensitiveWord(deleteTarget.id)
      toast.success(t("wordDeleted"))
      setDeleteTarget(null)
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div />
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setShowTestText(true)} className="gap-1.5">
            <Search className="h-4 w-4" />
            {t("testText")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setShowBatchImport(true)} className="gap-1.5">
            <Upload className="h-4 w-4" />
            {t("batchImport")}
          </Button>
          <Button size="sm" onClick={() => setShowAddWord(true)} className="gap-1.5">
            <Plus className="h-4 w-4" />
            {t("addWord")}
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : words.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-6 text-sm text-muted-foreground text-center">
          {t("noWords")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colWord")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colWordCategory")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colSeverity")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colActive")}</th>
                <th className="px-4 py-2.5 w-16" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {words.map((w) => (
                <tr key={w.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{w.word}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {w.category || "\u2014"}
                  </td>
                  <td className="px-4 py-3">
                    <Badge
                      variant="outline"
                      className={cn(
                        "text-xs",
                        w.severity === "block"
                          ? "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30"
                          : "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
                      )}
                    >
                      {w.severity === "block" ? t("severityBlock") : t("severityWarn")}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={w.is_active ? "default" : "secondary"}>
                      {w.is_active ? tc("active") : tc("inactive")}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0 text-destructive"
                        title={tc("delete")}
                        onClick={() => setDeleteTarget(w)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Delete confirm */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteWordTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteWordDesc", { word: deleteTarget?.word ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Add word dialog */}
      <WordFormDialog
        open={showAddWord}
        onOpenChange={setShowAddWord}
        onSuccess={() => { setShowAddWord(false); load() }}
      />

      {/* Batch import dialog */}
      <BatchImportDialog
        open={showBatchImport}
        onOpenChange={setShowBatchImport}
        onSuccess={() => { setShowBatchImport(false); load() }}
      />

      {/* Test text dialog */}
      <TestTextDialog
        open={showTestText}
        onOpenChange={setShowTestText}
      />
    </div>
  )
}

// ---- Main Component ----

export function AdminContent() {
  const t = useTranslations("admin.content")

  const [section, setSection] = useState<SubSection>("templates")

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Sub-section toggle */}
      <div className="flex items-center gap-1 rounded-lg bg-muted p-1 w-fit">
        <button
          onClick={() => setSection("templates")}
          className={cn(
            "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
            section === "templates"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <FileText className="h-4 w-4" />
          {t("templatesTab")}
        </button>
        <button
          onClick={() => setSection("moderation")}
          className={cn(
            "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
            section === "moderation"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <ShieldAlert className="h-4 w-4" />
          {t("moderationTab")}
        </button>
      </div>

      <Separator />

      {/* Content */}
      {section === "templates" && <PromptTemplatesSection />}
      {section === "moderation" && <SensitiveWordsSection />}
    </div>
  )
}
