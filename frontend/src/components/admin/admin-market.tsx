"use client"

import { useState, useEffect, useCallback, useMemo } from "react"
import { useTranslations } from "next-intl"
import { Upload, Loader2, Store, MoreHorizontal, EyeOff } from "lucide-react"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi } from "@/lib/api"
import type { MarketResourceItem } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { useDateFormatter } from "@/hooks/use-date-formatter"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const RESOURCE_TYPE_KEYS: Record<string, string> = {
  agent: "typeAgent",
  skill: "typeSkill",
  connector: "typeConnector",
  mcp_server: "typeMcpServer",
  workflow: "typeWorkflow",
  knowledge_base: "typeKnowledgeBase",
}

const FILTER_TYPES = [
  { key: "all", labelKey: "filterAll" },
  { key: "agent", labelKey: "typeAgent" },
  { key: "skill", labelKey: "typeSkill" },
  { key: "connector", labelKey: "typeConnector" },
  { key: "mcp_server", labelKey: "typeMcpServer" },
  { key: "workflow", labelKey: "typeWorkflow" },
  { key: "knowledge_base", labelKey: "typeKnowledgeBase" },
] as const

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AdminMarket() {
  const t = useTranslations("admin.market")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDate } = useDateFormatter()

  // ---- State ----
  const [resources, setResources] = useState<MarketResourceItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isImporting, setIsImporting] = useState(false)
  const [showImportConfirm, setShowImportConfirm] = useState(false)
  const [activeFilter, setActiveFilter] = useState("all")

  // ---- Filtered resources ----
  const filteredResources = useMemo(() => {
    if (activeFilter === "all") return resources
    return resources.filter((r) => r.resource_type === activeFilter)
  }, [resources, activeFilter])

  // ---- Data fetching ----

  const loadResources = useCallback(async () => {
    setIsLoading(true)
    try {
      const res = await adminApi.listMarketResources()
      setResources(res.items)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    loadResources()
  }, [loadResources])

  // ---- Import handler ----

  const handleImport = async () => {
    setShowImportConfirm(false)
    setIsImporting(true)
    try {
      const result = await adminApi.importMarketTemplates()
      toast.success(t("importSuccess", { created: result.created, updated: result.updated }))
      loadResources()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsImporting(false)
    }
  }

  // ---- Take Down handler ----

  const handleTakeDown = async (resource: MarketResourceItem) => {
    try {
      await adminApi.unpublishMarketResource(resource.resource_type, resource.id)
      toast.success(t("takeDownSuccess"))
      loadResources()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  // ---- Helpers ----

  const getTypeLabel = (resourceType: string): string => {
    const key = RESOURCE_TYPE_KEYS[resourceType]
    return key ? t(key) : resourceType
  }

  // ---- Render ----

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("description")}</p>
      </div>

      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <Button
          onClick={() => setShowImportConfirm(true)}
          disabled={isImporting}
          className="gap-1.5"
        >
          {isImporting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("importing")}
            </>
          ) : (
            <>
              <Upload className="h-4 w-4" />
              {t("importButton")}
            </>
          )}
        </Button>
        {!isLoading && resources.length > 0 && (
          <span className="text-sm text-muted-foreground">
            {t("templateCount", { count: resources.length })}
          </span>
        )}
      </div>

      {/* Type filter pills */}
      {!isLoading && resources.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {FILTER_TYPES.map(({ key, labelKey }) => (
            <Button
              key={key}
              variant={activeFilter === key ? "default" : "outline"}
              size="sm"
              className="h-7 text-xs"
              onClick={() => setActiveFilter(key)}
            >
              {t(labelKey)}
            </Button>
          ))}
        </div>
      )}

      {/* Table / Empty / Loading */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : resources.length === 0 ? (
        <div className="flex items-center justify-center rounded-lg border border-dashed border-border/60 py-16">
          <div className="max-w-md text-center space-y-4">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-muted">
              <Store className="h-7 w-7 text-muted-foreground" />
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">{t("emptyTitle")}</p>
              <p className="mt-1 text-sm text-muted-foreground leading-relaxed">
                {t("emptyDescription")}
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colName")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colType")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colOwner")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colCreated")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filteredResources.map((resource) => (
                <tr key={`${resource.resource_type}-${resource.id}`} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3">
                    <p className="font-medium text-foreground">{resource.name}</p>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant="secondary" className="text-xs">
                      {getTypeLabel(resource.resource_type)}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-sm">
                    {resource.owner_username ?? "-"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {formatDate(resource.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleTakeDown(resource)}>
                          <EyeOff className="mr-2 h-4 w-4" />
                          {t("takeDown")}
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

      {/* Import Confirm AlertDialog */}
      <AlertDialog open={showImportConfirm} onOpenChange={setShowImportConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("importConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("importConfirmDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleImport}>
              {t("importButton")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
