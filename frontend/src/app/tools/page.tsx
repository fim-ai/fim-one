"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import { Plus, Loader2, Wrench, Server } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { useAuth } from "@/contexts/auth-context"
import { mcpServerApi } from "@/lib/api"
import { BuiltinToolsSection } from "@/components/tools/builtin-tools-section"
import { MCPServerCard } from "@/components/tools/mcp-server-card"
import { MCPServerDialog } from "@/components/tools/mcp-server-dialog"
import type { MCPServerResponse } from "@/types/mcp-server"

export default function ToolsPage() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()

  const [servers, setServers] = useState<MCPServerResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingServer, setEditingServer] = useState<MCPServerResponse | null>(null)

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
    if (user) loadServers()
  }, [user, loadServers])

  const handleEdit = (server: MCPServerResponse) => {
    setEditingServer(server)
    setDialogOpen(true)
  }

  const handleAdd = () => {
    setEditingServer(null)
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

  const handleSuccess = (server: MCPServerResponse) => {
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
            Tools
          </h1>
          <p className="text-sm text-muted-foreground">
            Built-in tools and MCP server connections
          </p>
        </div>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="builtin" className="flex flex-col flex-1 overflow-hidden">
        <div className="px-6 pt-4 shrink-0">
          <TabsList>
            <TabsTrigger value="builtin">Built-in</TabsTrigger>
            <TabsTrigger value="mcp">MCP Servers</TabsTrigger>
          </TabsList>
        </div>

        {/* Built-in tab */}
        <TabsContent value="builtin" className="flex-1 overflow-y-auto px-6 py-4 mt-0">
          <BuiltinToolsSection />
        </TabsContent>

        {/* MCP Servers tab */}
        <TabsContent value="mcp" className="flex-1 overflow-y-auto px-6 py-4 mt-0">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-foreground flex items-center gap-1.5">
              <Server className="h-4 w-4" />
              MCP Servers
            </h2>
            <Button size="sm" className="gap-1.5" onClick={handleAdd}>
              <Plus className="h-4 w-4" />
              Add Server
            </Button>
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : servers.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center rounded-lg border border-dashed border-border">
              <p className="text-sm text-muted-foreground">
                No MCP servers configured. Add one to extend agent capabilities.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-4 gap-1.5"
                onClick={handleAdd}
              >
                <Plus className="h-4 w-4" />
                Add Server
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
                />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Dialog */}
      <MCPServerDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        server={editingServer}
        onSuccess={handleSuccess}
      />
    </div>
  )
}
