"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations, useLocale } from "next-intl"
import { MoreHorizontal, Search, Loader2, Eye, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { formatTokens } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
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
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import type { AdminConversation, AdminMessage } from "@/types/admin"

const PAGE_SIZE = 20

const ROLE_BADGE_VARIANT: Record<string, string> = {
  user: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  assistant: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  system: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  tool: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
}

export function AdminConversations() {
  const t = useTranslations("admin.conversations")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()
  const [conversations, setConversations] = useState<AdminConversation[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState("")
  const [isLoading, setIsLoading] = useState(true)
  const [deleteTarget, setDeleteTarget] = useState<AdminConversation | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // View dialog state
  const [viewTarget, setViewTarget] = useState<AdminConversation | null>(null)
  const [messages, setMessages] = useState<AdminMessage[]>([])
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)

  const loadConversations = useCallback(async () => {
    setIsLoading(true)
    try {
      const res = await adminApi.listAllConversations({ page, size: PAGE_SIZE, q: search || undefined })
      setConversations(res.items)
      setTotal(res.total)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, search])

  useEffect(() => { loadConversations() }, [loadConversations])

  const handleSearchChange = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(value)
      setPage(1)
    }, 300)
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await adminApi.adminDeleteConversation(deleteTarget.id)
      toast.success(t("conversationDeleted"))
      setDeleteTarget(null)
      loadConversations()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const handleView = async (conv: AdminConversation) => {
    setViewTarget(conv)
    setMessages([])
    setIsLoadingMessages(true)
    try {
      const msgs = await adminApi.getConversationMessages(conv.id)
      setMessages(msgs)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoadingMessages(false)
    }
  }

  const getRoleBadgeLabel = (role: string): string => {
    const key = `messageRole.${role}` as const
    // next-intl returns the key if not found, so fall back to raw role
    const label = t(key)
    return label === key ? role : label
  }

  const pages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            className="pl-9"
            onChange={(e) => handleSearchChange(e.target.value)}
          />
        </div>
        <span className="text-sm text-muted-foreground shrink-0">{t("totalLabel", { count: total })}</span>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : conversations.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noConversations")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("userColumn")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("titleColumn")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("modeColumn")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("modelColumn")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("tokensColumn")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("msgsColumn")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("dateColumn")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {conversations.map((conv) => (
                <tr key={conv.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{conv.username || conv.email || "--"}</td>
                  <td className="px-4 py-3 max-w-[200px] truncate text-muted-foreground">
                    {conv.title ?? t("untitled")}
                  </td>
                  <td className="px-4 py-3">
                    {conv.mode ? <Badge variant="outline">{conv.mode}</Badge> : <span className="text-muted-foreground/50">--</span>}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{conv.model_name ?? "--"}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{formatTokens(conv.total_tokens)}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{conv.message_count}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {new Date(conv.created_at).toLocaleDateString(locale)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleView(conv)}>
                          <Eye className="mr-2 h-4 w-4" />
                          {t("view")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(conv)}>
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

      {/* Pagination */}
      {!isLoading && conversations.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t("totalConversations", { count: total })}</span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              {t("previous")}
            </Button>
            <span>
              {t("pageOf", { page, pages })}
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

      {/* View Messages Sheet (right drawer) */}
      <Sheet open={!!viewTarget} onOpenChange={(open) => !open && setViewTarget(null)}>
        <SheetContent side="right" className="sm:max-w-2xl w-full flex flex-col p-0">
          <SheetHeader className="px-6 pt-6 pb-4 border-b border-border/40 shrink-0">
            <SheetTitle>{t("viewTitle")}</SheetTitle>
            <SheetDescription>
              {t("viewSubtitle", {
                title: viewTarget?.title ?? t("untitled"),
                username: viewTarget?.username || viewTarget?.email || "--",
              })}
            </SheetDescription>
          </SheetHeader>

          {isLoadingMessages ? (
            <div className="flex items-center justify-center flex-1">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground mr-2" />
              <span className="text-sm text-muted-foreground">{t("loadingMessages")}</span>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
              {t("noMessages")}
            </div>
          ) : (
            <ScrollArea className="flex-1 min-h-0">
              <div className="space-y-4 px-6 py-4">
                {messages.map((msg) => (
                  <div key={msg.id} className="space-y-1.5">
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${ROLE_BADGE_VARIANT[msg.role] ?? ROLE_BADGE_VARIANT.system}`}
                      >
                        {getRoleBadgeLabel(msg.role)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {new Date(msg.created_at).toLocaleString(locale)}
                      </span>
                    </div>
                    <div className="rounded-md border border-border bg-muted/20 px-3 py-2 text-sm whitespace-pre-wrap break-words">
                      {msg.content || <span className="italic text-muted-foreground/50">--</span>}
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          )}
        </SheetContent>
      </Sheet>

      {/* Delete AlertDialog */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteDesc", { title: deleteTarget?.title ?? t("untitled"), username: deleteTarget?.username || deleteTarget?.email || "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
