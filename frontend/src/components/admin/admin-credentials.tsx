"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations, useLocale } from "next-intl"
import { toast } from "sonner"
import {
  Loader2,
  Search,
  KeyRound,
  Plug,
  Server,
  Users,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { adminApi, type AdminCredential, type AdminCredentialStats } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"

const PAGE_SIZE = 20

export function AdminCredentials() {
  const t = useTranslations("admin.credentials")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

  // --- State ---
  const [credentials, setCredentials] = useState<AdminCredential[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [search, setSearch] = useState("")
  const [typeFilter, setTypeFilter] = useState("__default__")
  const [isLoading, setIsLoading] = useState(true)
  const [stats, setStats] = useState<AdminCredentialStats | null>(null)

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // --- Load ---
  const loadCredentials = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listCredentials({
        page,
        size: PAGE_SIZE,
        type: typeFilter !== "__default__" ? typeFilter : undefined,
        search: search || undefined,
      })
      setCredentials(data.items)
      setTotal(data.total)
      setPages(Math.max(1, Math.ceil(data.total / PAGE_SIZE)))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, search, typeFilter, tError])

  const loadStats = useCallback(async () => {
    try {
      const data = await adminApi.getCredentialStats()
      setStats(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }, [tError])

  useEffect(() => { loadCredentials() }, [loadCredentials])
  useEffect(() => { loadStats() }, [loadStats])

  // --- Search ---
  const handleSearchChange = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(value)
      setPage(1)
    }, 300)
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <KeyRound className="h-4 w-4" />
              <p className="text-xs font-medium">{t("totalCredentials")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">{stats.total_credentials ?? 0}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Plug className="h-4 w-4" />
              <p className="text-xs font-medium">{t("connectorCredentials")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">{stats.connector_credentials ?? 0}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Server className="h-4 w-4" />
              <p className="text-xs font-medium">{t("mcpCredentials")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">{stats.mcp_credentials ?? 0}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Users className="h-4 w-4" />
              <p className="text-xs font-medium">{t("usersWithCredentials")}</p>
            </div>
            <p className="text-2xl font-semibold tabular-nums">{stats.users_with_credentials ?? 0}</p>
          </div>
        </div>
      )}

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
        <Select value={typeFilter} onValueChange={(v) => { setTypeFilter(v); setPage(1) }}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__default__">{t("filterAll")}</SelectItem>
            <SelectItem value="connector">{t("filterConnector")}</SelectItem>
            <SelectItem value="mcp">{t("filterMCP")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : credentials.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noCredentials")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colUser")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colResource")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colType")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colStatus")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colUpdated")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {credentials.map((cred) => (
                <tr key={cred.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">
                    {cred.username || cred.email || "--"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{cred.resource_name || t("unknownResource")}</td>
                  <td className="px-4 py-3">
                    {cred.resource_type === "connector" ? (
                      <Badge variant="secondary" className="gap-1">
                        <Plug className="h-3 w-3" />
                        {t("typeConnector")}
                      </Badge>
                    ) : (
                      <Badge variant="secondary" className="gap-1">
                        <Server className="h-3 w-3" />
                        {t("typeMCP")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">
                      {t("statusActive")}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {cred.updated_at ? new Date(cred.updated_at).toLocaleDateString(locale) : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {!isLoading && credentials.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t("totalItems", { count: total })}</span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              {t("previous")}
            </Button>
            <span>{t("pageOf", { page, pages })}</span>
            <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage((p) => Math.min(pages, p + 1))}>
              {tc("next")}
            </Button>
          </div>
        </div>
      )}

    </div>
  )
}
