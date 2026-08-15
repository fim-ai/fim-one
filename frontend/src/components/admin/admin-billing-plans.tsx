"use client"

import { useState, useEffect, useCallback } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  AlertCircle,
  Loader2,
  Plus,
  MoreHorizontal,
  Pencil,
  Eye,
  Power,
  PowerOff,
  Trash2,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi, ApiError } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { formatTokens } from "@/lib/format-tokens"
import type { AdminBillingPlan } from "@/types/admin"

// ---------------------------------------------------------------------------
// Form state — used for both Create and Edit dialogs.
// ---------------------------------------------------------------------------

interface PlanFormState {
  slug: string
  name: string
  monthly_token_quota: string
  stripe_price_id: string
  description: string
  features: string  // newline-separated
  sort_order: string
  is_active: boolean
}

const EMPTY_FORM: PlanFormState = {
  slug: "",
  name: "",
  monthly_token_quota: "",
  stripe_price_id: "",
  description: "",
  features: "",
  sort_order: "0",
  is_active: true,
}

const SLUG_RE = /^[a-z0-9_-]+$/

function planToForm(p: AdminBillingPlan): PlanFormState {
  return {
    slug: p.slug,
    name: p.name,
    monthly_token_quota: String(p.monthly_token_quota),
    stripe_price_id: p.stripe_price_id ?? "",
    description: p.description ?? "",
    features: p.features.join("\n"),
    sort_order: String(p.sort_order),
    is_active: p.is_active,
  }
}

/**
 * Resolve the price string to render in the admin table.
 *
 * Stripe is the source of truth — the backend pre-formats
 * ``price_display`` from ``stripe.Price.retrieve`` so this matches the
 * user-facing card byte-for-byte. We fall through to the legacy
 * ``price_cents`` override only when Stripe couldn't be reached and no
 * live price was returned, so the cell is never empty.
 */
function formatPriceDisplay(p: AdminBillingPlan, freeLabel: string): string {
  if (p.price_display) return p.price_display
  if (!p.stripe_price_id) return freeLabel
  if (p.price_cents !== null && p.price_cents !== undefined) {
    return `$${(p.price_cents / 100).toFixed(2)}`
  }
  return "—"
}

