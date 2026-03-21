"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { format } from "date-fns"
import {
  Key,
  Plus,
  Loader2,
  Trash2,
  Copy,
  Check,
  MoreHorizontal,
  CalendarIcon,
  Power,
  PowerOff,
} from "lucide-react"
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Calendar } from "@/components/ui/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { apiFetch } from "@/lib/api"
import { cn } from "@/lib/utils"

interface ApiKeyItem {
  id: string
  name: string
  key_prefix: string
  is_active: boolean
  expires_at: string | null
  last_used_at: string | null
  created_at: string
}

interface CreateApiKeyResponse {
  id: string
  name: string
  key: string
  key_prefix: string
  is_active: boolean
  expires_at: string | null
  created_at: string
}

export function ApiKeysSettings() {
  const t = useTranslations("settings.apiKeys")
  const tc = useTranslations("common")

  const [keys, setKeys] = useState<ApiKeyItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState("")
  const [expiresAt, setExpiresAt] = useState<Date | undefined>()
  const [expiresAtOpen, setExpiresAtOpen] = useState(false)
  const [isMutating, setIsMutating] = useState(false)

  // Show key after creation
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [showKeyOpen, setShowKeyOpen] = useState(false)
  const [keyCopied, setKeyCopied] = useState(false)

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<ApiKeyItem | null>(null)

  const loadKeys = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await apiFetch<{ items: ApiKeyItem[]; total: number }>("/api/me/api-keys")
      setKeys(data.items)
    } catch {
      toast.error(t("loadFailed"))
    } finally {
      setIsLoading(false)
    }
  }, [t])

  useEffect(() => {
    loadKeys()
  }, [loadKeys])

  const handleCreate = async () => {
    if (!createName.trim()) return
    setIsMutating(true)
    try {
      const payload: { name: string; expires_at?: string } = {
        name: createName.trim(),
      }
      if (expiresAt) {
        payload.expires_at = format(expiresAt, "yyyy-MM-dd")
      }
      const result = await apiFetch<CreateApiKeyResponse>("/api/me/api-keys", {
        method: "POST",
        body: JSON.stringify(payload),
      })
      toast.success(t("keyCreated"))
      setCreateOpen(false)
      setCreateName("")
      setExpiresAt(undefined)
      setCreatedKey(result.key)
      setShowKeyOpen(true)
      setKeyCopied(false)
      await loadKeys()
    } catch {
      toast.error(t("loadFailed"))
    } finally {
      setIsMutating(false)
    }
  }

  const handleCopyKey = async () => {
    if (!createdKey) return
    try {
      await navigator.clipboard.writeText(createdKey)
      setKeyCopied(true)
      toast.success(t("keyCopied"))
      setTimeout(() => setKeyCopied(false), 2000)
    } catch {
      // ignore
    }
  }

  const handleToggleActive = async (key: ApiKeyItem) => {
    setIsMutating(true)
    try {
      await apiFetch(`/api/me/api-keys/${key.id}/active`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !key.is_active }),
      })
      toast.success(t("toggleActiveSuccess"))
      await loadKeys()
    } catch {
      toast.error(t("toggleActiveFailed"))
    } finally {
      setIsMutating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsMutating(true)
    try {
      await apiFetch(`/api/me/api-keys/${deleteTarget.id}`, {
        method: "DELETE",
      })
      toast.success(t("deleteSuccess"))
      setDeleteTarget(null)
      await loadKeys()
    } catch {
      toast.error(t("deleteFailed"))
    } finally {
      setIsMutating(false)
    }
  }

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return t("never")
    return new Date(dateStr).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    })
  }

  return (
    <div className="space-y-4">
      {/* API Keys Active Banner */}
      <div className="flex items-start gap-3 rounded-md border border-emerald-500/30 bg-emerald-50/50 p-3 dark:bg-emerald-950/20">
        <Key className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
        <p className="text-sm text-emerald-800 dark:text-emerald-300">
          {t.rich("activeBanner", {
            link: (chunks) => (
              <a href="https://docs.fim.ai/api/authentication" target="_blank" rel="noopener noreferrer" className="underline hover:text-emerald-900 dark:hover:text-emerald-200">
                {chunks}
              </a>
            ),
          })}
        </p>
      </div>
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("description")}</p>
        </div>
        <Button onClick={() => setCreateOpen(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("createKey")}
        </Button>
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
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colName")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colKey")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCreated")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colExpires")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colLastUsed")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colStatus")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
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
                  <td className="px-4 py-3 font-medium text-foreground">{k.name}</td>
                  <td className="px-4 py-3">
                    <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                      {k.key_prefix}...
                    </code>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{formatDate(k.created_at)}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {k.expires_at ? formatDate(k.expires_at) : t("noExpiry")}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{formatDate(k.last_used_at)}</td>
                  <td className="px-4 py-3">
                    {k.is_active ? (
                      <Badge variant="outline" className="border-green-500/30 bg-green-50 text-green-700 dark:bg-green-950/20 dark:text-green-400">
                        {t("active")}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="border-red-500/30 bg-red-50 text-red-700 dark:bg-red-950/20 dark:text-red-400">
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
                        <DropdownMenuItem onClick={() => handleToggleActive(k)}>
                          {k.is_active ? (
                            <>
                              <PowerOff className="mr-2 h-4 w-4" />
                              {tc("disable")}
                            </>
                          ) : (
                            <>
                              <Power className="mr-2 h-4 w-4" />
                              {tc("enable")}
                            </>
                          )}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() => setDeleteTarget(k)}
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

      {/* Create API Key Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("createKey")}</DialogTitle>
            <DialogDescription>{t("description")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label className="text-sm font-medium">
                {t("keyNameLabel")} <span className="text-destructive">*</span>
              </Label>
              <Input
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder={t("keyNamePlaceholder")}
              />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">{t("expiresAtLabel")}</Label>
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

      {/* Show Key After Creation Dialog */}
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
            <DialogTitle>{t("keyCreatedTitle")}</DialogTitle>
            <DialogDescription>{t("keyCreatedWarning")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="flex items-center gap-2">
              <Input readOnly value={createdKey ?? ""} className="font-mono text-xs" />
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
                {tc("copy")}
              </Button>
            </div>
            <div className="rounded-md border border-amber-500/30 bg-amber-50 dark:bg-amber-950/20 px-3 py-2">
              <p className="text-xs text-amber-700 dark:text-amber-400 font-medium">
                {t("keyCreatedWarning")}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => { setShowKeyOpen(false); setCreatedKey(null); setKeyCopied(false) }}>
              {tc("done")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("deleteConfirmDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
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
