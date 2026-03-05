"use client"

import { useState, useEffect, useCallback } from "react"
import { Loader2, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { apiFetch } from "@/lib/api"
import { toast } from "sonner"

interface AuditEntry {
  id: string
  admin_id: string
  admin_username: string
  action: string
  target_type: string | null
  target_id: string | null
  target_label: string | null
  detail: string | null
  created_at: string
}

interface AuditPage {
  items: AuditEntry[]
  total: number
  page: number
  size: number
  pages: number
}

const ACTION_COLORS: Record<string, string> = {
  "user.create": "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
  "user.delete": "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
  "user.disable": "bg-orange-500/15 text-orange-700 dark:text-orange-400 border-orange-500/30",
  "user.enable": "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
  "user.grant_admin": "bg-purple-500/15 text-purple-700 dark:text-purple-400 border-purple-500/30",
  "user.revoke_admin": "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
  "user.reset_password": "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30",
  "auth.force_logout_all": "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
  "settings.update": "bg-sky-500/15 text-sky-700 dark:text-sky-400 border-sky-500/30",
}

function actionColor(action: string): string {
  return ACTION_COLORS[action] ?? "bg-muted text-muted-foreground border-border"
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
  } catch {
    return iso
  }
}

export function AdminAudit() {
  const [data, setData] = useState<AuditPage | null>(null)
  const [page, setPage] = useState(1)
  const [isLoading, setIsLoading] = useState(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const res = await apiFetch<AuditPage>(`/api/admin/audit-log?page=${page}&size=50`)
      setData(res)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load audit log")
    } finally {
      setIsLoading(false)
    }
  }, [page])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">Audit Log</h2>
          <p className="text-sm text-muted-foreground">
            Admin actions recorded for accountability.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={isLoading}>
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isLoading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-6 text-sm text-muted-foreground text-center">
          No audit log entries yet.
        </div>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">Time</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Admin</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Action</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Target</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((entry) => (
                  <tr key={entry.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                      {formatTime(entry.created_at)}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-foreground">
                      {entry.admin_username}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${actionColor(entry.action)}`}>
                        {entry.action}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground text-xs">
                      {entry.target_label
                        ? <span className="font-medium text-foreground">{entry.target_label}</span>
                        : <span className="text-muted-foreground/50">—</span>}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground max-w-[260px] truncate">
                      {entry.detail ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>{data.total} entr{data.total !== 1 ? "ies" : "y"} total</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <span>Page {page} of {data.pages}</span>
              <Button variant="outline" size="sm" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
