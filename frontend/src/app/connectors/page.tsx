"use client"

import { useState, useEffect, useCallback, useRef, useMemo, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { Plus, Plug, Trash2, LayoutTemplate, Database, Globe, ChevronDown, Upload, Search, Server } from "lucide-react"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuth } from "@/contexts/auth-context"
import { connectorApi, marketApi, orgApi } from "@/lib/api"
import type { UserOrg } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import { PublishDialog } from "@/components/shared/publish-dialog"
import { ConnectorCard } from "@/components/connectors/connector-card"
import { MCPServersSection, type MCPServersSectionActions } from "@/components/tools/mcp-servers-section"
import type { ConnectorResponse } from "@/types/connector"
import { toast } from "sonner"
import { Input } from "@/components/ui/input"
import { useScopeFilter } from "@/hooks/use-scope-filter"
import { ScopeFilter } from "@/components/shared/scope-filter"
import { EmptyState } from "@/components/shared/empty-state"
import { ListPagination, PAGE_SIZE } from "@/components/shared/list-pagination"
import { ConnectorTemplateGallery } from "@/components/connectors/connector-template-gallery"

function ConnectorsPageInner() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const t = useTranslations("connectors")
  const tt = useTranslations("tools")
  const tc = useTranslations("common")
  const mcpActionsRef = useRef<MCPServersSectionActions | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const activeTab = searchParams.get("tab") === "mcp" ? "mcp" : "connectors"
  const { scope, setScope, filterByScope } = useScopeFilter()

  const [connectors, setConnectors] = useState<ConnectorResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [pendingUninstallId, setPendingUninstallId] = useState<string | null>(null)
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)
  const [publishOrgId, setPublishOrgId] = useState<string>("")
  const [userOrgs, setUserOrgs] = useState<UserOrg[]>([])
  const [orgsLoading, setOrgsLoading] = useState(false)
  const [templateGalleryOpen, setTemplateGalleryOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const [currentPage, setCurrentPage] = useState(1)

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

  const handleToggleActive = async (id: string, isActive: boolean) => {
    try {
      const updated = await connectorApi.toggleActive(id, isActive)
      setConnectors((prev) => prev.map((c) => (c.id === id ? updated : c)))
      toast.success(isActive ? t("connectorEnabled") : t("connectorDisabled"))
    } catch {
      toast.error(t("connectorToggleFailed"))
    }
  }

  const handlePublish = (id: string) => {
    setPendingPublishId(id)
    setPublishOrgId("")
    setOrgsLoading(true)
    orgApi.list().then((orgs) => {
      setUserOrgs(orgs)
    }).catch(() => {}).finally(() => setOrgsLoading(false))
  }

  const handleUnpublish = (id: string) => setPendingUnpublishId(id)

  const handleResubmit = async (id: string) => {
    try {
      const updated = await connectorApi.resubmit(id)
      setConnectors((prev) => prev.map((c) => (c.id === id ? updated : c)))
      toast.success(t("connectorResubmitted"))
    } catch {
      toast.error(t("connectorResubmitFailed"))
    }
  }

  const handleUninstall = (id: string) => setPendingUninstallId(id)

  const confirmUninstall = async () => {
    if (!pendingUninstallId) return
    const id = pendingUninstallId
    setPendingUninstallId(null)
    try {
      await marketApi.unsubscribe({ resource_type: "connector", resource_id: id })
      setConnectors((prev) => prev.filter((c) => c.id !== id))
      toast.success(tc("uninstalled"))
    } catch {
      toast.error(tc("error"))
    }
  }

  const handleExport = async (id: string) => {
    try {
      const data = await connectorApi.exportConnector(id)
      const connector = connectors.find((c) => c.id === id)
      const slug = (connector?.name || "connector")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `${slug}.json`
      a.click()
      URL.revokeObjectURL(url)
      toast.success(t("exportSuccess"))
    } catch {
      toast.error(t("exportFailed"))
    }
  }

  const handleFork = async (id: string) => {
    try {
      const forked = await connectorApi.forkConnector(id)
      setConnectors((prev) => [forked, ...prev])
      toast.success(t("forkSuccess", { name: forked.name }))
      router.push(`/connectors/${forked.id}`)
    } catch {
      toast.error(t("forkFailed"))
    }
  }

  const handleImport = () => {
    fileInputRef.current?.click()
  }

  const onFileImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      let parsed: unknown
      try {
        parsed = JSON.parse(text)
      } catch {
        toast.error(t("importFileInvalid"))
        return
      }
      const result = await connectorApi.importConnector(parsed)
      setConnectors((prev) => [result.connector, ...prev])
      toast.success(t("importSuccess"))
      router.push(`/connectors/${result.connector.id}`)
    } catch {
      toast.error(t("importFailed"))
    }
    // Reset file input
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

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

  const confirmPublish = async () => {
    if (!pendingPublishId) return
    const id = pendingPublishId
    setPendingPublishId(null)
    try {
      const updated = await connectorApi.publish(id, {
        scope: "org",
        org_id: publishOrgId,
      })
      setConnectors((prev) => prev.map((c) => (c.id === id ? updated : c)))
      toast.success(t("connectorPublished"))
    } catch {
      toast.error(t("connectorPublishFailed"))
    }
  }

  const confirmUnpublish = async () => {
    if (!pendingUnpublishId) return
    const id = pendingUnpublishId
    setPendingUnpublishId(null)
    try {
      const updated = await connectorApi.unpublish(id)
      setConnectors((prev) => prev.map((c) => (c.id === id ? updated : c)))
      toast.success(t("connectorUnpublished"))
    } catch {
      toast.error(t("connectorUnpublishFailed"))
    }
  }


  // Find selected org for review notice
  const selectedOrg = publishOrgId
    ? userOrgs.find((o) => o.id === publishOrgId)
    : null

  const filteredConnectors = useMemo(
    () => (user ? filterByScope(connectors, user.id) : connectors),
    [connectors, scope, user, filterByScope],
  )

  const searchedConnectors = useMemo(() => {
    if (!searchQuery.trim()) return filteredConnectors
    const q = searchQuery.toLowerCase()
    return filteredConnectors.filter(c =>
      c.name.toLowerCase().includes(q) ||
      (c.description ?? "").toLowerCase().includes(q)
    )
  }, [filteredConnectors, searchQuery])

  const connectorTotalPages = Math.ceil(searchedConnectors.length / PAGE_SIZE)
  const paginatedConnectors = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return searchedConnectors.slice(start, start + PAGE_SIZE)
  }, [searchedConnectors, currentPage])

  useEffect(() => { setCurrentPage(1) }, [searchQuery, scope, activeTab])

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
            <>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setTemplateGalleryOpen(true)}>
                <LayoutTemplate className="h-3.5 w-3.5" />
                {t("fromTemplate")}
              </Button>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={handleImport}>
                <Upload className="h-3.5 w-3.5" />
                {t("importConnector")}
              </Button>
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
            </>
          )}
          {activeTab === "mcp" && (
            <>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={() => mcpActionsRef.current?.openHub()}>
                <LayoutTemplate className="h-3.5 w-3.5" />
                {t("fromTemplate")}
              </Button>
              <Button size="sm" className="gap-1.5" onClick={() => mcpActionsRef.current?.openAdd()}>
                <Plus className="h-4 w-4" />
                {tt("addServer")}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Hidden file input for import */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json"
        className="hidden"
        onChange={onFileImport}
      />

      {/* Tabs */}
      <Tabs value={activeTab} className="flex flex-col flex-1 overflow-hidden">
        <div className="border-b px-6 shrink-0">
          <nav className="flex gap-4 -mb-px">
            {([
              { value: "connectors", href: "/connectors", label: t("connectorsTab"), icon: Database },
              { value: "mcp", href: "/connectors?tab=mcp", label: t("mcpTab"), icon: Server },
            ] as const).map((tab) => (
              <Link
                key={tab.value}
                href={tab.href}
                className={cn(
                  "py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5",
                  activeTab === tab.value
                    ? "border-foreground text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                <tab.icon className="h-3.5 w-3.5" />
                {tab.label}
              </Link>
            ))}
          </nav>
        </div>

        <div className="flex items-center gap-3 px-6 pt-3 shrink-0">
          <ScopeFilter value={scope} onChange={setScope} />
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              className="h-8 pl-8 text-xs"
              placeholder={tc("searchPlaceholder")}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>

        {/* Connectors tab */}
        <TabsContent value="connectors" className="flex-1 overflow-y-auto p-6 mt-0">
          {isLoading ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton.ConnectorCard key={i} />
              ))}
            </div>
          ) : connectors.length === 0 ? (
            <EmptyState
              icon={<Plug />}
              title={t("emptyTitle")}
              description={t("emptyDescription")}
              action={
                <Button variant="outline" size="sm" className="gap-1.5" asChild>
                  <Link href="/connectors/new">
                    <Plus className="h-4 w-4" />
                    {t("createConnector")}
                  </Link>
                </Button>
              }
            />
          ) : searchedConnectors.length === 0 ? (
            <EmptyState
              icon={<Search />}
              title={tc("noResultsTitle")}
              description={tc("noResultsDescription")}
            />
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                {paginatedConnectors.map((connector) => (
                  <ConnectorCard
                    key={connector.id}
                    connector={connector}
                    currentUserId={user.id}
                    onDelete={handleDelete}
                    onPublish={handlePublish}
                    onUnpublish={handleUnpublish}
                    onUninstall={handleUninstall}
                    onResubmit={handleResubmit}
                    onExport={handleExport}
                    onFork={handleFork}
                  />
                ))}
              </div>
              <ListPagination currentPage={currentPage} totalPages={connectorTotalPages} onPageChange={setCurrentPage} />
            </>
          )}
        </TabsContent>

        {/* MCP Servers tab */}
        <TabsContent value="mcp" className="flex-1 overflow-y-auto px-6 py-4 mt-0">
          <MCPServersSection onReady={(actions) => { mcpActionsRef.current = actions }} currentUserId={user.id} scope={scope} searchQuery={searchQuery} currentPage={currentPage} onPageChange={setCurrentPage} />
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

      <PublishDialog
        open={pendingPublishId !== null}
        onOpenChange={(open) => { if (!open) setPendingPublishId(null) }}
        title={t("publishTitle")}
        description={t("publishDescription")}
        orgs={userOrgs}
        orgsLoading={orgsLoading}
        selectedOrgId={publishOrgId}
        onOrgChange={setPublishOrgId}
        requiresReview={!!selectedOrg?.review_connectors}
        allowFallback={(() => {
          if (!pendingPublishId) return undefined
          const c = connectors.find(c => c.id === pendingPublishId)
          return c?.type === "database" ? undefined : c?.allow_fallback
        })()}
        fallbackLabel={(() => {
          if (!pendingPublishId) return undefined
          const c = connectors.find(c => c.id === pendingPublishId)
          return c?.type === "database" ? undefined : t("publishAllowFallback")
        })()}
        noOrgsText={t("publishNoOrgs")}
        selectOrgPlaceholder={t("publishSelectOrg")}
        onConfirm={confirmPublish}
      />

      {/* Unpublish Confirmation */}
      <AlertDialog open={pendingUnpublishId !== null} onOpenChange={(open) => { if (!open) setPendingUnpublishId(null) }}>
        <AlertDialogContent className="sm:max-w-sm">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("unpublishTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("unpublishDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={confirmUnpublish}>
              {t("unpublish")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Uninstall Confirmation */}
      <AlertDialog open={pendingUninstallId !== null} onOpenChange={(open) => { if (!open) setPendingUninstallId(null) }}>
        <AlertDialogContent className="sm:max-w-sm">
          <AlertDialogHeader>
            <AlertDialogTitle>{tc("uninstallConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {tc("uninstallConfirmDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={confirmUninstall}
            >
              {tc("uninstall")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Connector Template Gallery */}
      <ConnectorTemplateGallery
        open={templateGalleryOpen}
        onOpenChange={setTemplateGalleryOpen}
      />
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
