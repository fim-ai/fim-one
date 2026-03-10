"use client"

import { useState } from "react"
import { Play, Trash2, Loader2, AlertTriangle, Lock } from "lucide-react"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { connectorApi } from "@/lib/api"
import { toast } from "sonner"
import type { DbConnectionConfig, QueryResponse } from "@/types/connector"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface QueryPlaygroundProps {
  connectorId: string
  dbConfig?: DbConnectionConfig | null
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function QueryPlayground({ connectorId, dbConfig }: QueryPlaygroundProps) {
  const t = useTranslations("connectors")

  const [sql, setSql] = useState("")
  const [isRunning, setIsRunning] = useState(false)
  const [result, setResult] = useState<QueryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleRun = async () => {
    const trimmed = sql.trim()
    if (!trimmed || isRunning) return

    setIsRunning(true)
    setResult(null)
    setError(null)
    try {
      const data = await connectorApi.executeQuery(connectorId, { sql: trimmed })
      if (data.error) {
        setError(data.error)
      } else {
        setResult(data)
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error"
      setError(message)
      toast.error(t("queryError"))
    } finally {
      setIsRunning(false)
    }
  }

  const handleClear = () => {
    setSql("")
    setResult(null)
    setError(null)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Ctrl+Enter / Cmd+Enter to run
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault()
      handleRun()
    }
  }

  return (
    <div className="flex flex-col h-full p-4 gap-4">
      {/* Read-only badge */}
      {dbConfig?.read_only && (
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Lock className="h-3 w-3" />
          {t("readOnlyMode")}
        </div>
      )}

      {/* SQL Input */}
      <div className="space-y-2">
        <Textarea
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t("queryPlaceholder")}
          rows={6}
          className="resize-y font-mono text-sm"
        />
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={handleRun}
            disabled={isRunning || !sql.trim()}
            className="gap-1.5"
          >
            {isRunning ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            {isRunning ? t("queryRunning") : t("runQuery")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleClear}
            disabled={isRunning}
            className="gap-1.5"
          >
            <Trash2 className="h-3.5 w-3.5" />
            {t("clearQuery")}
          </Button>
        </div>
      </div>

      {/* Results */}
      <div className="flex-1 min-h-0 flex flex-col">
        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {result && (
          <div className="flex-1 flex flex-col min-h-0 space-y-2">
            {/* Result info bar */}
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span>{t("rowCount", { count: result.row_count })}</span>
              <span>{t("executionTime", { time: result.execution_time_ms.toFixed(1) })}</span>
              {result.truncated && (
                <span className="text-amber-600 dark:text-amber-400 flex items-center gap-1">
                  <AlertTriangle className="h-3 w-3" />
                  {t("queryTruncated", { max: dbConfig?.max_rows ?? 1000 })}
                </span>
              )}
            </div>

            {result.row_count === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">
                {t("noResults")}
              </p>
            ) : (
              <ScrollArea className="flex-1 min-h-0 rounded-md border">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0">
                      <tr className="border-b bg-muted/50">
                        {result.columns.map((col, i) => (
                          <th
                            key={i}
                            className="text-left font-medium text-muted-foreground px-3 py-1.5 whitespace-nowrap"
                          >
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.rows.map((row, ri) => (
                        <tr key={ri} className="border-b last:border-0 hover:bg-muted/30">
                          {row.map((cell, ci) => (
                            <td
                              key={ci}
                              className="px-3 py-1.5 font-mono text-xs whitespace-nowrap max-w-[300px] truncate"
                            >
                              {cell === null ? (
                                <span className="text-muted-foreground italic">NULL</span>
                              ) : (
                                String(cell)
                              )}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </ScrollArea>
            )}
          </div>
        )}

        {!result && !error && !isRunning && (
          <div className="flex-1 flex items-center justify-center">
            <p className="text-sm text-muted-foreground">{t("queryResults")}</p>
          </div>
        )}
      </div>
    </div>
  )
}
