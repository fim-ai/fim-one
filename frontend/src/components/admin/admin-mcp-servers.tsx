"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Plus, Pencil, Trash2, TestTube2, Loader2, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
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
import { adminApi } from "@/lib/api"
import type { AdminMCPServer } from "@/types/admin"

export function AdminMcpServers() {
  const t = useTranslations("admin.mcpServers")
  const tc = useTranslations("common")
  const [servers, setServers] = useState<AdminMCPServer[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [deleteTarget, setDeleteTarget] = useState<AdminMCPServer | null>(null)
  const [editTarget, setEditTarget] = useState<AdminMCPServer | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  const errMsg = (err: unknown) =>
    err instanceof Error ? err.message : "Operation failed"

  const load = async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listGlobalMcpServers()
      setServers(data)
    } catch (err) {
      toast.error(errMsg(err))
    } finally {
      setIsLoading(false)
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [])

  const handleTest = async (server: AdminMCPServer) => {
    try {
      const result = await adminApi.testGlobalMcpServer(server.id)
      if (result.ok) {
        toast.success(t("testConnected", { count: result.tool_count ?? 0 }))
        load()
      } else {
        toast.error(result.error ?? "Test failed")
      }
    } catch (err) {
      toast.error(errMsg(err))
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await adminApi.deleteGlobalMcpServer(deleteTarget.id)
      toast.success(t("serverDeleted"))
      setDeleteTarget(null)
      load()
    } catch (err) {
      toast.error(errMsg(err))
    }
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Button size="sm" onClick={() => setShowCreate(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("addServer")}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : servers.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noServers")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{tc("name")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("transport")}</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("tools")}</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{tc("status")}</th>
                <th className="px-4 py-2.5 w-28" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {servers.map((s) => (
                <tr key={s.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3">
                    <div>
                      <p className="font-medium text-foreground">{s.name}</p>
                      {s.description && <p className="text-xs text-muted-foreground">{s.description}</p>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant="outline">{s.transport}</Badge>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{s.tool_count}</td>
                  <td className="px-4 py-3">
                    <Badge variant={s.is_active ? "default" : "secondary"}>
                      {s.is_active ? tc("active") : tc("inactive")}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0" title={tc("test")} onClick={() => handleTest(s)}>
                        <TestTube2 className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0" title={tc("edit")} onClick={() => setEditTarget(s)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-destructive" title={tc("delete")} onClick={() => setDeleteTarget(s)}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Delete confirm */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteDesc", { name: deleteTarget?.name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Create dialog */}
      <GlobalMcpServerDialog
        open={showCreate}
        onOpenChange={setShowCreate}
        onSuccess={() => { setShowCreate(false); load() }}
      />

      {/* Edit dialog */}
      <GlobalMcpServerDialog
        open={!!editTarget}
        onOpenChange={(open) => { if (!open) setEditTarget(null) }}
        server={editTarget}
        onSuccess={() => { setEditTarget(null); load() }}
      />
    </div>
  )
}

/* -- Global MCP Server Form Dialog -- */

function GlobalMcpServerDialog({
  open,
  onOpenChange,
  server,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  server?: AdminMCPServer | null
  onSuccess: () => void
}) {
  const isEdit = !!server
  const t = useTranslations("admin.mcpServers")
  const tc = useTranslations("common")

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [transport, setTransport] = useState<"stdio" | "sse" | "streamable_http">("stdio")
  const [command, setCommand] = useState("")
  const [args, setArgs] = useState("")
  const [url, setUrl] = useState("")
  const [isActive, setIsActive] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [envPairs, setEnvPairs] = useState<Array<{ key: string; value: string }>>([])
  const [headerPairs, setHeaderPairs] = useState<Array<{ key: string; value: string }>>([])

  useEffect(() => {
    if (open) {
      if (server) {
        setName(server.name)
        setDescription(server.description || "")
        setTransport(server.transport as "stdio" | "sse" | "streamable_http")
        setCommand(server.command || "")
        setArgs(server.args?.join(", ") || "")
        setUrl(server.url || "")
        setIsActive(server.is_active)
        setEnvPairs([])
        setHeaderPairs([])
      } else {
        setName("")
        setDescription("")
        setTransport("stdio")
        setCommand("")
        setArgs("")
        setUrl("")
        setIsActive(true)
        setEnvPairs([])
        setHeaderPairs([])
      }
    }
  }, [open, server])

  const addEnvPair = () => setEnvPairs((prev) => [...prev, { key: "", value: "" }])
  const removeEnvPair = (index: number) => setEnvPairs((prev) => prev.filter((_, i) => i !== index))
  const updateEnvPair = (index: number, field: "key" | "value", val: string) =>
    setEnvPairs((prev) => prev.map((pair, i) => (i === index ? { ...pair, [field]: val } : pair)))

  const addHeaderPair = () => setHeaderPairs((prev) => [...prev, { key: "", value: "" }])
  const removeHeaderPair = (index: number) => setHeaderPairs((prev) => prev.filter((_, i) => i !== index))
  const updateHeaderPair = (index: number, field: "key" | "value", val: string) =>
    setHeaderPairs((prev) => prev.map((pair, i) => (i === index ? { ...pair, [field]: val } : pair)))

  const handleSubmit = async () => {
    if (!name.trim()) return
    setIsSaving(true)
    try {
      const envObj =
        envPairs.length > 0
          ? Object.fromEntries(envPairs.filter((p) => p.key.trim()).map((p) => [p.key.trim(), p.value]))
          : null

      const parsedArgs = args.trim()
        ? args.split(",").map((a) => a.trim()).filter(Boolean)
        : null

      const headersObj =
        (transport === "sse" || transport === "streamable_http") && headerPairs.length > 0
          ? Object.fromEntries(headerPairs.filter((p) => p.key.trim()).map((p) => [p.key.trim(), p.value]))
          : null

      const body = {
        name: name.trim(),
        description: description.trim() || null,
        transport,
        command: transport === "stdio" ? command.trim() || null : null,
        args: transport === "stdio" ? parsedArgs : null,
        env: transport === "stdio" ? envObj : null,
        url: (transport === "sse" || transport === "streamable_http") ? url.trim() || null : null,
        headers: headersObj,
        is_active: isActive,
      }

      if (isEdit && server) {
        await adminApi.updateGlobalMcpServer(server.id, body)
        toast.success(t("serverUpdated"))
      } else {
        await adminApi.createGlobalMcpServer(body)
        toast.success(t("serverCreated"))
      }
      onSuccess()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"))
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg flex flex-col max-h-[85vh]">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("editDialogTitle") : t("addDialogTitle")}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? t("editDialogDesc")
              : t("addDialogDesc")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto">
          <div className="grid gap-4 py-2">
            {/* Name */}
            <div className="grid gap-1.5">
              <label className="text-sm font-medium">{t("serverName")} <span className="text-destructive">*</span></label>
              <Input
                placeholder={t("serverNamePlaceholder")}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            {/* Description */}
            <div className="grid gap-1.5">
              <label className="text-sm font-medium">{tc("description")}</label>
              <Textarea
                placeholder={t("descriptionPlaceholder")}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
              />
            </div>

            {/* Transport */}
            <div className="grid gap-1.5">
              <label className="text-sm font-medium">{t("transport")}</label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={transport === "stdio" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTransport("stdio")}
                >
                  STDIO
                </Button>
                <Button
                  type="button"
                  variant={transport === "sse" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTransport("sse")}
                >
                  SSE
                </Button>
                <Button
                  type="button"
                  variant={transport === "streamable_http" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTransport("streamable_http")}
                >
                  Streamable HTTP
                </Button>
              </div>
            </div>

            {/* STDIO fields */}
            {transport === "stdio" && (
              <>
                <div className="grid gap-1.5">
                  <label className="text-sm font-medium">{t("commandLabel")}</label>
                  <Input
                    placeholder={t("commandPlaceholder")}
                    value={command}
                    onChange={(e) => setCommand(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <label className="text-sm font-medium">{t("argumentsLabel")}</label>
                  <Input
                    placeholder={t("argumentsPlaceholder")}
                    value={args}
                    onChange={(e) => setArgs(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">{t("argumentsHint")}</p>
                </div>
                <div className="grid gap-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium">{t("envVarsLabel")}</label>
                    <Button type="button" variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={addEnvPair}>
                      <Plus className="h-3 w-3" /> {tc("add")}
                    </Button>
                  </div>
                  {envPairs.map((pair, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <Input placeholder="KEY" className="flex-1 font-mono text-xs" value={pair.key} onChange={(e) => updateEnvPair(idx, "key", e.target.value)} />
                      <span className="text-muted-foreground text-xs">=</span>
                      <Input placeholder="value" className="flex-1 text-xs" value={pair.value} onChange={(e) => updateEnvPair(idx, "value", e.target.value)} />
                      <Button type="button" variant="ghost" size="sm" onClick={() => removeEnvPair(idx)} className="shrink-0 h-7 w-7 p-0 text-muted-foreground hover:text-destructive">
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                </div>
              </>
            )}

            {/* SSE / Streamable HTTP fields */}
            {(transport === "sse" || transport === "streamable_http") && (
              <>
                <div className="grid gap-1.5">
                  <label className="text-sm font-medium">{t("serverUrlLabel")}</label>
                  <Input
                    placeholder={transport === "sse" ? "e.g. http://localhost:3001/sse" : "e.g. http://localhost:3001/mcp"}
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium">{t("httpHeadersLabel")}</label>
                    <Button type="button" variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={addHeaderPair}>
                      <Plus className="h-3 w-3" /> {tc("add")}
                    </Button>
                  </div>
                  {headerPairs.map((pair, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <Input placeholder="Header-Name" className="flex-1 font-mono text-xs" value={pair.key} onChange={(e) => updateHeaderPair(idx, "key", e.target.value)} />
                      <span className="text-muted-foreground text-xs">:</span>
                      <Input placeholder="value" className="flex-1 text-xs" value={pair.value} onChange={(e) => updateHeaderPair(idx, "value", e.target.value)} />
                      <Button type="button" variant="ghost" size="sm" onClick={() => removeHeaderPair(idx)} className="shrink-0 h-7 w-7 p-0 text-muted-foreground hover:text-destructive">
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                </div>
              </>
            )}

            {/* Active toggle */}
            <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
              <div>
                <p className="text-sm font-medium">{t("activeLabel")}</p>
                <p className="text-xs text-muted-foreground">{t("activeDesc")}</p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={isActive}
                onClick={() => setIsActive(!isActive)}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                  isActive ? "bg-primary" : "bg-muted"
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 rounded-full bg-background shadow-sm transition-transform ${
                    isActive ? "translate-x-[18px]" : "translate-x-0.5"
                  }`}
                />
              </button>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSaving}>
            {tc("cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={!name.trim() || isSaving}>
            {isSaving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
            {isEdit ? t("saveChanges") : t("addServerBtn")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
