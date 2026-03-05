"use client"

import { useState, useEffect, useCallback } from "react"
import { Search, Loader2, CheckCircle, Globe, Monitor, Download, ChevronLeft, ChevronRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { mcpServerApi, apiFetch } from "@/lib/api"
import { toast } from "sonner"
import type { MCPServerResponse } from "@/types/mcp-server"
import type { MCPServerInitialValues } from "./mcp-server-dialog"

interface SmitheryServer {
  qualifiedName: string
  displayName: string
  description: string
  iconUrl?: string
  verified: boolean
  useCount: number
  remote: boolean
}

interface HubPagination {
  totalCount: number
  totalPages: number
  currentPage: number
}

interface MCPHubDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: (server: MCPServerResponse) => void
  onInstallLocal: (initial: MCPServerInitialValues) => void
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

export function MCPHubDialog({ open, onOpenChange, onSuccess, onInstallLocal }: MCPHubDialogProps) {
  const [query, setQuery] = useState("")
  const [page, setPage] = useState(1)
  const [servers, setServers] = useState<SmitheryServer[]>([])
  const [pagination, setPagination] = useState<HubPagination | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [installing, setInstalling] = useState<string | null>(null)

  const debouncedQuery = useDebounce(query, 400)

  const fetchServers = useCallback(async (q: string, p: number) => {
    setIsLoading(true)
    try {
      const params = new URLSearchParams({ page: String(p), page_size: "20" })
      if (q) params.set("q", q)
      const data = await apiFetch<{ servers: SmitheryServer[]; pagination: HubPagination }>(
        `/api/mcp-servers/hub/search?${params}`
      )
      setServers(data.servers ?? [])
      setPagination(data.pagination ?? null)
    } catch {
      toast.error("Failed to load MCP Hub")
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) {
      setPage(1)
      fetchServers(debouncedQuery, 1)
    }
  }, [open, debouncedQuery, fetchServers])

  useEffect(() => {
    if (open && page > 1) {
      fetchServers(debouncedQuery, page)
    }
  }, [page, open, debouncedQuery, fetchServers])

  const handleInstall = async (server: SmitheryServer) => {
    if (server.remote) {
      // Remote: direct install via API
      setInstalling(server.qualifiedName)
      try {
        const result = await mcpServerApi.create({
          name: server.displayName,
          description: server.description,
          transport: "streamable_http",
          url: `https://server.smithery.ai/${server.qualifiedName}/mcp`,
          is_active: false,
        })
        toast.success(`${server.displayName} installed`)
        onSuccess(result)
        onOpenChange(false)
      } catch {
        toast.error(`Failed to install ${server.displayName}`)
      } finally {
        setInstalling(null)
      }
    } else {
      // Local: pre-fill MCPServerDialog
      onInstallLocal({
        name: server.displayName,
        description: server.description,
        transport: "stdio",
        command: "npx",
        args: `-y, @smithery/cli@latest, run, ${server.qualifiedName}`,
      })
    }
  }

  const totalPages = pagination?.totalPages ?? 1

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col gap-0 p-0">
        <DialogHeader className="px-6 pt-6 pb-4 shrink-0">
          <DialogTitle className="flex items-center gap-2">
            Browse MCP Hub
            <span className="text-xs font-normal text-muted-foreground">
              powered by Smithery
            </span>
          </DialogTitle>
          <div className="relative mt-3">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search MCP servers..."
              className="pl-9"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1) }}
            />
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 pb-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : servers.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <p className="text-sm text-muted-foreground">No servers found</p>
            </div>
          ) : (
            <div className="space-y-2">
              {servers.map((server) => (
                <HubServerCard
                  key={server.qualifiedName}
                  server={server}
                  installing={installing === server.qualifiedName}
                  onInstall={() => handleInstall(server)}
                />
              ))}
            </div>
          )}
        </div>

        {!isLoading && totalPages > 1 && (
          <div className="flex items-center justify-between px-6 py-3 border-t border-border/40 shrink-0">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
            >
              <ChevronLeft className="h-4 w-4" />
              Prev
            </Button>
            <span className="text-xs text-muted-foreground">
              Page {page} of {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

interface HubServerCardProps {
  server: SmitheryServer
  installing: boolean
  onInstall: () => void
}

function HubServerCard({ server, installing, onInstall }: HubServerCardProps) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-3 hover:border-border/80 transition-colors">
      {/* Icon */}
      <div className="shrink-0 mt-0.5">
        {server.iconUrl ? (
          <img
            src={server.iconUrl}
            alt={server.displayName}
            className="h-8 w-8 rounded object-cover"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none" }}
          />
        ) : (
          <div className="h-8 w-8 rounded bg-muted flex items-center justify-center">
            <span className="text-xs font-bold text-muted-foreground">
              {server.displayName.charAt(0).toUpperCase()}
            </span>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-sm font-medium text-foreground truncate">
            {server.displayName}
          </span>
          {server.verified && (
            <CheckCircle className="h-3.5 w-3.5 text-blue-500 shrink-0" title="Verified" />
          )}
          <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 shrink-0 ${
            server.remote
              ? "bg-green-500/10 text-green-600 ring-green-500/20"
              : "bg-violet-500/10 text-violet-500 ring-violet-500/20"
          }`}>
            {server.remote ? <><Globe className="h-2.5 w-2.5" />Remote</> : <><Monitor className="h-2.5 w-2.5" />Local</>}
          </span>
        </div>
        <p className="text-xs font-mono text-muted-foreground truncate mt-0.5">
          {server.qualifiedName}
        </p>
        {server.description && (
          <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
            {server.description}
          </p>
        )}
        {server.useCount > 0 && (
          <p className="text-[10px] text-muted-foreground/70 mt-1">
            {server.useCount.toLocaleString()} installs
          </p>
        )}
      </div>

      {/* Install button */}
      <div className="shrink-0 mt-0.5">
        <Button
          size="sm"
          variant={server.remote ? "default" : "outline"}
          className="gap-1.5 h-7 text-xs"
          onClick={onInstall}
          disabled={installing}
        >
          {installing ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Download className="h-3 w-3" />
          )}
          {server.remote ? "Install" : "Configure"}
        </Button>
      </div>
    </div>
  )
}
