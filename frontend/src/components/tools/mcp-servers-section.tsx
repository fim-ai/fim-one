"use client"

import { useState, useEffect, useCallback, useRef, useMemo } from "react"
import { useTranslations } from "next-intl"
import { Plus, Loader2, Wrench, Search } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { ListPagination, PAGE_SIZE } from "@/components/shared/list-pagination"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { mcpServerApi, marketApi, orgApi } from "@/lib/api"
import type { UserOrg } from "@/lib/api"
import { PublishDialog } from "@/components/shared/publish-dialog"
import { MCPServerCard } from "@/components/tools/mcp-server-card"
import { MCPServerDialog, type MCPServerInitialValues } from "@/components/tools/mcp-server-dialog"
import { MCPHubDialog } from "@/components/tools/mcp-hub-dialog"
import { EmptyState } from "@/components/shared/empty-state"
import type { MCPServerResponse } from "@/types/mcp-server"
import type { ScopeValue } from "@/hooks/use-scope-filter"

export interface MCPServersSectionActions {
  openAdd: () => void
  openHub: () => void
}

interface MCPServersSectionProps {
  onReady?: (actions: MCPServersSectionActions) => void
  currentUserId?: string
  scope?: ScopeValue
  searchQuery?: string
  currentPage?: number
  onPageChange?: (page: number) => void
}

export function MCPServersSection({ onReady, currentUserId, scope = "all", searchQuery = "", currentPage = 1, onPageChange }: MCPServersSectionProps) {
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
  const [allowFallback, setAllowFallback] = useState(true)
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
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setServers((prev) => prev.map((s) => (s.id === id ? { ...updated, source: (s as any).source } : s)))
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
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        return prev.map((s) => (s.id === server.id ? { ...server, source: (s as any).source } : s))
      }
      return [server, ...prev]
    })
  }

  // Publish handlers
  const handlePublish = (id: string) => {
    setPendingPublishId(id)
    setPublishOrgId("")
    setAllowFallback(true)
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
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setServers((prev) => prev.map((s) => (s.id === id ? { ...updated, source: (s as any).source } : s)))
      toast.success(t("resubmitSuccess"))
    } catch {
      toast.error(t("resubmitError"))
    }
  }

  const handleUninstall = async (id: string) => {
    try {
      await marketApi.unsubscribe({ resource_type: "mcp_server", resource_id: id })
      setServers((prev) => prev.filter((s) => s.id !== id))
      toast.success(tc("uninstalled"))
    } catch {
      toast.error(tc("error"))
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
        allow_fallback: allowFallback,
      })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setServers((prev) => prev.map((s) => (s.id === id ? { ...updated, source: (s as any).source } : s)))
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
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setServers((prev) => prev.map((s) => (s.id === id ? { ...updated, source: (s as any).source } : s)))
      toast.success(t("mcpServerUnpublished"))
    } catch {
      toast.error(t("mcpServerUnpublishFailed"))
    }
  }

  // Find selected org for review notice
  const selectedOrg = publishOrgId ? userOrgs.find((o) => o.id === publishOrgId) : null

  const filteredServers = useMemo(
    () => {
      if (!currentUserId || scope === "all") return servers
      if (scope === "mine") return servers.filter((s) => s.user_id === currentUserId)
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if (scope === "installed") return servers.filter((s) => (s as any).source === "market")
      return servers.filter((s) => s.user_id !== currentUserId)
    },
    [servers, scope, currentUserId],
  )

  const searchedServers = useMemo(() => {
    if (!searchQuery.trim()) return filteredServers
    const q = searchQuery.toLowerCase()
    return filteredServers.filter(s =>
      s.name.toLowerCase().includes(q) ||
      (s.description ?? "").toLowerCase().includes(q)
    )
  }, [filteredServers, searchQuery])

  const mcpTotalPages = Math.ceil(searchedServers.length / PAGE_SIZE)
  const paginatedServers = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return searchedServers.slice(start, start + PAGE_SIZE)
  }, [searchedServers, currentPage])

  return (
    <>
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : servers.length === 0 ? (
        <EmptyState
          icon={<Wrench />}
          title={t("noServersTitle")}
          description={t("noServersDescription")}
          action={
            <Button variant="outline" size="sm" className="gap-1.5" onClick={() => handleAdd()}>
              <Plus className="h-4 w-4" />
              {t("addServer")}
            </Button>
          }
        />
      ) : searchedServers.length === 0 ? (
        <EmptyState
          icon={<Search />}
          title={tc("noResultsTitle")}
          description={tc("noResultsDescription")}
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {paginatedServers.map((server) => (
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
                onUninstall={handleUninstall}
                onResubmit={(id) => handleResubmit(id)}
                onCredentialsSaved={(serverId, hasCredentials) => {
                  setServers((prev) => prev.map((s) => s.id === serverId ? { ...s, my_has_credentials: hasCredentials } : s))
                }}
              />
            ))}
          </div>
          <ListPagination currentPage={currentPage} totalPages={mcpTotalPages} onPageChange={onPageChange ?? (() => {})} />
        </>
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

      <PublishDialog
        open={pendingPublishId !== null}
        onOpenChange={(open) => { if (!open) setPendingPublishId(null) }}
        title={t("publishDialogTitle")}
        description={t("publishDialogDescription")}
        orgs={userOrgs}
        orgsLoading={orgsLoading}
        selectedOrgId={publishOrgId}
        onOrgChange={setPublishOrgId}
        requiresReview={!!selectedOrg?.review_mcp_servers}
        allowFallback={allowFallback}
        onAllowFallbackChange={setAllowFallback}
        fallbackLabel={t("allowFallback")}
        fallbackHelp={t("allowFallbackHelp")}
        noOrgsText={t("publishNoOrgs")}
        selectOrgPlaceholder={t("publishSelectOrg")}
        onConfirm={confirmPublish}
      />

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
