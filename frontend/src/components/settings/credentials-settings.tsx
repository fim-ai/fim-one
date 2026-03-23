"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { Loader2, ShieldCheck, ExternalLink } from "lucide-react"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { apiFetch } from "@/lib/api"

interface CredItem {
  id: string
  name: string
  server_name?: string
  server_id?: string
  resource_type: string
  resource_id: string
  status: "configured" | "not_configured"
  created_at: string | null
  updated_at: string | null
}

interface CredentialsResponse {
  connector_credentials: CredItem[]
  mcp_credentials: CredItem[]
}

export function CredentialsSettings() {
  const t = useTranslations("settings.credentials")
  const { formatDate } = useDateFormatter()
  const [data, setData] = useState<CredentialsResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch<CredentialsResponse>("/api/me/credentials")
      .then(setData)
      .catch(() => toast.error(t("loadFailed")))
      .finally(() => setLoading(false))
  }, [t])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("description")}</p>
      </div>

      {/* Connector Credentials */}
      <div className="space-y-3">
        <h3 className="text-sm font-medium text-foreground">{t("connectorCredentials")}</h3>
        {(!data?.connector_credentials || data.connector_credentials.length === 0) ? (
          <div className="rounded-md border border-border bg-muted/30 p-6 text-center">
            <ShieldCheck className="mx-auto h-6 w-6 text-muted-foreground/50 mb-2" />
            <p className="text-sm text-muted-foreground">{t("noConnectorCredentials")}</p>
          </div>
        ) : (
          <div className="rounded-md border border-border overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("connectorName")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colStatus")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("lastUpdated")}</th>
                  <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("manage")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.connector_credentials.map((cred) => (
                  <tr key={cred.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 font-medium text-foreground">{cred.name || cred.server_name || cred.server_id || "Unknown"}</td>
                    <td className="px-4 py-3">
                      {cred.status === "configured" ? (
                        <Badge variant="outline" className="border-green-500/30 bg-green-50 text-green-700 dark:bg-green-950/20 dark:text-green-400">
                          {t("statusConfigured")}
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="text-muted-foreground">
                          {t("statusNotConfigured")}
                        </Badge>
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground text-xs">{formatDate(cred.updated_at || cred.created_at, "--")}</td>
                    <td className="px-4 py-3 text-right">
                      <Button variant="ghost" size="sm" asChild>
                        <Link href={`/connectors/${cred.resource_id}`}>
                          <ExternalLink className="mr-2 h-4 w-4" />
                          {t("manage")}
                        </Link>
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* MCP Credentials */}
      <div className="space-y-3">
        <h3 className="text-sm font-medium text-foreground">{t("mcpCredentials")}</h3>
        {(!data?.mcp_credentials || data.mcp_credentials.length === 0) ? (
          <div className="rounded-md border border-border bg-muted/30 p-6 text-center">
            <ShieldCheck className="mx-auto h-6 w-6 text-muted-foreground/50 mb-2" />
            <p className="text-sm text-muted-foreground">{t("noMcpCredentials")}</p>
          </div>
        ) : (
          <div className="rounded-md border border-border overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("mcpName")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("colStatus")}</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("lastUpdated")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.mcp_credentials.map((cred) => (
                  <tr key={cred.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 font-medium text-foreground">{cred.name || cred.server_name || cred.server_id || "Unknown"}</td>
                    <td className="px-4 py-3">
                      {cred.status === "configured" ? (
                        <Badge variant="outline" className="border-green-500/30 bg-green-50 text-green-700 dark:bg-green-950/20 dark:text-green-400">
                          {t("statusConfigured")}
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="text-muted-foreground">
                          {t("statusNotConfigured")}
                        </Badge>
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground text-xs">{formatDate(cred.updated_at || cred.created_at, "--")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
