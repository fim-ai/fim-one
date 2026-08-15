"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { Loader2, ShieldOff, ShieldCheck, Megaphone, Wrench, LogOut, AlertTriangle, Zap, Plus, Ticket, Copy, X, Eye, CreditCard, Blocks } from "lucide-react"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
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
import { apiFetch, adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import {
  setAccessModel,
  type AccessModel,
} from "@/lib/billing-flag"
import { MODULE_FLAGS_CHANGED_EVENT } from "@/lib/module-flags"
import { toast } from "sonner"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import type { InviteCode } from "@/types/admin"

interface SystemSettings {
  registration_enabled: boolean
  registration_mode: string
  email_verification_enabled: boolean
  maintenance_mode: boolean
  announcement_enabled: boolean
  announcement_text: string
  default_token_quota: number
  smtp_configured: boolean
  /** True when the operator has enabled the Stripe billing pipeline. */
  billing_enabled: boolean
  /** Instance posture: off | freemium | paid_only. */
  access_model: AccessModel
  /** True when STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET are present. */
  stripe_configured: boolean
  /** Soft-shelvable modules (off by default): skills, workflows. */
  modules: { skills: boolean; workflows: boolean }
}

export function AdminSettings() {
  const t = useTranslations("admin.settings")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const [settings, setSettings] = useState<SystemSettings | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [announcementDraft, setAnnouncementDraft] = useState("")
  const [quotaDraft, setQuotaDraft] = useState("")
  const [forceLogoutOpen, setForceLogoutOpen] = useState(false)
  const [isForcing, setIsForcing] = useState(false)

  useEffect(() => {
    apiFetch<SystemSettings>("/api/admin/settings")
      .then((data) => {
        setSettings(data)
        setAnnouncementDraft(data.announcement_text)
        setQuotaDraft(data.default_token_quota ? String(data.default_token_quota) : "0")
      })
      .catch((err) =>
        toast.error(getErrorMessage(err, tError)),
      )
      .finally(() => setIsLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const patch = async (updates: Partial<SystemSettings>) => {
    if (!settings) return
    setIsSaving(true)
    try {
      const updated = await apiFetch<SystemSettings>("/api/admin/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      })
      setSettings(updated)
      setAnnouncementDraft(updated.announcement_text)
      return updated
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSaving(false)
    }
  }

  const handleForceLogout = async () => {
    setIsForcing(true)
    try {
      const res = await apiFetch<{ invalidated: number }>("/api/admin/actions/force-logout-all", {
        method: "POST",
      })
      toast.success(t("loggedOutSessions", { count: res.invalidated }))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsForcing(false)
      setForceLogoutOpen(false)
    }
  }

  // Derive registration mode from settings
  const registrationMode = settings?.registration_mode ?? (settings?.registration_enabled ? "open" : "disabled")

  const handleRegistrationModeChange = async (value: string) => {
    await patch({ registration_mode: value } as Partial<SystemSettings>)
    toast.success(
      value === "open"
        ? t("registrationOpenToast")
        : value === "invite"
        ? t("registrationInviteToast")
        : t("registrationDisabledToast")
    )
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t("loadingSettings")}
      </div>
    )
  }

  return (
    <div className="space-y-8 max-w-2xl">
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">
          {t("subtitle")}
        </p>
      </div>

      <Separator />

      {/* -- Registration -- */}
      <SettingSection
        icon={registrationMode === "open" ? ShieldCheck : ShieldOff}
        iconColor={registrationMode === "open" ? "text-green-500" : registrationMode === "invite" ? "text-amber-500" : "text-destructive"}
        title={t("registrationTitle")}
        description={t("registrationDesc")}
      >
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-sm font-medium">{t("registrationMode")}</Label>
            <Select value={registrationMode} onValueChange={handleRegistrationModeChange} disabled={isSaving}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="open">{t("registrationOpen")}</SelectItem>
                <SelectItem value="invite">{t("registrationInvite")}</SelectItem>
                <SelectItem value="disabled">{t("registrationDisabled")}</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground mt-0.5">
              {registrationMode === "open"
                ? t("registrationOpenDesc")
                : registrationMode === "invite"
                ? t("registrationInviteDesc")
                : t("registrationDisabledDesc")}
            </p>
          </div>
          {registrationMode === "open" && (
            <div className="flex items-center justify-between pt-2 border-t border-border">
              <div>
                <Label htmlFor="email-verification-toggle" className="text-sm font-medium cursor-pointer">
                  {t("emailVerificationToggle")}
                </Label>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {t("emailVerificationDesc")}
                </p>
                {!settings?.smtp_configured && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                    {settings?.email_verification_enabled
                      ? t("smtpLostWhileEnabled")
                      : t("smtpRequired")}
                  </p>
                )}
              </div>
              <Switch
                id="email-verification-toggle"
                checked={settings?.email_verification_enabled ?? false}
                onCheckedChange={async (v) => {
                  await patch({ email_verification_enabled: v })
                  toast.success(v ? t("emailVerificationEnabled") : t("emailVerificationDisabled"))
                }}
                disabled={isSaving || (!settings?.smtp_configured && !settings?.email_verification_enabled)}
              />
            </div>
          )}
          {registrationMode === "invite" && <InviteCodeManager />}
        </div>
      </SettingSection>

      <Separator />

      {/* -- Default Token Quota -- */}
      <SettingSection
        icon={Zap}
        iconColor="text-blue-500"
        title={t("tokenQuotaTitle")}
        description={
          settings?.access_model === "paid_only"
            ? t("tokenQuotaDescPaidOnly")
            : settings?.access_model === "freemium"
              ? t("tokenQuotaDescFreemium")
              : t("tokenQuotaDesc")
        }
      >
        <div className="flex items-center gap-3">
          <Input
            type="number"
            min={0}
            className="max-w-[200px]"
            value={quotaDraft}
            onChange={(e) => setQuotaDraft(e.target.value)}
          />
          <Button
            size="sm"
            variant="outline"
            disabled={isSaving || quotaDraft === String(settings?.default_token_quota ?? 0)}
            onClick={async () => {
              const val = parseInt(quotaDraft, 10) || 0
              await patch({ default_token_quota: val })
              toast.success(t("tokenQuotaSaved"))
            }}
          >
            {isSaving && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
            {tc("save")}
          </Button>
        </div>
      </SettingSection>

      <Separator />

      {/* -- System Announcement -- */}
      <SettingSection
        icon={Megaphone}
        iconColor="text-amber-500"
        title={t("announcementTitle")}
        description={t("announcementDesc")}
      >
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label htmlFor="announcement-toggle" className="text-sm font-medium cursor-pointer">
              {t("announcementToggle")}
            </Label>
            <Switch
              id="announcement-toggle"
              checked={settings?.announcement_enabled ?? false}
              onCheckedChange={async (v) => {
                await patch({ announcement_enabled: v })
                toast.success(v ? t("announcementEnabled") : t("announcementDisabled"))
              }}
              disabled={isSaving}
            />
          </div>
          <Textarea
            placeholder={t("announcementPlaceholder")}
            value={announcementDraft}
            onChange={(e) => setAnnouncementDraft(e.target.value)}
            className="resize-none text-sm"
            rows={3}
          />
          <Button
            size="sm"
            variant="outline"
            disabled={isSaving || announcementDraft === settings?.announcement_text}
            onClick={async () => {
              await patch({ announcement_text: announcementDraft })
              toast.success(t("announcementSaved"))
            }}
          >
            {isSaving && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
            {t("saveText")}
          </Button>
        </div>
      </SettingSection>

      <Separator />

      {/* -- Billing pipeline feature flag -- */}
      <BillingToggleSection
        settings={settings}
        onUpdated={(updated) => setSettings(updated)}
      />

      <Separator />

      {/* -- Maintenance Mode -- */}
      <SettingSection
        icon={Wrench}
        iconColor={settings?.maintenance_mode ? "text-orange-500" : "text-muted-foreground"}
        title={t("maintenanceTitle")}
        description={t("maintenanceDesc")}
      >
        <div className="flex items-center justify-between">
          <div>
            <Label htmlFor="maintenance-toggle" className="text-sm font-medium cursor-pointer">
              {t("maintenanceToggle")}
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              {settings?.maintenance_mode
                ? t("maintenanceOnDesc")
                : t("maintenanceOffDesc")}
            </p>
          </div>
          <Switch
            id="maintenance-toggle"
            checked={settings?.maintenance_mode ?? false}
            onCheckedChange={async (v) => {
              await patch({ maintenance_mode: v })
              toast.success(v ? t("maintenanceOn") : t("maintenanceOff"))
            }}
            disabled={isSaving}
          />
        </div>
      </SettingSection>

      <Separator />

      {/* -- Modules (soft-shelve) -- */}
      <SettingSection
        icon={Blocks}
        iconColor="text-muted-foreground"
        title={t("modulesTitle")}
        description={t("modulesDesc")}
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <Label htmlFor="module-skills-toggle" className="text-sm font-medium cursor-pointer">
                {t("moduleSkills")}
              </Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t("moduleSkillsDesc")}
              </p>
            </div>
            <Switch
              id="module-skills-toggle"
              checked={settings?.modules?.skills ?? false}
              onCheckedChange={async (v) => {
                const updated = await patch({
                  modules: { ...(settings?.modules ?? { skills: false, workflows: false }), skills: v },
                } as Partial<SystemSettings>)
                if (updated) {
                  window.dispatchEvent(new CustomEvent(MODULE_FLAGS_CHANGED_EVENT))
                  toast.success(v ? t("moduleEnabled") : t("moduleDisabled"))
                }
              }}
              disabled={isSaving}
            />
          </div>
          <div className="flex items-center justify-between">
            <div>
              <Label htmlFor="module-workflows-toggle" className="text-sm font-medium cursor-pointer">
                {t("moduleWorkflows")}
              </Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t("moduleWorkflowsDesc")}
              </p>
            </div>
            <Switch
              id="module-workflows-toggle"
              checked={settings?.modules?.workflows ?? false}
              onCheckedChange={async (v) => {
                const updated = await patch({
                  modules: { ...(settings?.modules ?? { skills: false, workflows: false }), workflows: v },
                } as Partial<SystemSettings>)
                if (updated) {
                  window.dispatchEvent(new CustomEvent(MODULE_FLAGS_CHANGED_EVENT))
                  toast.success(v ? t("moduleEnabled") : t("moduleDisabled"))
                }
              }}
              disabled={isSaving}
            />
          </div>
        </div>
      </SettingSection>

      <Separator />

      {/* -- Danger Zone -- */}
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-medium text-destructive flex items-center gap-1.5">
            <AlertTriangle className="h-4 w-4" />
            {t("dangerZone")}
          </h4>
          <p className="text-sm text-muted-foreground">
            {t("dangerZoneDesc")}
          </p>
        </div>

        <div className="flex items-start gap-4 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <LogOut className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
          <div className="flex-1">
            <p className="text-sm font-medium">{t("forceLogoutAll")}</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {t("forceLogoutAllDesc")}
            </p>
          </div>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setForceLogoutOpen(true)}
          >
            {t("forceLogoutAllBtn")}
          </Button>
        </div>
      </div>

      {/* Confirm dialog */}
      <AlertDialog open={forceLogoutOpen} onOpenChange={setForceLogoutOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("forceLogoutTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("forceLogoutDesc")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={handleForceLogout}
              disabled={isForcing}
            >
              {isForcing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("yesForceLogout")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/* -- Invite Code Manager -- */

function InviteCodeManager() {
  const t = useTranslations("admin.settings")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDate } = useDateFormatter()
  const [codes, setCodes] = useState<InviteCode[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [createOpen, setCreateOpen] = useState(false)
  const [revokeTarget, setRevokeTarget] = useState<InviteCode | null>(null)
  const [showInactive, setShowInactive] = useState(false)

  // Create form
  const [note, setNote] = useState("")
  const [maxUses, setMaxUses] = useState("1")
  const [expiresAt, setExpiresAt] = useState("")
  const [isSaving, setIsSaving] = useState(false)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listInviteCodes()
      setCodes(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { load() }, [load])

  const handleCreate = async () => {
    setIsSaving(true)
    try {
      await adminApi.createInviteCode({
        note: note.trim() || undefined,
        max_uses: parseInt(maxUses, 10) || 1,
        expires_at: expiresAt || undefined,
      })
      toast.success(t("codeGenerated"))
      setCreateOpen(false)
      setNote("")
      setMaxUses("1")
      setExpiresAt("")
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSaving(false)
    }
  }

  const handleRevoke = async () => {
    if (!revokeTarget) return
    try {
      await adminApi.revokeInviteCode(revokeTarget.id)
      toast.success(t("codeRevoked"))
      setRevokeTarget(null)
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const copyCode = (code: string) => {
    navigator.clipboard.writeText(code)
    toast.success(t("codeCopied"))
  }

  const inactiveCount = codes.filter((c) => !c.is_active || c.use_count >= c.max_uses).length
  const visibleCodes = showInactive ? codes : codes.filter((c) => c.is_active && c.use_count < c.max_uses)

  return (
    <div className="space-y-3 pt-2">
      <div className="flex items-center justify-between">
        <h5 className="text-sm font-medium flex items-center gap-1.5">
          <Ticket className="h-3.5 w-3.5" />
          {t("inviteCodes")}
        </h5>
        <div className="flex items-center gap-2">
          {inactiveCount > 0 && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowInactive((v) => !v)}
              className="h-7 gap-1 text-xs text-muted-foreground"
            >
              <Eye className="h-3 w-3" />
              {showInactive ? t("hideInactive") : t("inactiveCount", { count: inactiveCount })}
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => setCreateOpen(true)} className="h-7 gap-1 text-xs">
            <Plus className="h-3 w-3" />
            {t("generateCode")}
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-xs py-2">
          <Loader2 className="h-3 w-3 animate-spin" />
          {tc("loading")}
        </div>
      ) : visibleCodes.length === 0 ? (
        <p className="text-xs text-muted-foreground py-2">
          {codes.length === 0 ? t("noInviteCodes") : t("noActiveInviteCodes")}
        </p>
      ) : (
        <div className="divide-y divide-border rounded-md border border-border text-sm">
          {visibleCodes.map((c) => (
            <div key={c.id} className="flex items-center justify-between px-3 py-2">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <code className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded">{c.code}</code>
                <button onClick={() => copyCode(c.code)} className="text-muted-foreground hover:text-foreground shrink-0">
                  <Copy className="h-3 w-3" />
                </button>
                {c.note && <span className="text-xs text-muted-foreground truncate">{c.note}</span>}
              </div>
              <div className="flex items-center gap-2 shrink-0 ml-2">
                <Badge variant="outline" className="text-xs">
                  {c.use_count}/{c.max_uses}
                </Badge>
                {c.expires_at && (
                  <span className="text-xs text-muted-foreground">
                    {t("expiresLabel", { date: formatDate(c.expires_at) })}
                  </span>
                )}
                {!c.is_active ? (
                  <Badge variant="secondary" className="text-xs">{t("revoked")}</Badge>
                ) : c.use_count >= c.max_uses ? (
                  <Badge variant="secondary" className="text-xs">{t("exhausted")}</Badge>
                ) : (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 text-destructive"
                    onClick={() => setRevokeTarget(c)}
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("generateTitle")}</DialogTitle>
            <DialogDescription>
              {t("generateDesc")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label className="text-sm font-medium">{t("noteLabel")}</Label>
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={t("notePlaceholder")}
              />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">{t("maxUsesLabel")}</Label>
              <Input
                type="number"
                min={1}
                value={maxUses}
                onChange={(e) => setMaxUses(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">{t("expiresAtLabel")}</Label>
              <Input
                type="date"
                value={expiresAt}
                onChange={(e) => setExpiresAt(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>{tc("cancel")}</Button>
            <Button onClick={handleCreate} disabled={isSaving}>
              {isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("generate")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revoke AlertDialog */}
      <AlertDialog open={!!revokeTarget} onOpenChange={(open) => !open && setRevokeTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("revokeTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("revokeDesc", { code: revokeTarget?.code ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleRevoke} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              {t("revoke")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────
 * Billing pipeline toggle — encodes the
 * "front-loaded data, switch-only state" architectural rule.
 *
 * - First-time activation (FALSE → TRUE) calls the dedicated activation
 *   endpoint. The backend seeds Free + Pro plans, sets the
 *   default_plan_id pointer, and backfills users.plan_id.
 * - Subsequent flips toggle the flag without touching any data.
 * - Re-running activation on an already-activated install is a no-op.
 * ──────────────────────────────────────────────────────────────────── */
function BillingToggleSection({
  settings,
  onUpdated,
}: {
  settings: SystemSettings | null
  onUpdated: (s: SystemSettings) => void
}) {
  const t = useTranslations("admin.settings")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const [isSaving, setIsSaving] = useState(false)
  const [pendingModel, setPendingModel] = useState<AccessModel | null>(null)

  if (!settings) return null

  const current: AccessModel = settings.access_model ?? (settings.billing_enabled ? "freemium" : "off")

  const applyModel = async (next: AccessModel) => {
    if (next !== "off" && !settings.stripe_configured) {
      toast.error(t("billingStripeMissing"))
      return
    }
    setIsSaving(true)
    try {
      const result = await setAccessModel(next)
      onUpdated({
        ...settings,
        billing_enabled: result.billing_enabled,
        access_model: result.access_model,
      })
      if (next === "off") {
        toast.success(t("billingDisabled"))
      } else if (result.plans_seeded > 0 || result.users_backfilled > 0) {
        toast.success(
          t("billingActivatedSeeded", {
            plans: result.plans_seeded,
            users: result.users_backfilled,
          }),
        )
      } else {
        toast.success(t("accessModelSaved"))
      }
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSaving(false)
      setPendingModel(null)
    }
  }

  const requestModel = (next: AccessModel) => {
    if (next === current) return
    const tightening =
      (current !== "off" && next === "off") ||
      next === "paid_only"
    if (tightening) {
      setPendingModel(next)
      return
    }
    void applyModel(next)
  }

  const options: { value: AccessModel; title: string; desc: string }[] = [
    { value: "off", title: t("accessModelOff"), desc: t("accessModelOffDesc") },
    { value: "freemium", title: t("accessModelFreemium"), desc: t("accessModelFreemiumDesc") },
    { value: "paid_only", title: t("accessModelPaidOnly"), desc: t("accessModelPaidOnlyDesc") },
  ]

  return (
    <SettingSection
      icon={CreditCard}
      iconColor={
        settings.billing_enabled ? "text-green-500" : "text-muted-foreground"
      }
      title={t("billingTitle")}
      description={t("billingDesc")}
    >
      <div className="space-y-3">
        {!settings.stripe_configured && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            {t("billingStripeMissing")}
          </p>
        )}
        <div className="grid gap-2">
          {options.map((opt) => {
            const selected = current === opt.value
            const stripeBlocked = opt.value !== "off" && !settings.stripe_configured
            return (
              <button
                key={opt.value}
                type="button"
                disabled={isSaving || stripeBlocked}
                onClick={() => requestModel(opt.value)}
                className={`rounded-md border px-3 py-2.5 text-left transition-colors ${
                  selected
                    ? "border-primary bg-primary/5"
                    : "border-border hover:bg-muted/40"
                } disabled:opacity-50`}
              >
                <div className="text-sm font-medium">{opt.title}</div>
                <p className="mt-0.5 text-xs text-muted-foreground">{opt.desc}</p>
              </button>
            )
          })}
        </div>
      </div>

      <AlertDialog
        open={pendingModel !== null}
        onOpenChange={(open) => { if (!open) setPendingModel(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("accessModelConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingModel === "off"
                ? t("accessModelConfirmOff", { quota: settings.default_token_quota })
                : current === "freemium"
                  ? t("accessModelConfirmPaidOnlyFromFreemium")
                  : t("accessModelConfirmPaidOnlyFromOff")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => pendingModel && applyModel(pendingModel)}
              disabled={isSaving}
            >
              {isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SettingSection>
  )
}

function SettingSection({
  icon: Icon,
  iconColor,
  title,
  description,
  children,
}: {
  icon: React.ElementType
  iconColor: string
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-4">
      <div>
        <h4 className="text-sm font-medium flex items-center gap-1.5">
          <Icon className={`h-4 w-4 ${iconColor}`} />
          {title}
        </h4>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <div className="rounded-lg border border-border bg-card p-4">
        {children}
      </div>
    </div>
  )
}
