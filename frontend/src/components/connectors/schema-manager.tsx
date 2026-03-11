"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { Search, RefreshCw, Loader2, Sparkles, Table2, Check, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { connectorApi } from "@/lib/api"
import { toast } from "sonner"
import type { SchemaTable, SchemaColumn } from "@/types/connector"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface SchemaManagerProps {
  connectorId: string
  refreshKey?: number
}

type SyncStatus = "idle" | "saving" | "saved" | "error"

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SchemaManager({ connectorId, refreshKey }: SchemaManagerProps) {
  const t = useTranslations("connectors")
  const tc = useTranslations("common")

  const [tables, setTables] = useState<SchemaTable[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isDiscovering, setIsDiscovering] = useState(false)
  const [isAnnotating, setIsAnnotating] = useState(false)
  const [annotateProgress, setAnnotateProgress] = useState<{ completed: number; total: number } | null>(null)
  const annotateAbortRef = useRef(false)
  const [selectedTableId, setSelectedTableId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [syncStatus, setSyncStatus] = useState<SyncStatus>("idle")

  // Debounce timers for auto-save
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const columnSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const syncResetRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const selectedTable = tables.find((t) => t.id === selectedTableId) ?? null

  const filteredTables = tables.filter(
    (tbl) =>
      tbl.table_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (tbl.display_name && tbl.display_name.toLowerCase().includes(searchQuery.toLowerCase())),
  )

  const visibleCount = tables.filter((tbl) => tbl.is_visible).length

  // Sync status helpers
  const markSaving = () => {
    if (syncResetRef.current) clearTimeout(syncResetRef.current)
    setSyncStatus("saving")
  }
  const markSaved = () => {
    setSyncStatus("saved")
    syncResetRef.current = setTimeout(() => setSyncStatus("idle"), 2000)
  }
  const markError = () => {
    setSyncStatus("error")
    syncResetRef.current = setTimeout(() => setSyncStatus("idle"), 3000)
  }

  // Load schemas
  const loadSchemas = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await connectorApi.getSchemas(connectorId)
      setTables(data)
    } catch (err) {
      console.error("Failed to load schemas:", err)
    } finally {
      setIsLoading(false)
    }
  }, [connectorId])

  useEffect(() => {
    loadSchemas()
  }, [loadSchemas])

  // Refresh when externally triggered (e.g., AI chat changed schema)
  useEffect(() => {
    if (refreshKey !== undefined && refreshKey > 0) {
      loadSchemas()
    }
  }, [refreshKey, loadSchemas])

  // Cleanup timers + abort any in-flight polling loop
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      if (columnSaveTimerRef.current) clearTimeout(columnSaveTimerRef.current)
      if (syncResetRef.current) clearTimeout(syncResetRef.current)
      annotateAbortRef.current = true
    }
  }, [])

  // Discover schema
  const handleDiscover = async () => {
    setIsDiscovering(true)
    try {
      const result = await connectorApi.introspect(connectorId)
      toast.success(
        t("tablesDiscovered", { count: result.tables_discovered }) +
          ` (${result.columns_discovered} columns)`,
      )
      await loadSchemas()
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error"
      toast.error(message)
    } finally {
      setIsDiscovering(false)
    }
  }

  // AI Annotate (single table)
  const handleAiAnnotate = async () => {
    if (!selectedTable) return
    setIsAnnotating(true)
    try {
      const result = await connectorApi.aiAnnotate(connectorId, {
        table_ids: [selectedTable.id],
      })
      toast.success(t("aiAnnotateSuccess", { count: result.annotated_count }))
      await loadSchemas()
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error"
      toast.error(message)
    } finally {
      setIsAnnotating(false)
    }
  }

  // AI Annotate All tables — background job with polling
  const handleAiAnnotateAll = async () => {
    setIsAnnotating(true)
    setAnnotateProgress(null)
    annotateAbortRef.current = false

    const toastId = toast.loading(t("aiAnnotateAllLoading"), {
      description: t("aiAnnotateAllHint"),
      duration: Infinity,
    })
    try {
      const { job_id } = await connectorApi.aiAnnotateAll(connectorId)

      // Poll every 2 s until done/error or component aborts
      while (!annotateAbortRef.current) {
        await new Promise((r) => setTimeout(r, 2000))
        if (annotateAbortRef.current) break

        const status = await connectorApi.getAnnotateStatus(connectorId, job_id)
        setAnnotateProgress({ completed: status.completed_batches, total: status.total_batches })

        if (status.status === "done") {
          toast.success(t("aiAnnotateAllSuccess", { count: status.annotated_count }), {
            id: toastId,
            duration: 4000,
          })
          await loadSchemas()
          break
        }
        if (status.status === "error") {
          toast.error(status.error ?? "Annotation failed", { id: toastId, duration: 4000 })
          break
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error"
      toast.error(message, { id: toastId, duration: 4000 })
    } finally {
      setIsAnnotating(false)
      setAnnotateProgress(null)
    }
  }

  // Table-level updates (debounced auto-save)
  const updateTableField = (
    tableId: string,
    field: "display_name" | "description" | "is_visible",
    value: string | boolean,
  ) => {
    setTables((prev) =>
      prev.map((tbl) =>
        tbl.id === tableId ? { ...tbl, [field]: value } : tbl,
      ),
    )
    markSaving()
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(async () => {
      try {
        await connectorApi.updateSchema(connectorId, tableId, {
          [field]: value,
        })
        markSaved()
      } catch {
        markError()
      }
    }, 600)
  }

  // Column-level updates (debounced auto-save)
  const updateColumnField = (
    tableId: string,
    columnId: string,
    field: "display_name" | "description" | "is_visible",
    value: string | boolean,
  ) => {
    setTables((prev) =>
      prev.map((tbl) =>
        tbl.id === tableId
          ? {
              ...tbl,
              columns: tbl.columns.map((col) =>
                col.id === columnId ? { ...col, [field]: value } : col,
              ),
            }
          : tbl,
      ),
    )
    markSaving()
    if (columnSaveTimerRef.current) clearTimeout(columnSaveTimerRef.current)
    columnSaveTimerRef.current = setTimeout(async () => {
      try {
        await connectorApi.updateSchemaColumn(connectorId, tableId, columnId, {
          [field]: value,
        })
        markSaved()
      } catch {
        markError()
      }
    }, 600)
  }

  // Toggle table visibility — immediate save (not debounced)
  const handleToggleVisible = async (tableId: string, currentVisible: boolean) => {
    const newVisible = !currentVisible
    setTables((prev) =>
      prev.map((tb) => (tb.id === tableId ? { ...tb, is_visible: newVisible } : tb)),
    )
    markSaving()
    try {
      await connectorApi.updateSchema(connectorId, tableId, { is_visible: newVisible })
      markSaved()
    } catch {
      setTables((prev) =>
        prev.map((tb) => (tb.id === tableId ? { ...tb, is_visible: currentVisible } : tb)),
      )
      markError()
    }
  }

  // Toggle column visibility — immediate save
  const handleToggleColumnVisible = async (
    tableId: string,
    col: SchemaColumn,
  ) => {
    const newVisible = !col.is_visible
    setTables((prev) =>
      prev.map((tb) =>
        tb.id === tableId
          ? { ...tb, columns: tb.columns.map((c) => c.id === col.id ? { ...c, is_visible: newVisible } : c) }
          : tb,
      ),
    )
    markSaving()
    try {
      await connectorApi.updateSchemaColumn(connectorId, tableId, col.id, { is_visible: newVisible })
      markSaved()
    } catch {
      setTables((prev) =>
        prev.map((tb) =>
          tb.id === tableId
            ? { ...tb, columns: tb.columns.map((c) => c.id === col.id ? { ...c, is_visible: col.is_visible } : c) }
            : tb,
        ),
      )
      markError()
    }
  }

  // ------- Render -------

  return (
    <div className="flex-1 flex min-h-0">
      {/* ---- Left panel: table list ---- */}
      <div className="w-[280px] border-r flex flex-col min-h-0 overflow-hidden">
        {/* Header with discover button */}
        <div className="p-3 border-b space-y-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleDiscover}
            disabled={isDiscovering}
            className="gap-1.5 w-full"
          >
            {isDiscovering ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            {isDiscovering ? t("discovering") : t("discoverSchema")}
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={handleAiAnnotateAll}
            disabled={isAnnotating || tables.length === 0}
            className="gap-1.5 w-full"
          >
            {isAnnotating ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            {isAnnotating && annotateProgress && annotateProgress.total > 0
              ? `${annotateProgress.completed} / ${annotateProgress.total} ${t("batches")}`
              : isAnnotating
              ? t("aiAnnotating")
              : t("aiAnnotateAll")}
          </Button>

          {/* Search */}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t("searchTables")}
              className="h-8 text-sm pl-8"
            />
          </div>

          {/* Visible count */}
          {tables.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {t("visibleTables", { visible: visibleCount, total: tables.length })}
            </p>
          )}
        </div>

        {/* Table list */}
        <ScrollArea className="flex-1 min-h-0">
          <div className="p-2 space-y-1">
            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : filteredTables.length === 0 ? (
              <div className="text-center py-8 space-y-2">
                <p className="text-xs text-muted-foreground">
                  {tables.length === 0 ? t("noTablesYet") : t("searchTables")}
                </p>
                {tables.length === 0 && (
                  <p className="text-xs text-muted-foreground/60">
                    {t("discoverSchemaHint")}
                  </p>
                )}
              </div>
            ) : (
              filteredTables.map((tbl) => (
                <div
                  key={tbl.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedTableId(tbl.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      setSelectedTableId(tbl.id)
                    }
                  }}
                  className={cn(
                    "group flex items-center gap-2 rounded-md border px-2.5 py-2 cursor-pointer transition-colors",
                    selectedTableId === tbl.id
                      ? "bg-accent border-border"
                      : "border-transparent hover:bg-muted/50",
                  )}
                >
                  <Switch
                    size="sm"
                    checked={tbl.is_visible}
                    onCheckedChange={() => handleToggleVisible(tbl.id, tbl.is_visible)}
                    onClick={(e) => e.stopPropagation()}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{tbl.table_name}</p>
                    {tbl.display_name && (
                      <p className="text-xs text-muted-foreground truncate">
                        {tbl.display_name}
                      </p>
                    )}
                  </div>
                  <span className="text-[10px] text-muted-foreground shrink-0">
                    {tbl.columns.length}
                  </span>
                </div>
              ))
            )}
          </div>
        </ScrollArea>
      </div>

      {/* ---- Right panel: table detail / columns ---- */}
      <div className="flex-1 flex flex-col min-h-0">
        {!selectedTable ? (
          <div className="flex-1 flex items-center justify-center">
            <p className="text-sm text-muted-foreground">{t("selectTable")}</p>
          </div>
        ) : (
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-4 space-y-4">
              {/* Table header row: name + sync status + AI button */}
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 min-w-0">
                  <Table2 className="h-4 w-4 text-muted-foreground shrink-0" />
                  <span className="text-sm font-semibold truncate">{selectedTable.table_name}</span>
                  {/* Inline sync status */}
                  {syncStatus !== "idle" && (
                    <span className={cn(
                      "flex items-center gap-1 text-xs shrink-0",
                      syncStatus === "saving" && "text-muted-foreground",
                      syncStatus === "saved" && "text-emerald-600 dark:text-emerald-400",
                      syncStatus === "error" && "text-destructive",
                    )}>
                      {syncStatus === "saving" && <Loader2 className="h-3 w-3 animate-spin" />}
                      {syncStatus === "saved" && <Check className="h-3 w-3" />}
                      {syncStatus === "error" && <AlertCircle className="h-3 w-3" />}
                      {syncStatus === "saving" && tc("syncing")}
                      {syncStatus === "saved" && tc("synced")}
                      {syncStatus === "error" && tc("syncFailed")}
                    </span>
                  )}
                </div>

                {/* AI Annotate button */}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleAiAnnotate}
                  disabled={isAnnotating}
                  className="gap-1.5 shrink-0"
                >
                  {isAnnotating ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5" />
                  )}
                  {isAnnotating ? t("aiAnnotating") : t("aiAnnotate")}
                </Button>
              </div>

              {/* Full-width fields */}
              <div className="space-y-3">
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">{t("displayName")}</label>
                  <Input
                    type="text"
                    value={selectedTable.display_name || ""}
                    onChange={(e) =>
                      updateTableField(selectedTable.id, "display_name", e.target.value)
                    }
                    placeholder={t("displayName")}
                    className="h-8 text-sm"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">{t("tableDescription")}</label>
                  <Textarea
                    value={selectedTable.description || ""}
                    onChange={(e) =>
                      updateTableField(selectedTable.id, "description", e.target.value)
                    }
                    placeholder={t("tableDescription")}
                    rows={2}
                    className="resize-none text-sm"
                  />
                </div>
              </div>

              {/* Columns table */}
              <div className="rounded-md border">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b bg-muted/50">
                        <th className="text-left font-medium text-muted-foreground px-3 py-2">
                          {t("columnName")}
                        </th>
                        <th className="text-left font-medium text-muted-foreground px-3 py-2">
                          {t("displayName")}
                        </th>
                        <th className="text-left font-medium text-muted-foreground px-3 py-2 w-[80px]">
                          {t("dataType")}
                        </th>
                        <th className="text-center font-medium text-muted-foreground px-3 py-2 w-[50px]">
                          {t("nullable")}
                        </th>
                        <th className="text-center font-medium text-muted-foreground px-3 py-2 w-[40px]">
                          {t("primaryKey")}
                        </th>
                        <th className="text-left font-medium text-muted-foreground px-3 py-2">
                          {tc("description")}
                        </th>
                        <th className="text-center font-medium text-muted-foreground px-3 py-2 w-[60px]">
                          {t("visible")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedTable.columns.map((col: SchemaColumn) => (
                        <tr key={col.id} className="border-b last:border-0">
                          <td className="px-3 py-1.5">
                            <span className="font-mono text-xs">{col.column_name}</span>
                          </td>
                          <td className="px-3 py-1.5">
                            <Input
                              type="text"
                              value={col.display_name || ""}
                              onChange={(e) =>
                                updateColumnField(
                                  selectedTable.id,
                                  col.id,
                                  "display_name",
                                  e.target.value,
                                )
                              }
                              placeholder="-"
                              className="h-7 text-xs border-transparent hover:border-input"
                            />
                          </td>
                          <td className="px-3 py-1.5">
                            <span className="text-xs text-muted-foreground font-mono">
                              {col.data_type}
                            </span>
                          </td>
                          <td className="px-3 py-1.5 text-center">
                            {col.is_nullable && (
                              <span className="text-xs text-muted-foreground">Y</span>
                            )}
                          </td>
                          <td className="px-3 py-1.5 text-center">
                            {col.is_primary_key && (
                              <span className="text-xs font-semibold text-amber-600 dark:text-amber-400">
                                PK
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-1.5">
                            <Input
                              type="text"
                              value={col.description || ""}
                              onChange={(e) =>
                                updateColumnField(
                                  selectedTable.id,
                                  col.id,
                                  "description",
                                  e.target.value,
                                )
                              }
                              placeholder="-"
                              className="h-7 text-xs border-transparent hover:border-input"
                            />
                          </td>
                          <td className="px-3 py-1.5 text-center">
                            <Switch
                              size="sm"
                              checked={col.is_visible}
                              onCheckedChange={() =>
                                handleToggleColumnVisible(selectedTable.id, col)
                              }
                            />
                          </td>
                        </tr>
                      ))}
                      {selectedTable.columns.length === 0 && (
                        <tr>
                          <td
                            colSpan={7}
                            className="px-3 py-6 text-center text-xs text-muted-foreground"
                          >
                            {t("noTablesYet")}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </ScrollArea>
        )}
      </div>
    </div>
  )
}
