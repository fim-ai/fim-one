"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  Eye,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plus,
  Power,
  Trash2,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { orgApi, type UserOrg } from "@/lib/api"
import { channelsApi } from "@/lib/api/channels"
import { getErrorMessage } from "@/lib/error-utils"
import { ChannelDetailsSheet } from "@/components/channels/channel-details-sheet"
import { ChannelFormDialog } from "@/components/channels/channel-form-dialog"
import type { Channel } from "@/types/channel"

export function ChannelsSettings() {
  const t = useTranslations("channels")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDateTime } = useDateFormatter()

  const [orgs, setOrgs] = useState<UserOrg[]>([])
  const [orgId, setOrgId] = useState<string>("")
  const [orgsLoading, setOrgsLoading] = useState(true)

  const [channels, setChannels] = useState<Channel[]>([])
  const [loading, setLoading] = useState(true)

  const currentOrg = orgs.find((o) => o.id === orgId) ?? null
  const canManage =
    currentOrg?.role === "owner" || currentOrg?.role === "admin"

  // Dialog / sheet targets
  const [formOpen, setFormOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<Channel | null>(null)
  const [detailsTarget, setDetailsTarget] = useState<Channel | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Channel | null>(null)
  const [isMutating, setIsMutating] = useState(false)

  // Load organizations once — the first one becomes the default selection.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const data = await orgApi.list()
        if (cancelled) return
        setOrgs(data)
        if (data.length > 0) setOrgId(data[0].id)
      } catch (err: unknown) {
        if (cancelled) return
        toast.error(getErrorMessage(err, tError))
      } finally {
        if (!cancelled) setOrgsLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [tError])

  const loadChannels = useCallback(
    async (targetOrgId: string) => {
      setLoading(true)
      try {
        const res = await channelsApi.list(targetOrgId)
        setChannels(res.items)
      } catch (err: unknown) {
        toast.error(getErrorMessage(err, tError))
      } finally {
        setLoading(false)
      }
    },
    [tError],
  )

  useEffect(() => {
    if (!orgId) return
    loadChannels(orgId)
  }, [orgId, loadChannels])

  // Mutation handlers
  const handleSaved = (saved: Channel) => {
    setChannels((prev) => {
      const idx = prev.findIndex((c) => c.id === saved.id)
      if (idx === -1) return [saved, ...prev]
      const next = [...prev]
      next[idx] = saved
      return next
    })
    setEditTarget(null)
  }

  const handleToggleActive = async (ch: Channel) => {
    setIsMutating(true)
    try {
      const updated = await channelsApi.update(ch.id, {
        is_active: !ch.is_active,
      })
      setChannels((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
      toast.success(
        updated.is_active ? t("messages.enabled") : t("messages.disabled"),
      )
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsMutating(true)
    try {
      await channelsApi.delete(deleteTarget.id)
      setChannels((prev) => prev.filter((c) => c.id !== deleteTarget.id))
      toast.success(t("messages.deleted"))
      setDeleteTarget(null)
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Render ---
  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-0.5">
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("description")}</p>
        </div>
        <Button
          className="gap-1.5"
          onClick={() => {
            setEditTarget(null)
            setFormOpen(true)
          }}
          disabled={!orgId || !canManage}
        >
          <Plus className="h-4 w-4" />
          {t("create")}
        </Button>
      </div>

      {/* Org selector */}
      {orgs.length > 1 && (
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">
            {t("orgSelector.label")}
          </span>
          <Select value={orgId} onValueChange={setOrgId}>
            <SelectTrigger className="w-72">
              <SelectValue placeholder={t("orgSelector.placeholder")} />
            </SelectTrigger>
            <SelectContent>
              {orgs.map((o) => (
                <SelectItem key={o.id} value={o.id}>
                  {o.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {/* Member read-only banner */}
      {!orgsLoading && currentOrg && !canManage && (
        <div className="rounded-md border border-amber-500/30 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
          {t("orgSelector.memberReadOnly")}
        </div>
      )}

      {/* No org state */}
      {!orgsLoading && orgs.length === 0 && (
        <div className="rounded-md border border-border bg-muted/30 p-8 text-center">
          <MessageSquare className="mx-auto h-8 w-8 text-muted-foreground/50 mb-2" />
          <p className="text-sm text-muted-foreground">
            {t("orgSelector.noOrgs")}
          </p>
          <Button asChild variant="outline" className="mt-4">
            <Link href="/settings?tab=organizations">
              {t("orgSelector.goToOrgs")}
            </Link>
          </Button>
        </div>
      )}

      {/* Loading skeletons */}
      {(orgsLoading || loading) && orgs.length > 0 && (
        <div className="rounded-md border border-border overflow-hidden">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton.TableRow key={i} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && !orgsLoading && orgs.length > 0 && channels.length === 0 && (
        <div className="rounded-md border border-dashed border-border bg-muted/20 p-10 text-center">
          <MessageSquare className="mx-auto h-8 w-8 text-muted-foreground/50 mb-3" />
          <h3 className="text-sm font-semibold text-foreground">
            {t("empty.title")}
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {canManage
              ? t("empty.description")
              : t("orgSelector.memberReadOnlyEmpty")}
          </p>
          {canManage && (
            <Button
              className="mt-4 gap-1.5"
              onClick={() => {
                setEditTarget(null)
                setFormOpen(true)
              }}
              disabled={!orgId}
            >
              <Plus className="h-4 w-4" />
              {t("empty.cta")}
            </Button>
          )}
        </div>
      )}

      {/* Table */}
      {!loading && !orgsLoading && channels.length > 0 && (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("columns.name")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("columns.type")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("columns.chat")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("columns.status")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("columns.updated")}
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  {tc("actions")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {channels.map((ch) => (
                <tr
                  key={ch.id}
                  className={`hover:bg-muted/20 transition-colors ${!ch.is_active ? "opacity-60" : ""}`}
                >
                  <td className="px-4 py-3 font-medium text-foreground">
                    {ch.name}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {t(`types.${ch.type}`)}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {ch.config.chat_name ?? (
                      <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                        {ch.config.chat_id ?? t("noChat")}
                      </code>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {ch.is_active ? (
                      <Badge
                        variant="outline"
                        className="border-green-500/30 bg-green-50 text-green-700 dark:bg-green-950/20 dark:text-green-400"
                      >
                        {t("status.enabled")}
                      </Badge>
                    ) : (
                      <Badge
                        variant="outline"
                        className="border-border bg-muted text-muted-foreground"
                      >
                        {t("status.disabled")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {formatDateTime(ch.updated_at ?? ch.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0"
                        >
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() => setDetailsTarget(ch)}
                        >
                          <Eye className="mr-2 h-4 w-4" />
                          {t("actions.viewDetails")}
                        </DropdownMenuItem>
                        {canManage && (
                          <DropdownMenuItem
                            onClick={() => {
                              setEditTarget(ch)
                              setFormOpen(true)
                            }}
                          >
                            <Pencil className="mr-2 h-4 w-4" />
                            {t("actions.edit")}
                          </DropdownMenuItem>
                        )}
                        {canManage && (
                          <DropdownMenuItem
                            onClick={() => handleToggleActive(ch)}
                            disabled={isMutating}
                          >
                            <Power className="mr-2 h-4 w-4" />
                            {ch.is_active
                              ? t("actions.disable")
                              : t("actions.enable")}
                          </DropdownMenuItem>
                        )}
                        {canManage && (
                          <>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() => setDeleteTarget(ch)}
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

      {/* Form dialog */}
      <ChannelFormDialog
        open={formOpen}
        onOpenChange={(next) => {
          setFormOpen(next)
          if (!next) setEditTarget(null)
        }}
        channel={editTarget}
        orgId={orgId}
        onSaved={handleSaved}
      />

      {/* Details sheet */}
      <ChannelDetailsSheet
        channel={detailsTarget}
        onOpenChange={(open) => {
          if (!open) setDetailsTarget(null)
        }}
      />

      {/* Delete confirmation */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteDialog.title")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteDialog.description", {
                name: deleteTarget?.name ?? "",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleDelete}
              disabled={isMutating}
            >
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
