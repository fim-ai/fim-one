"use client"

import { useState, useEffect } from "react"
import { Loader2, ShieldOff, ShieldCheck, Megaphone, Wrench, LogOut, AlertTriangle } from "lucide-react"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
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
import { apiFetch } from "@/lib/api"
import { toast } from "sonner"

interface SystemSettings {
  registration_enabled: boolean
  maintenance_mode: boolean
  announcement_enabled: boolean
  announcement_text: string
}

export function AdminSettings() {
  const [settings, setSettings] = useState<SystemSettings | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [announcementDraft, setAnnouncementDraft] = useState("")
  const [forceLogoutOpen, setForceLogoutOpen] = useState(false)
  const [isForcing, setIsForcing] = useState(false)

  useEffect(() => {
    apiFetch<SystemSettings>("/api/admin/settings")
      .then((data) => {
        setSettings(data)
        setAnnouncementDraft(data.announcement_text)
      })
      .catch((err) =>
        toast.error(err instanceof Error ? err.message : "Failed to load settings"),
      )
      .finally(() => setIsLoading(false))
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
      toast.error(err instanceof Error ? err.message : "Failed to update settings")
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
      toast.success(`Logged out ${res.invalidated} active session${res.invalidated !== 1 ? "s" : ""}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to force logout")
    } finally {
      setIsForcing(false)
      setForceLogoutOpen(false)
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading settings…
      </div>
    )
  }

  return (
    <div className="space-y-8 max-w-2xl">
      <div>
        <h3 className="text-base font-medium">System Settings</h3>
        <p className="text-sm text-muted-foreground">
          Global configuration that affects all users.
        </p>
      </div>

      <Separator />

      {/* ── Registration ── */}
      <SettingSection
        icon={settings?.registration_enabled ? ShieldCheck : ShieldOff}
        iconColor={settings?.registration_enabled ? "text-green-500" : "text-destructive"}
        title="User Registration"
        description="Control whether new users can self-register via the login page."
      >
        <div className="flex items-center justify-between">
          <div>
            <Label htmlFor="registration-toggle" className="text-sm font-medium cursor-pointer">
              Allow public registration
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              {settings?.registration_enabled
                ? "Anyone can create an account from the login page."
                : "Only admins can create accounts via the Users tab."}
            </p>
          </div>
          <Switch
            id="registration-toggle"
            checked={settings?.registration_enabled ?? true}
            onCheckedChange={async (v) => {
              await patch({ registration_enabled: v })
              toast.success(v ? "Public registration enabled" : "Public registration disabled")
            }}
            disabled={isSaving}
          />
        </div>
      </SettingSection>

      <Separator />

      {/* ── System Announcement ── */}
      <SettingSection
        icon={Megaphone}
        iconColor="text-amber-500"
        title="System Announcement"
        description="Show a banner message to all users at the top of every page."
      >
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label htmlFor="announcement-toggle" className="text-sm font-medium cursor-pointer">
              Show announcement banner
            </Label>
            <Switch
              id="announcement-toggle"
              checked={settings?.announcement_enabled ?? false}
              onCheckedChange={async (v) => {
                await patch({ announcement_enabled: v })
                toast.success(v ? "Announcement banner enabled" : "Announcement banner disabled")
              }}
              disabled={isSaving}
            />
          </div>
          <Textarea
            placeholder="Write your announcement here…"
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
              toast.success("Announcement text saved")
            }}
          >
            {isSaving && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
            Save Text
          </Button>
        </div>
      </SettingSection>

      <Separator />

      {/* ── Maintenance Mode ── */}
      <SettingSection
        icon={Wrench}
        iconColor={settings?.maintenance_mode ? "text-orange-500" : "text-muted-foreground"}
        title="Maintenance Mode"
        description="Block all non-admin access. Admins can still log in and manage the system."
      >
        <div className="flex items-center justify-between">
          <div>
            <Label htmlFor="maintenance-toggle" className="text-sm font-medium cursor-pointer">
              Enable maintenance mode
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              {settings?.maintenance_mode
                ? "System is in maintenance. Non-admin requests receive 503."
                : "System is operating normally."}
            </p>
          </div>
          <Switch
            id="maintenance-toggle"
            checked={settings?.maintenance_mode ?? false}
            onCheckedChange={async (v) => {
              await patch({ maintenance_mode: v })
              toast.success(v ? "Maintenance mode ON — users are blocked" : "Maintenance mode OFF — system restored")
            }}
            disabled={isSaving}
          />
        </div>
      </SettingSection>

      <Separator />

      {/* ── Danger Zone ── */}
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-medium text-destructive flex items-center gap-1.5">
            <AlertTriangle className="h-4 w-4" />
            Danger Zone
          </h4>
          <p className="text-sm text-muted-foreground">
            Irreversible or high-impact actions.
          </p>
        </div>

        <div className="flex items-start gap-4 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <LogOut className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
          <div className="flex-1">
            <p className="text-sm font-medium">Force logout all users</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              Invalidates every active refresh token. All users (except you) will be signed out immediately.
            </p>
          </div>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setForceLogoutOpen(true)}
          >
            Force Logout All
          </Button>
        </div>
      </div>

      {/* Confirm dialog */}
      <AlertDialog open={forceLogoutOpen} onOpenChange={setForceLogoutOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Force logout all users?</AlertDialogTitle>
            <AlertDialogDescription>
              This will immediately invalidate all active sessions. Every user will be signed out and
              must log in again. Your own session will not be affected.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={handleForceLogout}
              disabled={isForcing}
            >
              {isForcing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Yes, Force Logout
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
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
