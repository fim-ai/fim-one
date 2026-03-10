"use client"

import { useState, useEffect, useCallback, useRef, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { Plus, Loader2, Plug, Trash2, LayoutGrid, Database, Globe, ChevronDown } from "lucide-react"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuth } from "@/contexts/auth-context"
import { connectorApi } from "@/lib/api"
import { ConnectorCard } from "@/components/connectors/connector-card"
import { MCPServersSection, type MCPServersSectionActions } from "@/components/tools/mcp-servers-section"
import type { ConnectorResponse } from "@/types/connector"
import { toast } from "sonner"

function ConnectorsPageInner() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const t = useTranslations("connectors")
  const tt = useTranslations("tools")
  const tc = useTranslations("common")
  const mcpActionsRef = useRef<MCPServersSectionActions | null>(null)

  const activeTab = searchParams.get("tab") === "mcp" ? "mcp" : "connectors"

  const handleTabChange = (tab: string) => {
    if (tab === "connectors") {
      router.replace("/connectors")
    } else {
      router.replace(`/connectors?tab=${tab}`)
    }
  }

  const [connectors, setConnectors] = useState<ConnectorResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const loadConnectors = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await connectorApi.list()
      setConnectors(data.items)
    } catch (err) {
      console.error("Failed to load connectors:", err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) loadConnectors()
  }, [user, loadConnectors])

  const handleDelete = (id: string) => setPendingDeleteId(id)

  const confirmDelete = async () => {
    if (!pendingDeleteId) return
    const id = pendingDeleteId
    setPendingDeleteId(null)
    try {
      await connectorApi.delete(id)
      setConnectors((prev) => prev.filter((c) => c.id !== id))
      toast.success(t("connectorDeleted"))
    } catch {
      toast.error(t("connectorDeleteFailed"))
    }
  }

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Plug className="h-5 w-5" />
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {activeTab === "connectors" && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" className="gap-1.5">
                  <Plus className="h-4 w-4" />
                  {t("newConnector")}
                  <ChevronDown className="h-3 w-3 opacity-60" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem asChild>
                  <Link href="/connectors/new">
                    <Globe className="h-4 w-4" />
                    {t("newApiConnector")}
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/connectors/new?type=database">
                    <Database className="h-4 w-4" />
                    {t("newDatabaseConnector")}
                  </Link>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {activeTab === "mcp" && (
            <>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={() => mcpActionsRef.current?.openHub()}>
                <LayoutGrid className="h-4 w-4" />
                {tt("mcpCatalog")}
              </Button>
              <Button size="sm" className="gap-1.5" onClick={() => mcpActionsRef.current?.openAdd()}>
                <Plus className="h-4 w-4" />
                {tt("addServer")}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={handleTabChange} className="flex flex-col flex-1 overflow-hidden">
        <div className="px-6 pt-4 shrink-0">
          <TabsList>
            <TabsTrigger value="connectors">{t("connectorsTab")}</TabsTrigger>
            <TabsTrigger value="mcp">{t("mcpTab")}</TabsTrigger>
          </TabsList>
        </div>

        {/* Connectors tab */}
        <TabsContent value="connectors" className="flex-1 overflow-y-auto p-6 mt-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : connectors.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <p className="text-sm text-muted-foreground">
                {t("emptyState")}
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-4 gap-1.5"
                asChild
              >
                <Link href="/connectors/new">
                  <Plus className="h-4 w-4" />
                  {t("createConnector")}
                </Link>
              </Button>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {connectors.map((connector) => (
                <ConnectorCard
                  key={connector.id}
                  connector={connector}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          )}
        </TabsContent>

        {/* MCP Servers tab */}
        <TabsContent value="mcp" className="flex-1 overflow-y-auto px-6 py-4 mt-0">
          <MCPServersSection onReady={(actions) => { mcpActionsRef.current = actions }} />
        </TabsContent>
      </Tabs>

      {/* Delete Confirmation */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trash2 className="h-4 w-4" />
              {t("deleteConnectorTitle")}
            </DialogTitle>
            <DialogDescription>
              {t("deleteConnectorDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>{tc("cancel")}</Button>
            <Button variant="destructive" className="px-6" onClick={confirmDelete}>{tc("delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default function ConnectorsPage() {
  return (
    <Suspense fallback={null}>
      <ConnectorsPageInner />
    </Suspense>
  )
}
