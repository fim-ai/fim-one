"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import {
  Clock, Download, MoreHorizontal, PackageMinus, Pencil, Trash2, Terminal, Globe, GlobeLock, FlaskConical,
  Loader2, CheckCircle2, XCircle, Key, AlertTriangle, RotateCw, Power, Store, Users,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { mcpServerApi } from "@/lib/api"
import { MARKET_ORG_ID } from "@/lib/constants"
import type { MCPServerResponse } from "@/types/mcp-server"

interface MCPServerCardProps {
  server: MCPServerResponse
  currentUserId?: string
  onEdit: () => void
  onDelete: () => void
  onToggleActive: (isActive: boolean) => void
  onTest: () => Promise<{ ok: boolean; tool_count?: number; error?: string }>
  onPublish?: (id: string) => void
  onUnpublish?: (id: string) => void
  onResubmit?: (id: string) => void
  onUninstall?: (id: string) => void
  onCredentialsSaved?: (serverId: string, hasCredentials: boolean) => void
}

export function MCPServerCard({
  server,
  currentUserId,
  onEdit,
  onDelete,
  onToggleActive,
  onTest,
  onPublish,
  onUnpublish,
  onUninstall,
  onResubmit,
  onCredentialsSaved,
}: MCPServerCardProps) {
  const t = useTranslations("tools")
  const tc = useTranslations("common")
  const to = useTranslations("organizations")

  const endpoint = server.transport === "stdio" ? server.command : server.url
  const isRemoteTransport = server.transport === "sse" || server.transport === "streamable_http"

  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; tool_count?: number; error?: string } | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [myKeysOpen, setMyKeysOpen] = useState(false)
  const [myKeysEnv, setMyKeysEnv] = useState<Record<string, string>>({})
  const [myKeysEnvErrors, setMyKeysEnvErrors] = useState<Record<string, string>>({})
  const [myKeysSaving, setMyKeysSaving] = useState(false)
  const [myKeysLoading, setMyKeysLoading] = useState(false)

  const isOwner = !currentUserId || server.user_id === currentUserId
  // true for any org-visibility state (pending_review, approved, rejected — visibility already set to "org")
  const isOrgResource = server.visibility === "org" || server.visibility === "global"
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const source = (server as any).source as string | undefined
  const isInstalled = source === "installed"
  const isOrgShared = source === "org" || (!source && !isOwner && isOrgResource)
  const needsKeyConfig = !isOwner && !server.allow_fallback && !server.my_has_credentials

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await onTest()
      setTestResult(result)
    } finally {
      setTesting(false)
    }
  }

  const handleOpenMyKeys = async () => {
    setMyKeysEnv({})
    setMyKeysEnvErrors({})
    setMyKeysLoading(true)
    setMyKeysOpen(true)
    try {
      const status = await mcpServerApi.getMyCredentials(server.id)
      if (status.has_credentials && status.env && Object.keys(status.env).length > 0) {
        // Show actual saved values so user can see / edit them
        setMyKeysEnv(status.env)
      } else if (server.env) {
        // First time: pre-fill key names from server template, values empty
        const template: Record<string, string> = {}
        for (const k of Object.keys(server.env)) {
          template[k] = ""
        }
        setMyKeysEnv(template)
      }
    } catch {
      // ignore, just open with empty
    } finally {
      setMyKeysLoading(false)
    }
  }

  const handleAddEnvRow = () => {
    setMyKeysEnv((prev) => ({ ...prev, "": "" }))
  }

  const handleEnvKeyChange = (oldKey: string, newKey: string) => {
    setMyKeysEnv((prev) => {
      const entries = Object.entries(prev)
      const idx = entries.findIndex(([k]) => k === oldKey)
      if (idx === -1) return prev
      entries[idx] = [newKey, entries[idx][1]]
      return Object.fromEntries(entries)
    })
  }

  const handleEnvValueChange = (key: string, value: string) => {
    setMyKeysEnv((prev) => ({ ...prev, [key]: value }))
    if (value.trim()) setMyKeysEnvErrors((prev) => { const next = { ...prev }; delete next[key]; return next })
  }

  const handleRemoveEnvRow = (key: string) => {
    setMyKeysEnv((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const handleSaveMyKeys = async () => {
    // Validate: skip blank-key rows, reject empty-value rows
    const errors: Record<string, string> = {}
    const env: Record<string, string> = {}
    for (const [k, v] of Object.entries(myKeysEnv)) {
      if (!k.trim()) continue  // blank key row → silently skip
      if (!v.trim()) {
        errors[k] = t("myKeysValueRequired")
      } else {
        env[k.trim()] = v
      }
    }
    if (Object.keys(errors).length > 0) {
      setMyKeysEnvErrors(errors)
      return
    }
    setMyKeysSaving(true)
    try {
      await mcpServerApi.upsertMyCredentials(server.id, { env })
      const hasCredentials = Object.keys(env).length > 0
      toast.success(hasCredentials ? t("myCredentialsSaved") : t("myCredentialsCleared"))
      setMyKeysOpen(false)
      onCredentialsSaved?.(server.id, hasCredentials)
    } catch {
      toast.error(t("failedToSaveMcpServer"))
    } finally {
      setMyKeysSaving(false)
    }
  }

  return (
    <div className="group flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-ring/40 hover:bg-accent/10">
      {/* Header: status dot + name + dropdown */}
      <div className="flex items-center gap-2 mb-2">
        {/* Status indicator dot */}
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className={`shrink-0 inline-block h-2 w-2 rounded-full transition-colors ${
                server.is_active ? "bg-green-500" : "bg-muted-foreground/40"
              }`}
            />
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>
            {server.is_active ? tc("enabled") : tc("disabled")}
          </TooltipContent>
        </Tooltip>

        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground">
          {server.name}
        </h3>

        {/* Dropdown menu */}
        {isOwner ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0 text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 transition-opacity"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={onEdit}>
                <Pencil className="mr-2 h-4 w-4" />
                {tc("edit")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={handleTest} disabled={testing}>
                {testing
                  ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  : <FlaskConical className="mr-2 h-4 w-4" />
                }
                {t("testConnection")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => onToggleActive(!server.is_active)}>
                <Power className="mr-2 h-4 w-4" />
                {server.is_active ? tc("disable") : tc("enable")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              {/* Publish / Unpublish */}
              {onPublish && onUnpublish && (
                <DropdownMenuItem
                  onClick={() => isOrgResource ? onUnpublish(server.id) : onPublish(server.id)}
                >
                  {isOrgResource
                    ? <GlobeLock className="mr-2 h-4 w-4" />
                    : <Globe className="mr-2 h-4 w-4" />
                  }
                  {isOrgResource ? tc("unpublish") : t("publishToOrg")}
                </DropdownMenuItem>
              )}
              {/* Resubmit — only when rejected (API enforces this; pending_review would return 400) */}
              {onResubmit && server.publish_status === "rejected" && (
                <DropdownMenuItem onClick={() => onResubmit(server.id)}>
                  <RotateCw className="mr-2 h-4 w-4" />
                  {t("resubmit")}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
                <Trash2 className="mr-2 h-4 w-4" />
                {tc("delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : isInstalled && onUninstall ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0 text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 transition-opacity"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={handleOpenMyKeys}>
                <Key className="mr-2 h-4 w-4" />
                {t("configureMyKeys")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => onUninstall(server.id)}>
                <PackageMinus className="mr-2 h-4 w-4" />
                {tc("uninstall")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : isOrgResource ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0 text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 transition-opacity"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={handleOpenMyKeys}>
                <Key className="mr-2 h-4 w-4" />
                {t("configureMyKeys")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>

      {/* Installed / Shared badge (non-owner) */}
      {isInstalled && (
        <div className="flex items-center gap-1.5 mb-2">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-violet-500/10 text-violet-500 dark:text-violet-400 border-violet-500/20"
          >
            <Download className="h-2.5 w-2.5 mr-0.5" />
            {tc("installed")}
          </Badge>
        </div>
      )}
      {!isInstalled && isOrgShared && (
        <div className="flex items-center gap-1.5 mb-2">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
          >
            <Users className="h-2.5 w-2.5 mr-0.5" />
            {tc("shared")}
          </Badge>
        </div>
      )}

      {/* Owner visibility badge — shows where the resource is published */}
      {isOwner && isOrgResource && (
        <div className="flex items-center gap-1.5 mb-2">
          <Badge
            variant="secondary"
            className={cn(
              "text-[10px] px-1.5 py-0 h-5",
              server.org_id === MARKET_ORG_ID
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                : "bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
            )}
          >
            {server.org_id === MARKET_ORG_ID ? (
              <><Store className="h-2.5 w-2.5 mr-0.5" />{tc("publishedToMarket")}</>
            ) : (
              <><Globe className="h-2.5 w-2.5 mr-0.5" />{tc("published")}</>
            )}
          </Badge>
        </div>
      )}

      {/* Publish review status badges — only visible to owner */}
      {isOwner && (server.publish_status === "pending_review" || server.publish_status === "approved" || server.publish_status === "rejected") && (
        <div className="flex items-center gap-1.5 mb-2 flex-wrap">
          {server.publish_status === "pending_review" && (
            <Badge
              variant="secondary"
              className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20"
            >
              <Clock className="h-2.5 w-2.5 mr-0.5" />
              {to("publishStatusPending")}
            </Badge>
          )}
          {server.publish_status === "approved" && (
            <Badge
              variant="secondary"
              className="text-[10px] px-1.5 py-0 h-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
            >
              <CheckCircle2 className="h-2.5 w-2.5 mr-0.5" />
              {to("publishStatusApproved")}
            </Badge>
          )}
          {server.publish_status === "rejected" && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="secondary"
                    className="text-[10px] px-1.5 py-0 h-5 bg-red-500/10 text-red-500 dark:text-red-400 border-red-500/20 cursor-default"
                  >
                    <XCircle className="h-2.5 w-2.5 mr-0.5" />
                    {to("publishStatusRejected")}
                  </Badge>
                </TooltipTrigger>
                {server.review_note && (
                  <TooltipContent>
                    <p>{to("rejectedNote", { note: server.review_note })}</p>
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
      )}

      {/* Key required warning badge */}
      {needsKeyConfig && (
        <div className="flex items-center gap-1 mb-2">
          <Badge
            variant="outline"
            className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20 cursor-pointer"
            onClick={handleOpenMyKeys}
          >
            <AlertTriangle className="h-2.5 w-2.5 mr-0.5" />
            {t("keyRequiredWarning")}
          </Badge>
        </div>
      )}

      {/* Transport badge + Endpoint */}
      <div className="flex items-center gap-1.5 mb-1">
        <span className="shrink-0 text-[10px] font-mono uppercase tracking-wide text-muted-foreground/70 border border-border rounded px-1 py-0.5 leading-none">
          {server.transport === "streamable_http" ? "HTTP" : server.transport.toUpperCase()}
        </span>
        {endpoint && (
          <Tooltip>
            <TooltipTrigger asChild>
              <p className="text-xs text-muted-foreground truncate">
                {isRemoteTransport ? (
                  <Globe className="inline h-3 w-3 mr-1 -mt-0.5" />
                ) : (
                  <Terminal className="inline h-3 w-3 mr-1 -mt-0.5" />
                )}
                {endpoint}
              </p>
            </TooltipTrigger>
            <TooltipContent side="bottom" sideOffset={5}>
              {endpoint}
            </TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Tool count / test result */}
      {testResult ? (
        <p className={`text-xs mb-1 flex items-center gap-1 ${testResult.ok ? "text-green-600 dark:text-green-400" : "text-destructive"}`}>
          {testResult.ok
            ? <><CheckCircle2 className="h-3 w-3" />{t("toolsFound", { count: testResult.tool_count ?? 0 })}</>
            : <><XCircle className="h-3 w-3" /><span className="truncate" title={testResult.error}>{testResult.error}</span></>
          }
        </p>
      ) : server.tool_count > 0 ? (
        <p className="text-xs text-muted-foreground mb-1">
          {t("toolCount", { count: server.tool_count })}
        </p>
      ) : null}

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2">
        {server.description || t("noDescription")}
      </p>

      {/* Delete confirmation — sibling, not nested in dropdown */}
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent className="sm:max-w-sm">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Trash2 className="h-4 w-4" />
              {t("deleteMcpServer")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteMcpServerDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={onDelete}
            >
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Configure My Keys dialog — sibling, never nested */}
      <Dialog open={myKeysOpen} onOpenChange={setMyKeysOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Key className="h-4 w-4" />
              {t("myKeysDialogTitle")}
            </DialogTitle>
            <DialogDescription>
              {t("myKeysDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">{t("myKeysEnvVars")}</Label>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 text-xs"
                onClick={handleAddEnvRow}
              >
                {t("myKeysAddRow")}
              </Button>
            </div>
            {myKeysLoading ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {Object.entries(myKeysEnv).map(([key, value], idx) => (
                  <div key={idx} className="space-y-1">
                    <div className="flex items-center gap-2">
                      <Input
                        className="h-8 text-xs font-mono flex-1"
                        placeholder="KEY_NAME"
                        value={key}
                        onChange={(e) => handleEnvKeyChange(key, e.target.value)}
                      />
                      <Input
                        className={`h-8 text-xs font-mono flex-1 ${myKeysEnvErrors[key] ? "border-destructive" : ""}`}
                        placeholder="value"
                        value={value}
                        onChange={(e) => handleEnvValueChange(key, e.target.value)}
                        aria-invalid={!!myKeysEnvErrors[key]}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="shrink-0 text-muted-foreground hover:text-destructive"
                        onClick={() => handleRemoveEnvRow(key)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    {myKeysEnvErrors[key] && (
                      <p className="text-xs text-destructive pl-1">{myKeysEnvErrors[key]}</p>
                    )}
                  </div>
                ))}
                {Object.keys(myKeysEnv).length === 0 && (
                  <p className="text-xs text-muted-foreground text-center py-4">
                    No environment variables. Click &quot;Add&quot; to add one.
                  </p>
                )}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setMyKeysOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleSaveMyKeys} disabled={myKeysSaving}>
              {myKeysSaving && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              {t("myKeysSave")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
