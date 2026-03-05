"use client"

import { useState, useEffect } from "react"
import { Loader2, ShieldOff, ShieldCheck } from "lucide-react"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { apiFetch } from "@/lib/api"
import { toast } from "sonner"

interface SystemSettings {
  registration_enabled: boolean
}

export function AdminSettings() {
  const [settings, setSettings] = useState<SystemSettings | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    apiFetch<SystemSettings>("/api/admin/settings")
      .then((data) => setSettings(data))
      .catch((err) =>
        toast.error(err instanceof Error ? err.message : "Failed to load settings"),
      )
      .finally(() => setIsLoading(false))
  }, [])

  const handleToggleRegistration = async (enabled: boolean) => {
    if (!settings) return
    setIsSaving(true)
    try {
      const updated = await apiFetch<SystemSettings>("/api/admin/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ registration_enabled: enabled }),
      })
      setSettings(updated)
      toast.success(
        enabled ? "Public registration enabled" : "Public registration disabled",
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update settings")
    } finally {
      setIsSaving(false)
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
    <div className="space-y-8">
      <div>
        <h3 className="text-base font-medium">System Settings</h3>
        <p className="text-sm text-muted-foreground">
          Global configuration that affects all users.
        </p>
      </div>

      <Separator />

      {/* Registration control */}
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-medium">User Registration</h4>
          <p className="text-sm text-muted-foreground">
            Control whether new users can self-register via the login page.
          </p>
        </div>

        <div className="flex items-start gap-4 rounded-lg border border-border bg-card p-4">
          <div className="mt-0.5">
            {settings?.registration_enabled ? (
              <ShieldCheck className="h-5 w-5 text-green-500" />
            ) : (
              <ShieldOff className="h-5 w-5 text-destructive" />
            )}
          </div>
          <div className="flex-1 space-y-1">
            <Label htmlFor="registration-toggle" className="text-sm font-medium cursor-pointer">
              Allow public registration
            </Label>
            <p className="text-xs text-muted-foreground">
              {settings?.registration_enabled
                ? "Anyone with access to the login page can create an account."
                : "Registration is disabled. Only admins can create new accounts via the Users tab."}
            </p>
          </div>
          <Switch
            id="registration-toggle"
            checked={settings?.registration_enabled ?? true}
            onCheckedChange={handleToggleRegistration}
            disabled={isSaving}
          />
        </div>
      </div>
    </div>
  )
}
