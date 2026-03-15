"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { Plus, Loader2, Trash2, Upload, Search, MoreHorizontal, PowerOff, Power } from "lucide-react"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"

// ---- Types ----

interface SensitiveWord {
  id: string
  word: string
  category: string
  is_active: boolean
  created_at: string
}

interface MatchedWord {
  word: string
  category: string
}

const CATEGORY_KEYS = ["general", "political", "violence", "pornography", "advertising", "illegal"] as const

function categoryLabel(category: string, t: (key: string) => string): string {
  const cap = category.charAt(0).toUpperCase() + category.slice(1)
  const key = `cat${cap}`
  // If the category matches a known key, use the i18n translation; otherwise show raw string
  if ((CATEGORY_KEYS as readonly string[]).includes(category)) {
    return t(key)
  }
  return category
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
  const [category, setCategory] = useState("general")
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setWord("")
      setCategory("general")
    }
  }, [open])

  const handleSubmit = async () => {
    if (!word.trim()) return
    setIsSaving(true)
    try {
      await adminApi.createSensitiveWord({
        word: word.trim(),
        category,
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
              {t("wordCategory")}
            </Label>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CATEGORY_KEYS.map((cat) => (
                  <SelectItem key={cat} value={cat}>
                    {t(`cat${cat.charAt(0).toUpperCase() + cat.slice(1)}`)}
                  </SelectItem>
                ))}
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
  const [category, setCategory] = useState("general")
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setText("")
      setCategory("general")
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
        category,
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
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CATEGORY_KEYS.map((cat) => (
                  <SelectItem key={cat} value={cat}>
                    {t(`cat${cat.charAt(0).toUpperCase() + cat.slice(1)}`)}
                  </SelectItem>
                ))}
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
                        className="text-xs bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30"
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

  const handleToggleWord = async (word: SensitiveWord) => {
    try {
      await adminApi.toggleSensitiveWord(word.id, !word.is_active)
      setWords((prev) =>
        prev.map((w) =>
          w.id === word.id ? { ...w, is_active: !word.is_active } : w,
        ),
      )
      toast.success(t("wordToggled"))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

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
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
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
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colWord")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colWordCategory")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colActive")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {words.map((w) => (
                <tr key={w.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{w.word}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {w.category ? categoryLabel(w.category, t) : "\u2014"}
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={w.is_active ? "default" : "secondary"}>
                      {w.is_active ? tc("active") : tc("inactive")}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleToggleWord(w)}>
                          {w.is_active ? <PowerOff className="mr-2 h-4 w-4" /> : <Power className="mr-2 h-4 w-4" />}
                          {w.is_active ? tc("disable") : tc("enable")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(w)}>
                          <Trash2 className="mr-2 h-4 w-4" />
                          {tc("delete")}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
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

  return (
    <div className="space-y-6">
      <SensitiveWordsSection />
    </div>
  )
}
