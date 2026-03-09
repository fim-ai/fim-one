"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations } from "next-intl"
import { Plus, Loader2, Server, LayoutGrid } from "lucide-react"
import { Button } from "@/components/ui/button"
import { mcpServerApi } from "@/lib/api"
import { MCPServerCard } from "@/components/tools/mcp-server-card"
import { MCPServerDialog, type MCPServerInitialValues } from "@/components/tools/mcp-server-dialog"
import { MCPHubDialog } from "@/components/tools/mcp-hub-dialog"
import type { MCPServerResponse } from "@/types/mcp-server"

export interface MCPServersSectionActions {
  openAdd: () => void
  openHub: () => void
}

interface MCPServersSectionProps {
  onReady?: (actions: MCPServersSectionActions) => void
}

export function MCPServersSection({ onReady }: MCPServersSectionProps) {
  const t = useTranslations("tools")

  const [servers, setServers] = useState<MCPServerResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [allowStdio, setAllowStdio] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [hubOpen, setHubOpen] = useState(false)
  const [editingServer, setEditingServer] = useState<MCPServerResponse | null>(null)
  const [dialogInitialValues, setDialogInitialValues] = useState<MCPServerInitialValues | null>(null)
  const fromCatalogRef = useRef(false)
  const dialogSucceededRef = useRef(false)

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
    loadServers()
    mcpServerApi.capabilities().then((c) => setAllowStdio(c.allow_stdio)).catch(() => {})
  }, [loadServers])

  const handleEdit = (server: MCPServerResponse) => {
    setEditingServer(server)
    setDialogOpen(true)
  }

  const handleAdd = useCallback((initial?: MCPServerInitialValues) => {
    setEditingServer(null)
    setDialogInitialValues(initial ?? null)
    setDialogOpen(true)
  }, [])

  // Expose actions to parent via callback
  useEffect(() => {
    onReady?.({
      openAdd: () => handleAdd(),
      openHub: () => setHubOpen(true),
    })
  }, [onReady, handleAdd])

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

  return (
    <>
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
    </>
  )
}
