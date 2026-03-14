"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Bell, Loader2, Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { workflowApi } from "@/lib/api"

interface WebhookConfigDialogProps {
  workflowId: string
  webhookUrl: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (webhookUrl: string | null) => void
}

export function WebhookConfigDialog({
  workflowId,
  webhookUrl,
  open,
  onOpenChange,
  onSaved,
}: WebhookConfigDialogProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")

  const [url, setUrl] = useState(webhookUrl ?? "")
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [isTesting, setIsTesting] = useState(false)
  const [isClearing, setIsClearing] = useState(false)

  // Reset form state when dialog opens
  useEffect(() => {
    if (open) {
      setUrl(webhookUrl ?? "")
      setFieldError(null)
    }
  }, [open, webhookUrl])

  const validateUrl = useCallback(
    (value: string): boolean => {
      if (!value.trim()) {
        setFieldError(null)
        return false
      }
      if (
        !value.trim().startsWith("https://") &&
        !value.trim().startsWith("http://")
      ) {
        setFieldError(t("webhookUrlInvalid"))
        return false
      }
      setFieldError(null)
      return true
    },
    [t],
  )

  const handleUrlChange = (value: string) => {
    setUrl(value)
    if (fieldError) {
      // Re-validate on change to clear error when corrected
      if (
        value.trim().startsWith("https://") ||
        value.trim().startsWith("http://") ||
        !value.trim()
      ) {
        setFieldError(null)
      }
    }
  }

  const handleSave = async () => {
    const trimmed = url.trim()
    if (!trimmed) {
      // If the field is empty, treat as clearing
      await handleClear()
      return
    }
    if (!validateUrl(trimmed)) return

    setIsSaving(true)
    try {
      const updated = await workflowApi.update(workflowId, {
        webhook_url: trimmed,
      })
      toast.success(t("webhookSaved"))
      onSaved(updated.webhook_url)
      onOpenChange(false)
    } catch {
      toast.error(t("webhookSaveFailed"))
    } finally {
      setIsSaving(false)
    }
  }

  const handleClear = async () => {
    setIsClearing(true)
    try {
      const updated = await workflowApi.update(workflowId, {
        webhook_url: null,
      })
      toast.success(t("webhookCleared"))
      onSaved(updated.webhook_url)
      onOpenChange(false)
    } catch {
      toast.error(t("webhookClearFailed"))
    } finally {
      setIsClearing(false)
    }
  }

  const handleTest = async () => {
    const trimmed = url.trim()
    if (!trimmed) return
    if (!validateUrl(trimmed)) return

    // If current URL differs from saved, save first then test
    if (trimmed !== (webhookUrl ?? "")) {
      setIsSaving(true)
      try {
        const updated = await workflowApi.update(workflowId, {
          webhook_url: trimmed,
        })
        onSaved(updated.webhook_url)
      } catch {
        toast.error(t("webhookSaveFailed"))
        setIsSaving(false)
        return
      } finally {
        setIsSaving(false)
      }
    }

    setIsTesting(true)
    try {
      const result = await workflowApi.testWebhook(workflowId)
      if (result.success) {
        toast.success(
          t("webhookTestSuccess", { statusCode: result.status_code ?? 200 }),
        )
      } else {
        toast.error(result.error ?? t("webhookTestFailed"))
      }
    } catch {
      toast.error(t("webhookTestFailed"))
    } finally {
      setIsTesting(false)
    }
  }

  const isConfigured = !!webhookUrl
  const isBusy = isSaving || isTesting || isClearing

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Bell className="h-4 w-4" />
            {t("webhookTitle")}
          </DialogTitle>
          <DialogDescription>{t("webhookDescription")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Status indicator */}
          <div className="flex items-center gap-2">
            <span
              className={
                isConfigured
                  ? "h-2 w-2 rounded-full bg-emerald-500"
                  : "h-2 w-2 rounded-full bg-muted-foreground/40"
              }
            />
            <span className="text-xs text-muted-foreground">
              {isConfigured
                ? t("webhookStatusConfigured")
                : t("webhookStatusNotConfigured")}
            </span>
          </div>

          {/* URL input */}
          <div className="space-y-1.5">
            <label
              htmlFor="webhook-url"
              className="text-sm font-medium text-foreground"
            >
              {t("webhookUrlLabel")}
            </label>
            <Input
              id="webhook-url"
              type="url"
              className="text-sm"
              value={url}
              onChange={(e) => handleUrlChange(e.target.value)}
              placeholder={t("webhookUrlPlaceholder")}
              aria-invalid={!!fieldError}
            />
            {fieldError && (
              <p className="text-sm text-destructive">{fieldError}</p>
            )}
          </div>
        </div>

        <DialogFooter className="flex-row gap-2 sm:justify-between">
          <div className="flex gap-2">
            {/* Test button */}
            <Button
              variant="outline"
              size="sm"
              onClick={handleTest}
              disabled={isBusy || !url.trim()}
            >
              {isTesting ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="mr-1.5 h-3.5 w-3.5" />
              )}
              {t("webhookTestButton")}
            </Button>
            {/* Clear button */}
            {isConfigured && (
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClear}
                disabled={isBusy}
              >
                {isClearing && (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                )}
                {t("webhookClearButton")}
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              className="px-6"
              onClick={() => onOpenChange(false)}
            >
              {tc("cancel")}
            </Button>
            <Button
              className="px-6"
              onClick={handleSave}
              disabled={isBusy || !!fieldError}
            >
              {isSaving && (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              )}
              {tc("save")}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
