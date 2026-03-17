"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter, useParams } from "next/navigation"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { ArrowLeft, Plus, MoreHorizontal, Pencil, Trash2, Loader2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/contexts/auth-context"
import { evalApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import type { EvalDatasetResponse, EvalCaseResponse } from "@/types/eval"
import { usePageTitle } from "@/hooks/use-page-title"

function AssertionList({
  assertions,
  onChange,
}: {
  assertions: string[]
  onChange: (v: string[]) => void
}) {
  const t = useTranslations("eval")
  return (
    <div className="space-y-2">
      {assertions.map((a, i) => (
        <div key={i} className="flex gap-2">
          <Input
            value={a}
            onChange={(e) => {
              const next = [...assertions]
              next[i] = e.target.value
              onChange(next)
            }}
            placeholder={t("assertionPlaceholder")}
            className="flex-1"
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onChange(assertions.filter((_, j) => j !== i))}
          >
            <X className="h-4 w-4" />
            <span className="sr-only">{t("removeAssertion")}</span>
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...assertions, ""])}>
        <Plus className="h-4 w-4 mr-2" />
        {t("addAssertion")}
      </Button>
    </div>
  )
}

export default function DatasetCasesPage() {
  const t = useTranslations("eval")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const router = useRouter()
  const params = useParams()
  const datasetId = params.id as string
  const { user, isLoading: authLoading } = useAuth()

  const [dataset, setDataset] = useState<EvalDatasetResponse | null>(null)

  usePageTitle(dataset?.name)

  const [cases, setCases] = useState<EvalCaseResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [editCase, setEditCase] = useState<EvalCaseResponse | null>(null)
  const [deleteCaseId, setDeleteCaseId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // Form state
  const [prompt, setPrompt] = useState("")
  const [expectedBehavior, setExpectedBehavior] = useState("")
  const [assertions, setAssertions] = useState<string[]>([])
  const [promptError, setPromptError] = useState<string | null>(null)
  const [expectedError, setExpectedError] = useState<string | null>(null)

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login")
  }, [authLoading, user, router])

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const [ds, casesData] = await Promise.all([
        evalApi.getDataset(datasetId),
        evalApi.listCases(datasetId),
      ])
      setDataset(ds)
      setCases((casesData as { items?: EvalCaseResponse[] }).items ?? [])
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setLoading(false)
    }
  }, [datasetId, tError])

  useEffect(() => {
    if (user) load()
  }, [user, load])

  function openAddSheet() {
    setEditCase(null)
    setPrompt("")
    setExpectedBehavior("")
    setAssertions([])
    setPromptError(null)
    setExpectedError(null)
    setSheetOpen(true)
  }

  function openEditSheet(c: EvalCaseResponse) {
    setEditCase(c)
    setPrompt(c.prompt)
    setExpectedBehavior(c.expected_behavior)
    setAssertions(c.assertions ?? [])
    setPromptError(null)
    setExpectedError(null)
    setSheetOpen(true)
  }

  async function handleSave() {
    let valid = true
    if (!prompt.trim()) {
      setPromptError(tc("required"))
      valid = false
    }
    if (!expectedBehavior.trim()) {
      setExpectedError(tc("required"))
      valid = false
    }
    if (!valid) return

    setSaving(true)
    try {
      const assertionList = assertions.filter((a) => a.trim())
      if (editCase) {
        await evalApi.updateCase(datasetId, editCase.id, {
          prompt: prompt.trim(),
          expected_behavior: expectedBehavior.trim(),
          assertions: assertionList.length > 0 ? assertionList : null,
        })
        toast.success(tc("success"))
      } else {
        await evalApi.createCase(datasetId, {
          prompt: prompt.trim(),
          expected_behavior: expectedBehavior.trim(),
          assertions: assertionList.length > 0 ? assertionList : null,
        })
        toast.success(tc("success"))
      }
      setSheetOpen(false)
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setSaving(false)
    }
  }

  async function handleDeleteCase(id: string) {
    try {
      await evalApi.deleteCase(datasetId, id)
      toast.success(tc("success"))
      setDeleteCaseId(null)
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      <div className="border-b px-6 py-4">
        <Link
          href="/eval"
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-2 w-fit"
        >
          <ArrowLeft className="h-4 w-4" />
          {t("backToEval")}
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold">{dataset?.name}</h1>
            {dataset?.description && (
              <p className="text-sm text-muted-foreground mt-0.5">{dataset.description}</p>
            )}
          </div>
          <Button size="sm" onClick={openAddSheet}>
            <Plus className="h-4 w-4 mr-2" />
            {t("addCase")}
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {cases.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-12">{t("noCases")}</p>
        ) : (
          <div className="rounded-md border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/40">
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                    {t("promptLabel")}
                  </th>
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                    {t("expectedLabel")}
                  </th>
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                    {t("assertions")}
                  </th>
                  <th className="px-4 py-2 text-right font-medium text-muted-foreground">
                    {tc("actions")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.id} className="border-b last:border-0">
                    <td className="px-4 py-2 max-w-xs">
                      <span className="line-clamp-2">{c.prompt}</span>
                    </td>
                    <td className="px-4 py-2 max-w-xs text-muted-foreground">
                      <span className="line-clamp-2">{c.expected_behavior}</span>
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {c.assertions?.length ?? 0}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" className="h-7 w-7 p-0">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => openEditSheet(c)}>
                            <Pencil className="mr-2 h-4 w-4" />
                            {tc("edit")}
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => setDeleteCaseId(c.id)}
                          >
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
      </div>

      {/* Add/Edit Case Sheet */}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{editCase ? tc("edit") : t("addCase")}</SheetTitle>
            <SheetDescription>
              {editCase ? t("editDataset") : t("addCase")}
            </SheetDescription>
          </SheetHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-1">
              <Label>{t("prompt")}</Label>
              <Textarea
                value={prompt}
                onChange={(e) => {
                  setPrompt(e.target.value)
                  setPromptError(null)
                }}
                placeholder={t("promptPlaceholder")}
                rows={4}
                aria-invalid={!!promptError}
              />
              {promptError && <p className="text-sm text-destructive">{promptError}</p>}
            </div>
            <div className="space-y-1">
              <Label>{t("expectedBehavior")}</Label>
              <Textarea
                value={expectedBehavior}
                onChange={(e) => {
                  setExpectedBehavior(e.target.value)
                  setExpectedError(null)
                }}
                placeholder={t("expectedBehaviorPlaceholder")}
                rows={3}
                aria-invalid={!!expectedError}
              />
              {expectedError && <p className="text-sm text-destructive">{expectedError}</p>}
            </div>
            <div className="space-y-1">
              <Label>{t("assertions")}</Label>
              <AssertionList assertions={assertions} onChange={setAssertions} />
            </div>
          </div>
          <SheetFooter>
            <Button variant="outline" onClick={() => setSheetOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("save")}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      {/* Delete Case AlertDialog */}
      <AlertDialog open={!!deleteCaseId} onOpenChange={(o) => !o && setDeleteCaseId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteCaseTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("deleteCaseDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleteCaseId && handleDeleteCase(deleteCaseId)}>
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
