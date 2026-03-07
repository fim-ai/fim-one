"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useTranslations } from "next-intl"
import { Plus, Loader2, Wrench, Server, LayoutGrid } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { useAuth } from "@/contexts/auth-context"
import { mcpServerApi } from "@/lib/api"
import { BuiltinToolsSection } from "@/components/tools/builtin-tools-section"
import { MCPServerCard } from "@/components/tools/mcp-server-card"
import { MCPServerDialog, type MCPServerInitialValues } from "@/components/tools/mcp-server-dialog"
import { MCPHubDialog } from "@/components/tools/mcp-hub-dialog"
import type { MCPServerResponse } from "@/types/mcp-server"

export default function ToolsPage() {
  const t = useTranslations("tools")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()

  const VALID_TABS = ["builtin", "mcp"]
  const initialTab = VALID_TABS.includes(searchParams.get("tab") ?? "") ? searchParams.get("tab")! : "builtin"
  const [activeTab, setActiveTab] = useState(initialTab)

  const handleTabChange = (tab: string) => {
    setActiveTab(tab)
    router.replace(`/tools?tab=${tab}`)
  }
  const [servers, setServers] = useState<MCPServerResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [allowStdio, setAllowStdio] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [hubOpen, setHubOpen] = useState(false)
  const [editingServer, setEditingServer] = useState<MCPServerResponse | null>(null)
  const [dialogInitialValues, setDialogInitialValues] = useState<MCPServerInitialValues | null>(null)
  const fromCatalogRef = useRef(false)
  const dialogSucceededRef = useRef(false)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const loadServers = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await mcpServerApi.list()
      setServers(data.items)
    } catch (err) {
      console.error("Failed to load MCP servers:", err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) {
      loadServers()
      mcpServerApi.capabilities().then((c) => setAllowStdio(c.allow_stdio)).catch(() => {})
    }
  }, [user, loadServers])

  const handleEdit = (server: MCPServerResponse) => {
    setEditingServer(server)
    setDialogOpen(true)
  }

  const handleAdd = (initial?: MCPServerInitialValues) => {
    setEditingServer(null)
    setDialogInitialValues(initial ?? null)
    setDialogOpen(true)
  }

  const handleDelete = async (id: string) => {
    try {
      await mcpServerApi.delete(id)
      setServers((prev) => prev.filter((s) => s.id !== id))
    } catch (err) {
      console.error("Failed to delete MCP server:", err)
    }
  }

  const handleToggleActive = async (id: string, isActive: boolean) => {
    try {
      const updated = await mcpServerApi.toggleActive(id, isActive)
      setServers((prev) => prev.map((s) => (s.id === id ? updated : s)))
    } catch (err) {
      console.error("Failed to toggle MCP server:", err)
    }
  }

  const handleTest = async (id: string) => {
    const result = await mcpServerApi.test(id)
    if (result.ok && result.tool_count !== undefined) {
      setServers((prev) => prev.map((s) => (s.id === id ? { ...s, tool_count: result.tool_count! } : s)))
    }
    return result
  }

  const handleSuccess = (server: MCPServerResponse) => {
    dialogSucceededRef.current = true
    setServers((prev) => {
      const exists = prev.find((s) => s.id === server.id)
      if (exists) {
        return prev.map((s) => (s.id === server.id ? server : s))
      }
      return [server, ...prev]
    })
  }

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Wrench className="h-5 w-5" />
            {t("pageTitle")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("pageDescription")}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={handleTabChange} className="flex flex-col flex-1 overflow-hidden">
        <div className="px-6 pt-4 shrink-0">
          <TabsList>
            <TabsTrigger value="builtin">{t("builtinTab")}</TabsTrigger>
            <TabsTrigger value="mcp">{t("mcpServersTab")}</TabsTrigger>
          </TabsList>
        </div>

        {/* Built-in tab */}
        <TabsContent value="builtin" className="flex-1 overflow-y-auto px-6 py-4 mt-0">
          <BuiltinToolsSection onSwitchToMCP={() => handleTabChange("mcp")} />
        </TabsContent>

        {/* MCP Servers tab */}
        <TabsContent value="mcp" className="flex-1 overflow-y-auto px-6 py-4 mt-0">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-foreground flex items-center gap-1.5">
              <Server className="h-4 w-4" />
              {t("mcpServersTitle")}
            </h2>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" className="gap-1.5 h-8" onClick={() => setHubOpen(true)}>
                <LayoutGrid className="h-4 w-4" />
                {t("mcpCatalog")}
              </Button>
              <Button size="sm" className="gap-1.5 h-8" onClick={() => handleAdd()}>
                <Plus className="h-4 w-4" />
                {t("addServer")}
              </Button>
            </div>
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : servers.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-center rounded-lg border border-dashed border-border">
              <p className="text-sm text-muted-foreground">
                {t("noServersMessage")}
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-4 gap-1.5"
                onClick={() => handleAdd()}
              >
                <Plus className="h-4 w-4" />
                {t("addServer")}
              </Button>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {servers.map((server) => (
                <MCPServerCard
                  key={server.id}
                  server={server}
                  onEdit={() => handleEdit(server)}
                  onDelete={() => handleDelete(server.id)}
                  onToggleActive={(isActive) => handleToggleActive(server.id, isActive)}
                  onTest={() => handleTest(server.id)}
                />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Dialog */}
      <MCPServerDialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open)
          if (!open) {
            // Reopen catalog if dialog was opened from it and user didn't save
            if (fromCatalogRef.current && !dialogSucceededRef.current) {
              setHubOpen(true)
            }
            fromCatalogRef.current = false
            dialogSucceededRef.current = false
          }
        }}
        server={editingServer}
        initialValues={dialogInitialValues}
        onSuccess={handleSuccess}
        allowStdio={allowStdio}
      />
      <MCPHubDialog
        open={hubOpen}
        onOpenChange={setHubOpen}
        onSuccess={handleSuccess}
        onInstallLocal={(initial) => {
          fromCatalogRef.current = true
          dialogSucceededRef.current = false
          setHubOpen(false)
          handleAdd(initial)
        }}
      />
    </div>
  )
}
