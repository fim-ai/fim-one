"use client"

import { useState, useEffect, useCallback, Suspense } from "react"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { ArrowLeft, Loader2, Plug, Settings, Zap, Database, Table2, Terminal } from "lucide-react"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useAuth } from "@/contexts/auth-context"
import { connectorApi } from "@/lib/api"
import { ConnectorSettingsForm } from "@/components/connectors/connector-settings-form"
import { MyCredentialsForm } from "@/components/connectors/my-credentials-form"
import { DbConnectorSettingsForm } from "@/components/connectors/db-connector-settings-form"
import { ActionManager } from "@/components/connectors/action-manager"
import { SchemaManager } from "@/components/connectors/schema-manager"
import { QueryPlayground } from "@/components/connectors/query-playground"
import { AIActionPanel } from "@/components/connectors/ai-action-panel"
import type { ConnectorResponse } from "@/types/connector"
import { usePageTitle } from "@/hooks/use-page-title"

function ConnectorEditorPageInner() {
  const params = useParams()
  const router = useRouter()
  const searchParams = useSearchParams()
  const { user, isLoading: authLoading } = useAuth()
  const t = useTranslations("connectors")

  const id = params.id as string
  const [connector, setConnector] = useState<ConnectorResponse | null>(null)
  const [isNew, setIsNew] = useState(id === "new")

  usePageTitle(isNew ? t("newConnector") : connector?.name)

  const activeTab = searchParams.get("tab") || "connector"
  const [isLoading, setIsLoading] = useState(id !== "new")
  const [formDirty, setFormDirty] = useState(false)
  const [builderActive, setBuilderActive] = useState(false)
  const [schemaRefreshKey, setSchemaRefreshKey] = useState(0)

  // Determine if this is a database connector
  // For new: check URL param ?type=database
  // For existing: check connector.type
  const isDatabase = isNew
    ? searchParams.get("type") === "database"
    : connector?.type === "database"

  const handleTabChange = (tab: string) => {
    const params = new URLSearchParams(searchParams.toString())
    if (tab === "connector") {
      params.delete("tab")
    } else {
      params.set("tab", tab)
    }
    const qs = params.toString()
    router.replace(`/connectors/${id}${qs ? `?${qs}` : ""}`)
  }

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const loadConnector = useCallback(async () => {
    if (id === "new") return
    try {
      setIsLoading(true)
      const data = await connectorApi.get(id)
      // Non-owners cannot access connector detail (black box model)
      if (user && data.user_id !== user.id) {
        router.replace("/connectors")
        return
      }
      setConnector(data)
      setIsNew(false)
    } catch (err) {
      console.error("Failed to load connector:", err)
      router.replace("/connectors")
    } finally {
      setIsLoading(false)
    }
  }, [id, router, user])

  useEffect(() => {
    if (user && id !== "new") loadConnector()
  }, [user, id, loadConnector])

  const handleBuilderModeChange = useCallback((active: boolean) => {
    setBuilderActive(active)
    if (!active) {
      loadConnector()
    }
  }, [loadConnector])

  const handleConnectorSaved = (saved: ConnectorResponse) => {
    setConnector(saved)
    if (isNew) {
      setIsNew(false)
      router.replace("/connectors")
    }
  }

  const reload = async () => {
    if (!connector) return
    try {
      const data = await connectorApi.get(connector.id)
      setConnector(data)
    } catch (err) {
      console.error("Failed to reload connector:", err)
    }
  }

  if (authLoading || !user) return null

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border/40 shrink-0">
        <Tooltip>
          <TooltipTrigger asChild>
            {builderActive ? (
              <Button variant="ghost" size="icon-xs" disabled>
                <ArrowLeft className="h-4 w-4" />
              </Button>
            ) : (
              <Button variant="ghost" size="icon-xs" asChild>
                <Link href="/connectors">
                  <ArrowLeft className="h-4 w-4" />
                </Link>
              </Button>
            )}
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={5}>{t("backToConnectors")}</TooltipContent>
        </Tooltip>
        <h1 className="text-sm font-semibold text-foreground truncate flex items-center gap-2">
          {connector?.icon ? (
            <span className="text-base leading-none shrink-0">{connector.icon}</span>
          ) : isDatabase ? (
            <Database className="h-4 w-4 shrink-0" />
          ) : (
            <Plug className="h-4 w-4 shrink-0" />
          )}
          {isNew ? t("newConnector") : connector?.name || t("connector")}
        </h1>
      </div>

      {/* Main content: left AI chat + right tabs */}
      <div className="flex flex-1 min-h-0">
        {/* Left: AI Chat Panel (1/3 -> 1/2 in builder mode) */}
        <div className={`${builderActive ? "w-1/2" : "w-1/3"} border-r border-border flex flex-col min-h-0 transition-all duration-300`}>
          <AIActionPanel
            connectorId={connector?.id ?? null}
            onActionsChanged={reload}
            onConnectorUpdated={(updated) => setConnector(updated)}
            onSchemaChanged={() => setSchemaRefreshKey((k) => k + 1)}
            formDirty={formDirty}
            isNewMode={isNew}
            onConnectorCreated={handleConnectorSaved}
            onBuilderModeChange={handleBuilderModeChange}
            connectorType={isDatabase ? "database" : "api"}
          />
        </div>

        {/* Right: Tabs (2/3 -> 1/2 in builder mode) */}
        <div className={`${builderActive ? "w-1/2" : "w-2/3"} flex flex-col min-h-0 transition-all duration-300`}>
          <Tabs
            value={activeTab}
            onValueChange={handleTabChange}
            className="flex flex-col flex-1 min-h-0"
          >
            <TabsList className="shrink-0 mx-4 mt-3 w-fit">
              <TabsTrigger value="connector" className="gap-1.5">
                <Settings className="h-3.5 w-3.5" />
                {t("connectorTab")}
              </TabsTrigger>
              {isDatabase ? (
                <>
                  <TabsTrigger value="schema" disabled={isNew} className="gap-1.5">
                    <Table2 className="h-3.5 w-3.5" />
                    {t("schemaTab")}
                  </TabsTrigger>
                  <TabsTrigger value="query" disabled={isNew} className="gap-1.5">
                    <Terminal className="h-3.5 w-3.5" />
                    {t("queryTab")}
                  </TabsTrigger>
                </>
              ) : (
                <TabsTrigger value="actions" disabled={isNew} className="gap-1.5">
                  <Zap className="h-3.5 w-3.5" />
                  {t("actionsTab")}
                  {connector && connector.actions.length > 0 && (
                    <span className="text-xs text-muted-foreground">
                      ({connector.actions.length})
                    </span>
                  )}
                </TabsTrigger>
              )}
            </TabsList>

            {/* Settings tab: swap form based on type */}
            <TabsContent forceMount value="connector" className="flex-1 min-h-0 px-4 py-4 overflow-auto flex flex-col data-[state=inactive]:hidden">
              {isDatabase ? (
                <DbConnectorSettingsForm
                  connector={connector}
                  onSaved={handleConnectorSaved}
                  onDirtyChange={setFormDirty}
                />
              ) : (
                <ConnectorSettingsForm
                  connector={connector}
                  onSaved={handleConnectorSaved}
                  onDirtyChange={setFormDirty}
                />
              )}
              {connector && user && connector.user_id !== user.id
                && connector.visibility !== "personal"
                && connector.auth_type !== "none" && (
                <MyCredentialsForm connector={connector} />
              )}
            </TabsContent>

            {/* API: Actions tab */}
            <TabsContent forceMount value="actions" className="flex-1 min-h-0 overflow-hidden flex flex-col data-[state=inactive]:hidden">
              {connector && (
                <ActionManager connector={connector} onChanged={reload} />
              )}
            </TabsContent>

            {/* DB: Schema tab */}
            <TabsContent forceMount value="schema" className="flex-1 min-h-0 overflow-hidden flex flex-col data-[state=inactive]:hidden">
              {isDatabase && connector && (
                <SchemaManager connectorId={connector.id} refreshKey={schemaRefreshKey} />
              )}
            </TabsContent>

            {/* DB: Query tab */}
            <TabsContent forceMount value="query" className="flex-1 min-h-0 overflow-hidden flex flex-col data-[state=inactive]:hidden">
              {isDatabase && connector && (
                <QueryPlayground connectorId={connector.id} dbConfig={connector.db_config} />
              )}
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  )
}

export default function ConnectorEditorPage() {
  return (
    <Suspense fallback={null}>
      <ConnectorEditorPageInner />
    </Suspense>
  )
}
