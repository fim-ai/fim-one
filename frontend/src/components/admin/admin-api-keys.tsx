"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { format } from "date-fns"
import { Key, Plus, Loader2, Trash2, Copy, Check, MoreHorizontal, CalendarIcon, ShieldOff } from "lucide-react"
import { useDateFormatter } from "@/hooks/use-date-formatter"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Calendar } from "@/components/ui/calendar"
import { Checkbox } from "@/components/ui/checkbox"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { cn } from "@/lib/utils"

const AVAILABLE_SCOPES = ["chat", "agents", "kb", "connectors", "admin"] as const

interface ApiKeyInfo {
  id: string
  name: string
  key_prefix: string
  scopes: string | null
  is_active: boolean
  user_id: string | null
  expires_at: string | null
  last_used_at: string | null
  total_requests: number
  created_at: string
}

interface CreateApiKeyResponse {
  id: string
  name: string
  key: string
  key_prefix: string
  scopes: string | null
  is_active: boolean
  user_id: string | null
  expires_at: string | null
  last_used_at: string | null
  total_requests: number
  created_at: string
}

type ActionTarget = { key: ApiKeyInfo; action: "revoke" | "delete" }

export function AdminApiKeys() {
  const t = useTranslations("admin.apiKeys")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDate, formatDateTime } = useDateFormatter()

  // --- List state ---
  const [keys, setKeys] = useState<ApiKeyInfo[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [isLoading, setIsLoading] = useState(true)

  // --- Dialog states ---
  const [createOpen, setCreateOpen] = useState(false)
  const [actionTarget, setActionTarget] = useState<ActionTarget | null>(null)

  // --- Create form fields ---
  const [createName, setCreateName] = useState("")
  const [selectedScopes, setSelectedScopes] = useState<string[]>([])
  const [expiresAt, setExpiresAt] = useState<Date | undefined>()
  const [expiresAtOpen, setExpiresAtOpen] = useState(false)

  // --- Show key after creation ---
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [showKeyOpen, setShowKeyOpen] = useState(false)
  const [keyCopied, setKeyCopied] = useState(false)

  // --- Mutation loading ---
  const [isMutating, setIsMutating] = useState(false)

  // --- Load keys ---
  const loadKeys = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await adminApi.listApiKeys({ page, size: 20 })
      setKeys(data.items)
      setTotal(data.total)
      setPages(Math.max(1, Math.ceil(data.total / 20)))
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, tError])

  useEffect(() => {
    loadKeys()
  }, [loadKeys])

  // --- Create key ---
  const handleCreate = async () => {
    if (!createName.trim()) return
    setIsMutating(true)
    try {
      const payload: { name: string; scopes?: string; expires_at?: string } = {
        name: createName.trim(),
      }
      if (selectedScopes.length > 0) {
        payload.scopes = selectedScopes.join(",")
      }
      if (expiresAt) {
        payload.expires_at = format(expiresAt, "yyyy-MM-dd")
      }
      const result: CreateApiKeyResponse = await adminApi.createApiKey(payload)
      toast.success(t("keyCreated"))
      setCreateOpen(false)
      setCreateName("")
      setSelectedScopes([])
      setExpiresAt(undefined)
      // Show the full key
      setCreatedKey(result.key)
      setShowKeyOpen(true)
      setKeyCopied(false)
      await loadKeys()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Copy key to clipboard ---
  const handleCopyKey = async () => {
    if (!createdKey) return
    try {
      await navigator.clipboard.writeText(createdKey)
      setKeyCopied(true)
      toast.success(t("keyCopied"))
      setTimeout(() => setKeyCopied(false), 2000)
    } catch {
      toast.error("Failed to copy")
    }
  }

  // --- Revoke key (soft-delete: set is_active=false) ---
  const handleRevoke = async () => {
    if (!actionTarget || actionTarget.action !== "revoke") return
    setIsMutating(true)
    try {
      await adminApi.toggleApiKey(actionTarget.key.id, false)
      toast.success(t("keyRevoked"))
      setActionTarget(null)
      await loadKeys()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Delete key (permanent removal from DB) ---
  const handleDelete = async () => {
    if (!actionTarget || actionTarget.action !== "delete") return
    setIsMutating(true)
    try {
      await adminApi.deleteApiKey(actionTarget.key.id)
      toast.success(t("keyDeleted"))
      setActionTarget(null)
      await loadKeys()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Confirm action handler ---
  const handleConfirmAction = () => {
    if (!actionTarget) return
    if (actionTarget.action === "revoke") {
      handleRevoke()
    } else {
      handleDelete()
    }
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Button onClick={() => setCreateOpen(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("createKey")}
        </Button>
      </div>

      {/* API Keys Active banner */}
      <div className="rounded-md border border-emerald-500/30 bg-emerald-50 dark:bg-emerald-950/20 px-4 py-3 flex items-start gap-3">
        <Key className="h-4 w-4 text-emerald-600 dark:text-emerald-400 mt-0.5 shrink-0" />
        <div>
          <p className="text-sm font-medium text-emerald-700 dark:text-emerald-300">{t("activeBannerTitle")}</p>
          <p className="text-xs text-emerald-600/80 dark:text-emerald-400/80 mt-0.5">
            {t.rich("activeBannerDesc", {
              link: (chunks) => (
                <a href="https://docs.fim.ai/api/authentication" target="_blank" rel="noopener noreferrer" className="underline hover:text-emerald-800 dark:hover:text-emerald-200">
                  {chunks}
                </a>
              ),
            })}
          </p>
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : keys.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-8 text-center">
          <Key className="mx-auto h-8 w-8 text-muted-foreground/50 mb-2" />
          <p className="text-sm text-muted-foreground">{t("noKeys")}</p>
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colName")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colPrefix")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colStatus")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colScopes")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colLastUsed")}
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  {t("colRequests")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("colCreated")}
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  {tc("actions")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {keys.map((k) => (
                <tr
                  key={k.id}
                  className={cn(
                    "hover:bg-muted/20 transition-colors",
                    !k.is_active && "opacity-50",
                  )}
                >
                  <td className="px-4 py-3 font-medium text-foreground">
                    {k.name}
                  </td>
                  <td className="px-4 py-3">
                    <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                      {k.key_prefix}...
                    </code>
                  </td>
                  <td className="px-4 py-3">
                    {k.is_active ? (
                      <Badge variant="outline" className="border-green-500/30 bg-green-50 text-green-700 dark:bg-green-950/20 dark:text-green-400">
                        {t("active")}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="border-red-500/30 bg-red-50 text-red-700 dark:bg-red-950/20 dark:text-red-400">
                        {t("revoked")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {k.scopes ? (
                      <span className="text-xs">{k.scopes}</span>
                    ) : (
                      <span className="text-muted-foreground/50">--</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {formatDateTime(k.last_used_at, t("never"))}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {k.total_requests.toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {formatDate(k.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {k.is_active ? (
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => setActionTarget({ key: k, action: "revoke" })}
                          >
                            <ShieldOff className="mr-2 h-4 w-4" />
                            {t("revoke")}
                          </DropdownMenuItem>
                        ) : (
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => setActionTarget({ key: k, action: "delete" })}
                          >
                            <Trash2 className="mr-2 h-4 w-4" />
                            {tc("delete")}
                          </DropdownMenuItem>
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

      {/* Pagination */}
      {!isLoading && keys.length > 0 && pages > 1 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{total} total</span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              {tc("back")}
            </Button>
            <span>
              {page} / {pages}
            </span>
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

      {/* --- Create API Key Dialog --- */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("createKey")}</DialogTitle>
            <DialogDescription>
              {t("subtitle")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label className="text-sm font-medium">
                {t("keyName")} <span className="text-destructive">*</span>
              </Label>
              <Input
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder={t("keyName")}
              />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">{t("scopes")}</Label>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {AVAILABLE_SCOPES.map((scope) => (
                  <label key={scope} className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm cursor-pointer hover:bg-accent/50 transition-colors">
                    <Checkbox
                      checked={selectedScopes.includes(scope)}
                      onCheckedChange={(checked) => {
                        setSelectedScopes(prev =>
                          checked ? [...prev, scope] : prev.filter(s => s !== scope)
                        )
                      }}
                    />
                    {scope}
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">{t("scopesHint")}</p>
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">{t("expiresAt")}</Label>
              <Popover open={expiresAtOpen} onOpenChange={setExpiresAtOpen}>
                <PopoverTrigger asChild>
                  <button className={cn(
                    "flex w-full items-center gap-2 rounded-md border border-input bg-transparent px-3 h-9 text-sm shadow-xs transition-[color,box-shadow] focus-visible:outline-2 focus-visible:outline-ring/70",
                    !expiresAt && "text-muted-foreground",
                  )}>
                    <CalendarIcon className="size-4 text-muted-foreground" />
                    {expiresAt ? format(expiresAt, "yyyy-MM-dd") : t("expiresAtPlaceholder")}
                  </button>
                </PopoverTrigger>
                <PopoverContent className="w-auto overflow-hidden p-0" align="start">
                  <Calendar
                    mode="single"
                    captionLayout="dropdown"
                    selected={expiresAt}
                    onSelect={(date) => { setExpiresAt(date); setExpiresAtOpen(false) }}
                    disabled={(date) => date < new Date()}
                    startMonth={new Date()}
                    endMonth={new Date(new Date().getFullYear() + 2, 11)}
                  />
                </PopoverContent>
              </Popover>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleCreate}
              disabled={isMutating || !createName.trim()}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Show Key After Creation Dialog --- */}
      <Dialog
        open={showKeyOpen}
        onOpenChange={(open) => {
          if (!open) {
            setShowKeyOpen(false)
            setCreatedKey(null)
            setKeyCopied(false)
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("showKeyTitle")}</DialogTitle>
            <DialogDescription>{t("showKeyDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="flex items-center gap-2">
              <Input
                readOnly
                value={createdKey ?? ""}
                className="font-mono text-xs"
              />
              <Button
                variant="outline"
                size="sm"
                className="shrink-0 gap-1.5"
                onClick={handleCopyKey}
              >
                {keyCopied ? (
                  <Check className="h-4 w-4 text-green-600" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
                {t("copyKey")}
              </Button>
            </div>
            <div className="rounded-md border border-amber-500/30 bg-amber-50 dark:bg-amber-950/20 px-3 py-2">
              <p className="text-xs text-amber-700 dark:text-amber-400 font-medium">
                {t("keyWarning")}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button
              onClick={() => {
                setShowKeyOpen(false)
                setCreatedKey(null)
                setKeyCopied(false)
              }}
            >
              {tc("done")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Revoke / Delete API Key AlertDialog --- */}
      <AlertDialog
        open={actionTarget !== null}
        onOpenChange={(open) => { if (!open) setActionTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {actionTarget?.action === "revoke"
                ? t("revokeConfirmTitle")
                : t("deleteTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {actionTarget?.action === "revoke"
                ? t("revokeConfirmDesc")
                : t("deleteDesc")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleConfirmAction}
              disabled={isMutating}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {actionTarget?.action === "revoke" ? t("revoke") : tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