export function AdminBillingPlans() {
  const t = useTranslations("admin.billing.plans")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [plans, setPlans] = useState<AdminBillingPlan[]>([])
  const [isLoading, setIsLoading] = useState(true)

  // Dialog targets
  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<AdminBillingPlan | null>(null)
  const [viewTarget, setViewTarget] = useState<AdminBillingPlan | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AdminBillingPlan | null>(null)
  const [toggleTarget, setToggleTarget] = useState<AdminBillingPlan | null>(null)

  // Dirty-state guard for Create/Edit dialogs.
  const [dirtyConfirm, setDirtyConfirm] = useState<"create" | "edit" | null>(null)

  // Form state
  const [form, setForm] = useState<PlanFormState>(EMPTY_FORM)
  const [initialForm, setInitialForm] = useState<PlanFormState>(EMPTY_FORM)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const [isMutating, setIsMutating] = useState(false)

  // ----- Load plans -----
  const load = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await adminApi.listBillingPlans()
      setPlans(data)
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // ----- Form helpers -----
  const setField = <K extends keyof PlanFormState>(key: K, value: PlanFormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
    setFieldErrors((prev) => {
      if (!prev[key]) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const isDirty = (() => {
    return (
      form.slug !== initialForm.slug ||
      form.name !== initialForm.name ||
      form.monthly_token_quota !== initialForm.monthly_token_quota ||
      form.stripe_price_id !== initialForm.stripe_price_id ||
      form.description !== initialForm.description ||
      form.features !== initialForm.features ||
      form.sort_order !== initialForm.sort_order ||
      form.is_active !== initialForm.is_active
    )
  })()

  const resetForm = () => {
    setForm(EMPTY_FORM)
    setInitialForm(EMPTY_FORM)
    setFieldErrors({})
  }

  // ----- Validation -----
  const validateCreate = (): boolean => {
    const errs: Record<string, string> = {}
    if (!form.slug.trim()) errs.slug = t("errors.slugRequired")
    else if (!SLUG_RE.test(form.slug.trim())) errs.slug = t("errors.slugInvalid")
    if (!form.name.trim()) errs.name = t("errors.nameRequired")
    const quota = Number(form.monthly_token_quota)
    if (!Number.isFinite(quota) || quota < 0 || !Number.isInteger(quota)) {
      errs.monthly_token_quota = t("errors.quotaInvalid")
    }
    setFieldErrors(errs)
    return Object.keys(errs).length === 0
  }

  const validateEdit = (): boolean => {
    const errs: Record<string, string> = {}
    if (!form.name.trim()) errs.name = t("errors.nameRequired")
    const quota = Number(form.monthly_token_quota)
    if (!Number.isFinite(quota) || quota < 0 || !Number.isInteger(quota)) {
      errs.monthly_token_quota = t("errors.quotaInvalid")
    }
    setFieldErrors(errs)
    return Object.keys(errs).length === 0
  }

  // ----- Open/close handlers with dirty guard -----
  const openCreate = () => {
    setForm(EMPTY_FORM)
    setInitialForm(EMPTY_FORM)
    setFieldErrors({})
    setCreateOpen(true)
  }

  const openEdit = (plan: AdminBillingPlan) => {
    const f = planToForm(plan)
    setForm(f)
    setInitialForm(f)
    setFieldErrors({})
    setEditTarget(plan)
  }

  const handleCreateClose = (open: boolean) => {
    if (open) {
      setCreateOpen(true)
      return
    }
    if (isDirty) {
      setDirtyConfirm("create")
      return
    }
    setCreateOpen(false)
    resetForm()
  }

  const handleEditClose = (open: boolean) => {
    if (open) return
    if (isDirty) {
      setDirtyConfirm("edit")
      return
    }
    setEditTarget(null)
    resetForm()
  }

  const confirmDiscard = () => {
    if (dirtyConfirm === "create") setCreateOpen(false)
    if (dirtyConfirm === "edit") setEditTarget(null)
    resetForm()
    setDirtyConfirm(null)
  }

  // ----- Mutations -----
  const handleCreate = async () => {
    if (!validateCreate()) return
    setIsMutating(true)
    try {
      const features = form.features
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean)
      await adminApi.createBillingPlan({
        slug: form.slug.trim(),
        name: form.name.trim(),
        monthly_token_quota: Number(form.monthly_token_quota),
        stripe_price_id: form.stripe_price_id.trim() || null,
        description: form.description.trim() || null,
        features,
        sort_order: Number(form.sort_order) || 0,
        is_active: form.is_active,
      })
      toast.success(t("planCreated"))
      setCreateOpen(false)
      resetForm()
      await load()
    } catch (err: unknown) {
      // Map known field-level conflicts inline; everything else → toast.
      if (err instanceof ApiError && err.errorCode === "billing_plan_slug_taken") {
        setFieldErrors((p) => ({ ...p, slug: t("errors.slugTaken") }))
      } else if (err instanceof ApiError && err.errorCode === "billing_plan_stripe_price_taken") {
        setFieldErrors((p) => ({ ...p, stripe_price_id: t("errors.stripePriceTaken") }))
      } else {
        toast.error(getErrorMessage(err, tError))
      }
    } finally {
      setIsMutating(false)
    }
  }

  const handleEdit = async () => {
    if (!editTarget) return
    if (!validateEdit()) return
    setIsMutating(true)
    try {
      const features = form.features
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean)
      await adminApi.updateBillingPlan(editTarget.id, {
        name: form.name.trim(),
        monthly_token_quota: Number(form.monthly_token_quota),
        stripe_price_id: form.stripe_price_id.trim() || null,
        description: form.description.trim() || null,
        features,
        sort_order: Number(form.sort_order) || 0,
        is_active: form.is_active,
      })
      toast.success(t("planUpdated"))
      setEditTarget(null)
      resetForm()
      await load()
    } catch (err: unknown) {
      if (err instanceof ApiError && err.errorCode === "billing_plan_stripe_price_taken") {
        setFieldErrors((p) => ({ ...p, stripe_price_id: t("errors.stripePriceTaken") }))
      } else {
        toast.error(getErrorMessage(err, tError))
      }
    } finally {
      setIsMutating(false)
    }
  }

  const handleToggleActive = async () => {
    if (!toggleTarget) return
    setIsMutating(true)
    try {
      await adminApi.updateBillingPlan(toggleTarget.id, {
        is_active: !toggleTarget.is_active,
      })
      toast.success(t("planUpdated"))
      setToggleTarget(null)
      await load()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleteError(null)
    setIsMutating(true)
    try {
      await adminApi.deleteBillingPlan(deleteTarget.id)
      toast.success(t("planDeleted"))
      setDeleteTarget(null)
      await load()
    } catch (err: unknown) {
      if (err instanceof ApiError && err.errorCode === "billing_plan_has_active_subscriptions") {
        const args = err.errorArgs as { count?: number } | undefined
        const count = args?.count ?? deleteTarget.active_subscription_count
        setDeleteError(t("deleteBlocked", { count }))
      } else if (err instanceof ApiError && err.errorCode === "billing_plan_is_default") {
        setDeleteError(t("deleteDefaultBlocked"))
      } else {
        toast.error(getErrorMessage(err, tError))
        setDeleteTarget(null)
      }
    } finally {
      setIsMutating(false)
    }
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Button onClick={openCreate} className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("createPlan")}
        </Button>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : plans.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noPlansFound")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("columns.slug")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("columns.name")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("columns.price")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("columns.quota")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("columns.status")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("columns.subsCount")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {plans.map((p) => (
                <tr key={p.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-mono text-xs text-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      {p.slug}
                      {p.is_default && (
                        <Badge variant="outline" className="font-sans text-[10px]">
                          {t("defaultPlan")}
                        </Badge>
                      )}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-foreground">{p.name}</td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {p.stripe_price_id && !p.price_display ? (
                      <span
                        className="inline-flex items-center gap-1 text-destructive"
                        title={t("stripePriceMissingHint")}
                      >
                        <AlertCircle className="h-3.5 w-3.5" />
                        {t("stripePriceMissing")}
                      </span>
                    ) : (
                      formatPriceDisplay(p, t("freeTier"))
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                    {formatTokens(p.monthly_token_quota)}{" "}
                    <span className="text-muted-foreground/60 text-xs">{t("tokensSuffix")}</span>
                  </td>
                  <td className="px-4 py-3">
                    {p.is_active ? (
                      <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">
                        {tc("enabled")}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="border-red-500/40 text-red-600 dark:text-red-400">
                        {tc("disabled")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{p.active_subscription_count}</td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => setViewTarget(p)}>
                          <Eye className="mr-2 h-4 w-4" />
                          {t("actions.view")}
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => openEdit(p)}>
                          <Pencil className="mr-2 h-4 w-4" />
                          {t("actions.edit")}
                        </DropdownMenuItem>
                        {!p.is_default && (
                          <DropdownMenuItem onClick={() => setToggleTarget(p)}>
                            {p.is_active ? (
                              <>
                                <PowerOff className="mr-2 h-4 w-4" />
                                {t("actions.disable")}
                              </>
                            ) : (
                              <>
                                <Power className="mr-2 h-4 w-4" />
                                {t("actions.enable")}
                              </>
                            )}
                          </DropdownMenuItem>
                        )}
                        {!p.is_default && (
                          <>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() => {
                                setDeleteError(null)
                                setDeleteTarget(p)
                              }}
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              {t("actions.delete")}
                            </DropdownMenuItem>
                          </>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* --- Create dialog --- */}
      <Dialog open={createOpen} onOpenChange={handleCreateClose}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("createTitle")}</DialogTitle>
            <DialogDescription>{t("createDescription")}</DialogDescription>
          </DialogHeader>
          <PlanFormFields
            form={form}
            setField={setField}
            fieldErrors={fieldErrors}
            slugReadOnly={false}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => handleCreateClose(false)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleCreate} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Edit dialog --- */}
      <Dialog open={editTarget !== null} onOpenChange={handleEditClose}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("editTitle")}</DialogTitle>
            <DialogDescription>{t("editDescription")}</DialogDescription>
          </DialogHeader>
          <PlanFormFields
            form={form}
            setField={setField}
            fieldErrors={fieldErrors}
            slugReadOnly={true}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => handleEditClose(false)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleEdit} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Dirty-state confirmation (sibling, not nested) --- */}
      <AlertDialog
        open={dirtyConfirm !== null}
        onOpenChange={(open) => { if (!open) setDirtyConfirm(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{tc("unsavedChangesTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{tc("unsavedChanges")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("keepEditing")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={confirmDiscard}
            >
              {tc("discardChanges")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- View details dialog --- */}
      <Dialog open={viewTarget !== null} onOpenChange={(open) => { if (!open) setViewTarget(null) }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("viewTitle")}</DialogTitle>
            <DialogDescription>{t("viewDescription")}</DialogDescription>
          </DialogHeader>
          {viewTarget && (
            <div className="space-y-3 py-2 text-sm">
              <DetailRow label={t("fields.slug")} value={<span className="font-mono">{viewTarget.slug}</span>} />
              <DetailRow label={t("fields.name")} value={viewTarget.name} />
              <DetailRow
                label={t("fields.priceCents")}
                value={
                  viewTarget.price_display
                    ? viewTarget.price_display
                    : !viewTarget.stripe_price_id
                      ? t("freeTier")
                      : "—"
                }
              />
              <DetailRow label={t("fields.monthlyTokenQuota")} value={formatTokens(viewTarget.monthly_token_quota)} />
              <DetailRow label={t("fields.stripePriceId")} value={viewTarget.stripe_price_id ?? "—"} />
              <DetailRow label={t("fields.description")} value={viewTarget.description ?? "—"} />
              <DetailRow label={t("fields.features")} value={
                viewTarget.features.length > 0 ? (
                  <ul className="list-disc pl-5 space-y-0.5">
                    {viewTarget.features.map((f, i) => <li key={i}>{f}</li>)}
                  </ul>
                ) : "—"
              } />
              <DetailRow label={t("fields.sortOrder")} value={String(viewTarget.sort_order)} />
              <DetailRow label={t("fields.isActive")} value={viewTarget.is_active ? tc("enabled") : tc("disabled")} />
              <DetailRow label={t("columns.subsCount")} value={String(viewTarget.active_subscription_count)} />
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setViewTarget(null)}>
              {tc("close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Toggle active AlertDialog --- */}
      <AlertDialog
        open={toggleTarget !== null}
        onOpenChange={(open) => { if (!open) setToggleTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {toggleTarget?.is_active ? t("actions.disable") : t("actions.enable")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {toggleTarget?.name}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleToggleActive} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {toggleTarget?.is_active ? tc("disable") : tc("enable")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- Delete AlertDialog --- */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null)
            setDeleteError(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("deleteConfirmTitle", { slug: deleteTarget?.slug ?? "" })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteConfirmDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <p className="text-sm text-destructive">{deleteError}</p>
          )}
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
    </div>
  )
}

// ---------------------------------------------------------------------------
// Inline form fields component — shared by Create + Edit dialogs.
// ---------------------------------------------------------------------------

interface PlanFormFieldsProps {
  form: PlanFormState
  setField: <K extends keyof PlanFormState>(key: K, value: PlanFormState[K]) => void
  fieldErrors: Record<string, string>
  slugReadOnly: boolean
}

function PlanFormFields({ form, setField, fieldErrors, slugReadOnly }: PlanFormFieldsProps) {
  const t = useTranslations("admin.billing.plans")
  // The Free plan's quota is the canonical "default for unsubscribed
  // users" — its source of truth is the System Settings page's
  // ``Default Monthly Token Quota`` field, which both the admin
  // settings PATCH and the activation flow keep in sync. We lock the
  // input here so the operator edits one knob, not two — and the
  // backend also ignores the field on the slug='free' plan as
  // defence-in-depth.
  const isFreePlan = slugReadOnly && form.slug === "free"
  return (
    <div className="space-y-4 py-2">
      <div className="space-y-1.5">
        <Label className="text-sm font-medium">
          {t("fields.slug")}
          {!slugReadOnly && <span className="text-destructive"> *</span>}
        </Label>
        <Input
          value={form.slug}
          onChange={(e) => setField("slug", e.target.value)}
          placeholder="pro"
          aria-invalid={!!fieldErrors.slug}
          disabled={slugReadOnly}
          className="font-mono"
        />
        {fieldErrors.slug ? (
          <p className="text-sm text-destructive">{fieldErrors.slug}</p>
        ) : (
          <p className="text-xs text-muted-foreground">{t("fields.slugHint")}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label className="text-sm font-medium">
          {t("fields.name")} <span className="text-destructive">*</span>
        </Label>
        <Input
          value={form.name}
          onChange={(e) => setField("name", e.target.value)}
          placeholder="Pro"
          aria-invalid={!!fieldErrors.name}
        />
        {fieldErrors.name && <p className="text-sm text-destructive">{fieldErrors.name}</p>}
      </div>

      <div className="space-y-1.5">
        <Label className="text-sm font-medium">
          {t("fields.monthlyTokenQuota")} <span className="text-destructive">*</span>
        </Label>
        <Input
          type="number"
          min={0}
          step={1}
          value={form.monthly_token_quota}
          onChange={(e) => setField("monthly_token_quota", e.target.value)}
          placeholder="5000000"
          aria-invalid={!!fieldErrors.monthly_token_quota}
          disabled={isFreePlan}
        />
        {fieldErrors.monthly_token_quota ? (
          <p className="text-sm text-destructive">{fieldErrors.monthly_token_quota}</p>
        ) : isFreePlan ? (
          <p className="text-xs text-muted-foreground">
            {t("freeQuotaSyncedHint")}{" "}
            <Link
              href="/admin?tab=settings"
              className="underline underline-offset-2 hover:text-foreground"
            >
              {t("freeQuotaEditLink")}
            </Link>
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">{t("fields.monthlyTokenQuotaHint")}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label className="text-sm font-medium">{t("fields.stripePriceId")}</Label>
        <Input
          value={form.stripe_price_id}
          onChange={(e) => setField("stripe_price_id", e.target.value)}
          placeholder="price_1ABCxyz..."
          aria-invalid={!!fieldErrors.stripe_price_id}
          className="font-mono"
        />
        {fieldErrors.stripe_price_id ? (
          <p className="text-sm text-destructive">{fieldErrors.stripe_price_id}</p>
        ) : (
          <p className="text-xs text-muted-foreground">{t("fields.stripePriceIdHint")}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label className="text-sm font-medium">{t("fields.description")}</Label>
        <Textarea
          value={form.description}
          onChange={(e) => setField("description", e.target.value)}
          rows={2}
          placeholder=""
        />
      </div>

      <div className="space-y-1.5">
        <Label className="text-sm font-medium">{t("fields.features")}</Label>
        <Textarea
          value={form.features}
          onChange={(e) => setField("features", e.target.value)}
          rows={3}
          placeholder=""
        />
        <p className="text-xs text-muted-foreground">{t("fields.featuresHint")}</p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label className="text-sm font-medium">{t("fields.sortOrder")}</Label>
          <Input
            type="number"
            value={form.sort_order}
            onChange={(e) => setField("sort_order", e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-sm font-medium">{t("fields.isActive")}</Label>
          <div className="flex h-9 items-center">
            <Switch
              checked={form.is_active}
              onCheckedChange={(v) => setField("is_active", v)}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-3 items-start">
      <div className="text-muted-foreground">{label}</div>
      <div className="col-span-2 break-words">{value}</div>
    </div>
  )
}
