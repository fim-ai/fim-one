"use client"

import { useState, useEffect, useCallback } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import {
  Loader2,
  BookMarked,
  MoreHorizontal,
  Unlink,
  ExternalLink,
} from "lucide-react"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { apiFetch } from "@/lib/api"

interface SubItem {
  id: string
  resource_type: string
  resource_name: string
  resource_id: string
  org_id: string
  publisher: string | null
  subscribed_at: string
}

const TYPE_LABEL_KEYS: Record<string, string> = {
  agent: "typeAgent",
  connector: "typeConnector",
  knowledge_base: "typeKb",
  kb: "typeKb",
  mcp_server: "typeMcp",
  mcp: "typeMcp",
  workflow: "typeWorkflow",
}

const TYPE_COLORS: Record<string, string> = {
  agent: "border-blue-500/30 bg-blue-50 text-blue-700 dark:bg-blue-950/20 dark:text-blue-400",
  connector: "border-purple-500/30 bg-purple-50 text-purple-700 dark:bg-purple-950/20 dark:text-purple-400",
  knowledge_base: "border-amber-500/30 bg-amber-50 text-amber-700 dark:bg-amber-950/20 dark:text-amber-400",
  kb: "border-amber-500/30 bg-amber-50 text-amber-700 dark:bg-amber-950/20 dark:text-amber-400",
  mcp_server: "border-green-500/30 bg-green-50 text-green-700 dark:bg-green-950/20 dark:text-green-400",
  mcp: "border-green-500/30 bg-green-50 text-green-700 dark:bg-green-950/20 dark:text-green-400",
  workflow: "border-pink-500/30 bg-pink-50 text-pink-700 dark:bg-pink-950/20 dark:text-pink-400",
}

function getResourceUrl(type: string, id: string): string {
  switch (type) {
    case "agent": return `/agents/${id}`
    case "connector": return `/connectors/${id}`
    case "knowledge_base":
    case "kb": return `/kb/${id}`
    case "mcp_server":
    case "mcp": return `/connectors?tab=mcp`
    case "workflow": return `/workflows/${id}`
    default: return "#"
  }
}

export function SubscriptionsSettings() {
  const t = useTranslations("settings.subscriptions")
  const tc = useTranslations("common")
  const { formatDate } = useDateFormatter()

  const [items, setItems] = useState<SubItem[]>([])
  const [loading, setLoading] = useState(true)
  const [unsubTarget, setUnsubTarget] = useState<SubItem | null>(null)
  const [unsubbing, setUnsubbing] = useState(false)

  const loadSubscriptions = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: SubItem[]; total: number }>("/api/me/subscriptions")
      setItems(data.items)
    } catch {
      toast.error(t("loadFailed"))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    loadSubscriptions()
  }, [loadSubscriptions])

  const handleUnsubscribe = async () => {
    if (!unsubTarget) return
    setUnsubbing(true)
    try {
      await apiFetch("/api/market/unsubscribe", {
        method: "DELETE",
        body: JSON.stringify({
          resource_type: unsubTarget.resource_type,
          resource_id: unsubTarget.resource_id,
          org_id: unsubTarget.org_id,
        }),
      })
      toast.success(t("unsubscribeSuccess"))
      setUnsubTarget(null)
      await loadSubscriptions()
    } catch {
      toast.error(t("unsubscribeFailed"))
    } finally {
      setUnsubbing(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("description")}</p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-8 text-center space-y-3">
          <BookMarked className="mx-auto h-8 w-8 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">{t("noSubscriptions")}</p>
          <Button variant="outline" size="sm" asChild>
            <Link href="/market">
              {t("browseMarket")}
            </Link>
          </Button>
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colType")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colName")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colPublisher")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colSubscribedDate")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((item) => (
                <tr key={item.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3">
                    <Badge
                      variant="outline"
                      className={TYPE_COLORS[item.resource_type] || ""}
                    >
                      {t(TYPE_LABEL_KEYS[item.resource_type] || item.resource_type)}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 font-medium text-foreground">{item.resource_name}</td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {item.publisher || "--"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{formatDate(item.subscribed_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem asChild>
                          <Link href={getResourceUrl(item.resource_type, item.resource_id)}>
                            <ExternalLink className="mr-2 h-4 w-4" />
                            {tc("view")}
                          </Link>
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() => setUnsubTarget(item)}
                        >
                          <Unlink className="mr-2 h-4 w-4" />
                          {t("unsubscribe")}
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

      {/* Unsubscribe Confirmation */}
      <AlertDialog open={unsubTarget !== null} onOpenChange={(open) => { if (!open) setUnsubTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("unsubscribeConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("unsubscribeConfirmDescription", { name: unsubTarget?.resource_name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleUnsubscribe}
              disabled={unsubbing}
            >
              {unsubbing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("unsubscribe")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
