"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations } from "next-intl"
import { Plus, Loader2, Clock } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { mcpServerApi, orgApi } from "@/lib/api"
import type { UserOrg } from "@/lib/api"
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
  currentUserId?: string
}

export function MCPServersSection({ onReady, currentUserId }: MCPServersSectionProps) {
  const t = useTranslations("tools")
  const tc = useTranslations("common")
  const to = useTranslations("organizations")

  const [servers, setServers] = useState<MCPServerResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [allowStdio, setAllowStdio] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [hubOpen, setHubOpen] = useState(false)
  const [editingServer, setEditingServer] = useState<MCPServerResponse | null>(null)
  const [dialogInitialValues, setDialogInitialValues] = useState<MCPServerInitialValues | null>(null)
  const fromCatalogRef = useRef(false)
  const dialogSucceededRef = useRef(false)

  // Publish / unpublish state
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)
  const [publishOrgId, setPublishOrgId] = useState<string>("")
  const [userOrgs, setUserOrgs] = useState<UserOrg[]>([])
  const [orgsLoading, setOrgsLoading] = useState(false)

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

  // Publish handlers
  const handlePublish = (id: string) => {
    setPendingPublishId(id)
    setPublishOrgId("")
    setOrgsLoading(true)
    orgApi.list().then((orgs) => {
      setUserOrgs(orgs)
      if (orgs.length > 0) setPublishOrgId(orgs[0].id)
    }).catch(() => {}).finally(() => setOrgsLoading(false))
  }

  const handleUnpublish = (id: string) => {
    setPendingUnpublishId(id)
  }

  const handleResubmit = async (id: string) => {
    try {
      const updated = await mcpServerApi.resubmit(id)
      setServers((prev) => prev.map((s) => (s.id === id ? updated : s)))
      toast.success(t("resubmitSuccess"))
    } catch {
      toast.error(t("resubmitError"))
    }
  }

  const confirmPublish = async () => {
    if (!pendingPublishId || !publishOrgId) return
    const id = pendingPublishId
    setPendingPublishId(null)
    try {
      const updated = await mcpServerApi.publish(id, {
        scope: "org",
        org_id: publishOrgId,
        allow_fallback: true,
      })
      setServers((prev) => prev.map((s) => (s.id === id ? updated : s)))
      toast.success(t("mcpServerPublished"))
    } catch {
      toast.error(t("mcpServerPublishFailed"))
    }
  }

  const confirmUnpublish = async () => {
    if (!pendingUnpublishId) return
    const id = pendingUnpublishId
    setPendingUnpublishId(null)
    try {
      const updated = await mcpServerApi.unpublish(id)
      setServers((prev) => prev.map((s) => (s.id === id ? updated : s)))
      toast.success(t("mcpServerUnpublished"))
    } catch {
      toast.error(t("mcpServerUnpublishFailed"))
    }
  }

  // Find selected org for review notice
  const selectedOrg = publishOrgId ? userOrgs.find((o) => o.id === publishOrgId) : null

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
              currentUserId={currentUserId}
              onEdit={() => handleEdit(server)}
              onDelete={() => handleDelete(server.id)}
              onToggleActive={(isActive) => handleToggleActive(server.id, isActive)}
              onTest={() => handleTest(server.id)}
              onPublish={(id) => handlePublish(id)}
              onUnpublish={(id) => handleUnpublish(id)}
              onResubmit={(id) => handleResubmit(id)}
              onCredentialsSaved={(serverId) => {
                setServers((prev) => prev.map((s) => s.id === serverId ? { ...s, my_has_credentials: true } : s))
              }}
            />
          ))}
        </div>
      )}

      {/* MCP Server create/edit dialog */}
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

      {/* MCP Hub / catalog dialog */}
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

      {/* Publish to Org dialog */}
      <Dialog open={pendingPublishId !== null} onOpenChange={(open) => { if (!open) setPendingPublishId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("publishDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("publishDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              {orgsLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                </div>
              ) : userOrgs.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("publishNoOrgs")}</p>
              ) : (
                <>
                  <Select value={publishOrgId} onValueChange={setPublishOrgId}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder={t("publishSelectOrg")} />
                    </SelectTrigger>
                    <SelectContent>
                      {userOrgs.map((org) => (
                        <SelectItem key={org.id} value={org.id}>{org.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  {/* Review notice */}
                  {selectedOrg?.review_mcp_servers && (
                    <div className="flex items-center gap-2 text-sm text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 p-2 rounded-md">
                      <Clock className="h-4 w-4 shrink-0" />
                      <span>{to("publishRequiresReview")}</span>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingPublishId(null)}>{tc("cancel")}</Button>
            <Button
              className="px-6"
              onClick={confirmPublish}
              disabled={orgsLoading || userOrgs.length === 0 || !publishOrgId}
            >
              {tc("publish")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unpublish confirmation dialog */}
      <Dialog open={pendingUnpublishId !== null} onOpenChange={(open) => { if (!open) setPendingUnpublishId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("unpublishDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("unpublishDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingUnpublishId(null)}>{tc("cancel")}</Button>
            <Button variant="secondary" className="px-6" onClick={confirmUnpublish}>{tc("unpublish")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
