"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations, useLocale } from "next-intl"
import { toast } from "sonner"
import {
  Loader2,
  Search,
  MoreHorizontal,
  Eye,
  Power,
  Trash2,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
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
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi, type AdminSkillInfo, type AdminSkillDetail } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"

const PAGE_SIZE = 20

export function AdminSkills() {
  const t = useTranslations("admin.skills")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

  // --- List state ---
  const [skills, setSkills] = useState<AdminSkillInfo[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [search, setSearch] = useState("")
  const [isLoading, setIsLoading] = useState(true)

  // --- Batch selection ---
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  // --- Dialog states ---
  const [deleteTarget, setDeleteTarget] = useState<AdminSkillInfo | null>(null)
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false)
  const [detailTarget, setDetailTarget] = useState<AdminSkillDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)

  // --- Mutation loading ---
  const [isMutating, setIsMutating] = useState(false)

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // --- Load skills ---
  const loadSkills = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await adminApi.listAllSkills({ page, size: PAGE_SIZE, search: search || undefined })
      setSkills(data.items)
      setTotal(data.total)
      setPages(Math.max(1, Math.ceil(data.total / PAGE_SIZE)))
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, search, tError])

  useEffect(() => {
    loadSkills()
  }, [loadSkills])

  // --- Search with debounce ---
  const handleSearchChange = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(value)
      setPage(1)
      setSelectedIds(new Set())
    }, 300)
  }

  // --- Toggle active ---
  const handleToggleActive = async (skill: AdminSkillInfo) => {
    try {
      const result = await adminApi.toggleSkillActive(skill.id, !skill.is_active)
      toast.success(result.is_active ? t("skillEnabled") : t("skillDisabled"))
      await loadSkills()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  // --- Delete skill ---
  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsMutating(true)
    try {
      await adminApi.adminDeleteSkill(deleteTarget.id)
      toast.success(t("skillDeleted"))
      setDeleteTarget(null)
      setSelectedIds((prev) => { const next = new Set(prev); next.delete(deleteTarget.id); return next })
      await loadSkills()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Batch delete ---
  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return
    setIsMutating(true)
    try {
      const result = await adminApi.batchDeleteSkills(Array.from(selectedIds))
      toast.success(t("batchDeleted", { count: result.deleted }))
      setBatchDeleteOpen(false)
      setSelectedIds(new Set())
      await loadSkills()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- View detail ---
  const handleViewDetail = async (skill: AdminSkillInfo) => {
    setDetailOpen(true)
    setDetailLoading(true)
    try {
      const detail = await adminApi.getSkillDetail(skill.id)
      setDetailTarget(detail)
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
      setDetailOpen(false)
    } finally {
      setDetailLoading(false)
    }
  }

  // --- Selection helpers ---
  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    if (selectedIds.size === skills.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(skills.map((s) => s.id)))
    }
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            className="pl-9"
            onChange={(e) => handleSearchChange(e.target.value)}
          />
        </div>
        {selectedIds.size > 0 && (
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setBatchDeleteOpen(true)}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            {t("batchDelete")} ({selectedIds.size})
          </Button>
        )}
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : skills.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noSkills")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left">
                  <Checkbox
                    checked={selectedIds.size === skills.length && skills.length > 0}
                    onCheckedChange={toggleAll}
                  />
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colName")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colOwner")}
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  {t("colAgentsUsing")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colCreated")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colStatus")}
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  {tc("actions")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {skills.map((skill) => (
                <tr key={skill.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3">
                    <Checkbox
                      checked={selectedIds.has(skill.id)}
                      onCheckedChange={() => toggleSelect(skill.id)}
                    />
                  </td>
                  <td className="px-4 py-3 font-medium text-foreground">
                    {skill.name}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {skill.username || skill.email || "--"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {skill.agents_using}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {new Date(skill.created_at).toLocaleDateString(locale)}
                  </td>
                  <td className="px-4 py-3">
                    {skill.is_active ? (
                      <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">
                        {t("active")}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="border-red-500/40 text-red-600 dark:text-red-400">
                        {t("inactive")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleViewDetail(skill)}>
                          <Eye className="mr-2 h-4 w-4" />
                          {t("viewDetail")}
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => handleToggleActive(skill)}>
                          <Power className="mr-2 h-4 w-4" />
                          {skill.is_active ? tc("disable") : tc("enable")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() => setDeleteTarget(skill)}
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

      {/* Pagination */}
      {!isLoading && skills.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t("totalSkills", { count: total })}</span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              {t("previous")}
            </Button>
            <span>{t("pageOf", { page, pages })}</span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            >
              {tc("next")}
            </Button>
          </div>
        </div>
      )}

      {/* --- Detail Sheet --- */}
      <Sheet open={detailOpen} onOpenChange={(open) => { if (!open) { setDetailOpen(false); setDetailTarget(null) } }}>
        <SheetContent className="sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{t("detailTitle")}</SheetTitle>
            {detailTarget && (
              <SheetDescription>
                {t("detailSubtitle", { name: detailTarget.name, owner: detailTarget.username || detailTarget.email || "--" })}
              </SheetDescription>
            )}
          </SheetHeader>
          {detailLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : detailTarget ? (
            <div className="mt-4 space-y-4">
              {detailTarget.description && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">{tc("description")}</p>
                  <p className="text-sm">{detailTarget.description}</p>
                </div>
              )}
              {detailTarget.system_prompt && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">System Prompt</p>
                  <pre className="rounded-md border border-border bg-muted/30 p-3 text-xs whitespace-pre-wrap max-h-[300px] overflow-y-auto">
                    {detailTarget.system_prompt}
                  </pre>
                </div>
              )}
              {detailTarget.content && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Content</p>
                  <pre className="rounded-md border border-border bg-muted/30 p-3 text-xs whitespace-pre-wrap max-h-[300px] overflow-y-auto">
                    {detailTarget.content}
                  </pre>
                </div>
              )}
            </div>
          ) : null}
        </SheetContent>
      </Sheet>

      {/* --- Delete Skill AlertDialog --- */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteConfirm")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteConfirmDesc", {
                name: deleteTarget?.name || "",
                owner: deleteTarget?.username || deleteTarget?.email || "",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={handleDelete}
              disabled={isMutating}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- Batch Delete AlertDialog --- */}
      <AlertDialog open={batchDeleteOpen} onOpenChange={setBatchDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("batchDeleteConfirm", { count: selectedIds.size })}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("batchDeleteConfirmDesc")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={handleBatchDelete}
              disabled={isMutating}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
