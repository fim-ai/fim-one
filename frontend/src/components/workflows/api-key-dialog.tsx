"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  CheckCircle2,
  Copy,
  Key,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Trash2,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { workflowApi } from "@/lib/api"
import { getApiDirectUrl } from "@/lib/constants"

interface ApiKeyDialogProps {
  workflowId: string
  hasApiKey: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  onApiKeyChanged: (hasKey: boolean) => void
}

type DialogState = "no_key" | "key_generated" | "key_exists"

export function ApiKeyDialog({
  workflowId,
  hasApiKey,
  open,
  onOpenChange,
  onApiKeyChanged,
}: ApiKeyDialogProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")

  const [state, setState] = useState<DialogState>(
    hasApiKey ? "key_exists" : "no_key",
  )
  const [apiKey, setApiKey] = useState<string | null>(null)
  const [isGenerating, setIsGenerating] = useState(false)
  const [isRevoking, setIsRevoking] = useState(false)
  const [copied, setCopied] = useState(false)
  const [showRegenerateConfirm, setShowRegenerateConfirm] = useState(false)
  const [showRevokeConfirm, setShowRevokeConfirm] = useState(false)

  // Sync state when dialog opens or hasApiKey changes
  useEffect(() => {
    if (open) {
      setState(hasApiKey ? "key_exists" : "no_key")
      setApiKey(null)
      setCopied(false)
    }
  }, [open, hasApiKey])

  const handleGenerate = useCallback(async () => {
    setIsGenerating(true)
    try {
      const result = await workflowApi.generateApiKey(workflowId)
      setApiKey(result.api_key)
      setState("key_generated")
      onApiKeyChanged(true)
      toast.success(t("apiKeyGenerated"))
    } catch {
      toast.error(t("apiKeyGenerateFailed"))
    } finally {
      setIsGenerating(false)
    }
  }, [workflowId, onApiKeyChanged, t])

  const handleRegenerate = useCallback(async () => {
    setShowRegenerateConfirm(false)
    await handleGenerate()
  }, [handleGenerate])

  const handleRevoke = useCallback(async () => {
    setShowRevokeConfirm(false)
    setIsRevoking(true)
    try {
      await workflowApi.revokeApiKey(workflowId)
      setState("no_key")
      setApiKey(null)
      onApiKeyChanged(false)
      toast.success(t("apiKeyRevoked"))
    } catch {
      toast.error(t("apiKeyRevokeFailed"))
    } finally {
      setIsRevoking(false)
    }
  }, [workflowId, onApiKeyChanged, t])

  const handleCopy = useCallback(async () => {
    if (!apiKey) return
    try {
      await navigator.clipboard.writeText(apiKey)
      setCopied(true)
      toast.success(t("apiKeyCopied"))
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error(t("apiKeyCopyFailed"))
    }
  }, [apiKey, t])

  // The key travels in the Authorization header, never in the URL, so it
  // stays out of proxy and access logs.
  const triggerUrl = `${getApiDirectUrl()}/api/workflows/trigger`
  const keyForExample = apiKey ?? "{YOUR_API_KEY}"

  const curlExample = `curl -X POST "${triggerUrl}" \\
  -H "Authorization: Bearer ${keyForExample}" \\
  -H "Content-Type: application/json" \\
  -d '{"inputs": {}}'`

  const fetchExample = `fetch("${triggerUrl}", {
  method: "POST",
  headers: {
    "Authorization": "Bearer ${keyForExample}",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ inputs: {} }),
})`

  const isBusy = isGenerating || isRevoking

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Key className="h-4 w-4" />
              {t("apiKeyTitle")}
            </DialogTitle>
            <DialogDescription>{t("apiKeyDescription")}</DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* State 1: No key configured */}
            {state === "no_key" && (
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-muted-foreground/40" />
                  <span className="text-xs text-muted-foreground">
                    {t("apiKeyStatusNone")}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground">
                  {t("apiKeyExplanation")}
                </p>
                <Button
                  onClick={handleGenerate}
                  disabled={isBusy}
                  className="w-full"
                >
                  {isGenerating ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Key className="mr-2 h-4 w-4" />
                  )}
                  {t("apiKeyGenerateButton")}
                </Button>
              </div>
            )}

            {/* State 2: Key just generated */}
            {state === "key_generated" && apiKey && (
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  <span className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
                    {t("apiKeyStatusGenerated")}
                  </span>
                </div>

                {/* Key display */}
                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-foreground">
                    {t("apiKeyLabel")}
                  </label>
                  <div className="flex gap-2">
                    <code className="flex-1 rounded-md border bg-muted px-3 py-2 text-xs font-mono break-all select-all">
                      {apiKey}
                    </code>
                    <Button
                      variant="outline"
                      size="icon"
                      onClick={handleCopy}
                      className="shrink-0"
                    >
                      {copied ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                      ) : (
                        <Copy className="h-4 w-4" />
                      )}
                    </Button>
                  </div>
                </div>

                {/* Warning */}
                <div className="flex items-start gap-2 rounded-md border border-amber-400/30 bg-amber-50 p-3 dark:bg-amber-900/20">
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
                  <p className="text-xs text-amber-700 dark:text-amber-300">
                    {t("apiKeyWarningOnce")}
                  </p>
                </div>

                {/* Trigger URL */}
                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-foreground">
                    {t("apiKeyTriggerUrl")}
                  </label>
                  <code className="block rounded-md border bg-muted px-3 py-2 text-xs font-mono break-all select-all">
                    POST {triggerUrl}
                  </code>
                </div>

                {/* Example requests */}
                <ExampleSection curlExample={curlExample} fetchExample={fetchExample} />
              </div>
            )}

            {/* State 3: Key exists but not shown */}
            {state === "key_exists" && (
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  <span className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
                    {t("apiKeyStatusConfigured")}
                  </span>
                </div>

                <p className="text-sm text-muted-foreground">
                  {t("apiKeyConfiguredMessage")}
                </p>

                {/* Trigger URL (placeholder) */}
                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-foreground">
                    {t("apiKeyTriggerUrl")}
                  </label>
                  <code className="block rounded-md border bg-muted px-3 py-2 text-xs font-mono break-all select-all">
                    POST {triggerUrl}
                  </code>
                </div>

                {/* Example requests */}
                <ExampleSection curlExample={curlExample} fetchExample={fetchExample} />

                {/* Action buttons */}
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    onClick={() => setShowRegenerateConfirm(true)}
                    disabled={isBusy}
                    className="flex-1"
                  >
                    {isGenerating ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <RefreshCw className="mr-2 h-4 w-4" />
                    )}
                    {t("apiKeyRegenerateButton")}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => setShowRevokeConfirm(true)}
                    disabled={isBusy}
                    className="text-destructive hover:text-destructive"
                  >
                    {isRevoking ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="mr-2 h-4 w-4" />
                    )}
                    {t("apiKeyRevokeButton")}
                  </Button>
                </div>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Regenerate confirmation */}
      <AlertDialog
        open={showRegenerateConfirm}
        onOpenChange={setShowRegenerateConfirm}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("apiKeyRegenerateConfirmTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("apiKeyRegenerateConfirmDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleRegenerate}>
              {t("apiKeyRegenerateButton")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Revoke confirmation */}
      <AlertDialog
        open={showRevokeConfirm}
        onOpenChange={setShowRevokeConfirm}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("apiKeyRevokeConfirmTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("apiKeyRevokeConfirmDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleRevoke}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {t("apiKeyRevokeButton")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

/** Collapsible example request section */
function ExampleSection({
  curlExample,
  fetchExample,
}: {
  curlExample: string
  fetchExample: string
}) {
  const t = useTranslations("workflows")
  const [tab, setTab] = useState<"curl" | "fetch">("curl")

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium text-foreground">
          {t("apiKeyExampleTitle")}
        </label>
        <div className="flex gap-1 ml-auto">
          <button
            onClick={() => setTab("curl")}
            className={`px-2 py-0.5 text-[11px] rounded-md transition-colors ${
              tab === "curl"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            cURL
          </button>
          <button
            onClick={() => setTab("fetch")}
            className={`px-2 py-0.5 text-[11px] rounded-md transition-colors ${
              tab === "fetch"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            JavaScript
          </button>
        </div>
      </div>
      <pre className="rounded-md border bg-muted px-3 py-2 text-xs font-mono whitespace-pre-wrap break-all overflow-x-auto max-h-40">
        {tab === "curl" ? curlExample : fetchExample}
      </pre>
    </div>
  )
}
