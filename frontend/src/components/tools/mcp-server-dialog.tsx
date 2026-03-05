"use client"

import { useState, useEffect } from "react"
import { Loader2, Plus, X } from "lucide-react"
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
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { mcpServerApi } from "@/lib/api"
import { toast } from "sonner"
import type { MCPServerResponse, MCPServerCreate, MCPServerUpdate } from "@/types/mcp-server"

export interface MCPServerInitialValues {
  name?: string
  description?: string
  transport?: "stdio" | "sse" | "streamable_http"
  url?: string
  command?: string
  args?: string
}

interface MCPServerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  server?: MCPServerResponse | null
  initialValues?: MCPServerInitialValues | null
  onSuccess: (server: MCPServerResponse) => void
  allowStdio?: boolean
}

export function MCPServerDialog({
  open,
  onOpenChange,
  server,
  initialValues,
  onSuccess,
  allowStdio = true,
}: MCPServerDialogProps) {
  const isEdit = !!server

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [transport, setTransport] = useState<"stdio" | "sse" | "streamable_http">("stdio")
  const [command, setCommand] = useState("")
  const [args, setArgs] = useState("")
  const [url, setUrl] = useState("")
  const [workingDir, setWorkingDir] = useState("")
  const [envPairs, setEnvPairs] = useState<Array<{ key: string; value: string }>>([])
  const [headerPairs, setHeaderPairs] = useState<Array<{ key: string; value: string }>>([])
  const [isActive, setIsActive] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)

  // Reset form when dialog opens or server changes
  useEffect(() => {
    if (open) {
      if (server) {
        setName(server.name)
        setDescription(server.description || "")
        setTransport(!allowStdio && server.transport === "stdio" ? "sse" : server.transport)
        setCommand(server.command || "")
        setArgs(server.args?.join(", ") || "")
        setUrl(server.url || "")
        setEnvPairs(
          server.env
            ? Object.entries(server.env).map(([key, value]) => ({ key, value }))
            : []
        )
        setWorkingDir(server.working_dir || "")
        setHeaderPairs(
          server.headers
            ? Object.entries(server.headers).map(([key, value]) => ({ key, value }))
            : []
        )
        setIsActive(server.is_active)
      } else {
        setName(initialValues?.name ?? "")
        setDescription(initialValues?.description ?? "")
        setTransport(initialValues?.transport ?? (allowStdio ? "stdio" : "sse"))
        setCommand(initialValues?.command ?? "")
        setArgs(initialValues?.args ?? "")
        setUrl(initialValues?.url ?? "")
        setWorkingDir("")
        setEnvPairs([])
        setHeaderPairs([])
        setIsActive(true)
      }
    }
  }, [open, server])

  // isDirty: create mode = any meaningful field has content; edit mode = any field differs from original
  const isDirty = server
    ? name !== server.name ||
      description !== (server.description || "") ||
      transport !== server.transport ||
      command !== (server.command || "") ||
      args !== (server.args?.join(", ") || "") ||
      url !== (server.url || "") ||
      workingDir !== (server.working_dir || "") ||
      isActive !== server.is_active ||
      JSON.stringify(envPairs) !== JSON.stringify(
        server.env ? Object.entries(server.env).map(([key, value]) => ({ key, value })) : []
      ) ||
      JSON.stringify(headerPairs) !== JSON.stringify(
        server.headers ? Object.entries(server.headers).map(([key, value]) => ({ key, value })) : []
      )
    : name.trim().length > 0 || description.trim().length > 0 ||
      command.trim().length > 0 || url.trim().length > 0 ||
      args.trim().length > 0 || workingDir.trim().length > 0 ||
      envPairs.length > 0 || headerPairs.length > 0

  const handleClose = (open: boolean) => {
    if (!open && isDirty) { setShowCloseConfirm(true); return }
    onOpenChange(open)
  }

  const addEnvPair = () => setEnvPairs((prev) => [...prev, { key: "", value: "" }])

  const removeEnvPair = (index: number) =>
    setEnvPairs((prev) => prev.filter((_, i) => i !== index))

  const updateEnvPair = (index: number, field: "key" | "value", val: string) =>
    setEnvPairs((prev) =>
      prev.map((pair, i) => (i === index ? { ...pair, [field]: val } : pair))
    )

  const addHeaderPair = () => setHeaderPairs((prev) => [...prev, { key: "", value: "" }])

  const removeHeaderPair = (index: number) =>
    setHeaderPairs((prev) => prev.filter((_, i) => i !== index))

  const updateHeaderPair = (index: number, field: "key" | "value", val: string) =>
    setHeaderPairs((prev) =>
      prev.map((pair, i) => (i === index ? { ...pair, [field]: val } : pair))
    )

  const handleSubmit = async () => {
    if (!name.trim()) return

    setIsSaving(true)
    try {
      const envObj =
        envPairs.length > 0
          ? Object.fromEntries(
              envPairs
                .filter((p) => p.key.trim())
                .map((p) => [p.key.trim(), p.value])
            )
          : null

      const parsedArgs =
        args.trim()
          ? args.split(",").map((a) => a.trim()).filter(Boolean)
          : null

      const headersObj =
        (transport === "sse" || transport === "streamable_http") && headerPairs.length > 0
          ? Object.fromEntries(
              headerPairs
                .filter((p) => p.key.trim())
                .map((p) => [p.key.trim(), p.value])
            )
          : null

      if (isEdit && server) {
        const body: MCPServerUpdate = {
          name: name.trim(),
          description: description.trim() || null,
          transport,
          command: transport === "stdio" ? command.trim() || null : null,
          args: transport === "stdio" ? parsedArgs : null,
          env: transport === "stdio" ? envObj : null,
          working_dir: transport === "stdio" ? workingDir.trim() || null : null,
          url: (transport === "sse" || transport === "streamable_http") ? url.trim() || null : null,
          headers: headersObj,
          is_active: isActive,
        }
        const updated = await mcpServerApi.update(server.id, body)
        onSuccess(updated)
      } else {
        const body: MCPServerCreate = {
          name: name.trim(),
          description: description.trim() || null,
          transport,
          command: transport === "stdio" ? command.trim() || null : null,
          args: transport === "stdio" ? parsedArgs : null,
          env: transport === "stdio" ? envObj : null,
          working_dir: transport === "stdio" ? workingDir.trim() || null : null,
          url: (transport === "sse" || transport === "streamable_http") ? url.trim() || null : null,
          headers: headersObj,
          is_active: isActive,
        }
        const created = await mcpServerApi.create(body)
        onSuccess(created)
      }
      toast.success(isEdit ? "MCP server updated" : "MCP server created")
      onOpenChange(false)
    } catch {
      toast.error("Failed to save MCP server")
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <>
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className="sm:max-w-lg max-h-[85vh] overflow-y-auto"
        onInteractOutside={(e) => {
          if (isDirty) { e.preventDefault(); setShowCloseConfirm(true) }
        }}
      >
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit MCP Server" : "Add MCP Server"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the MCP server configuration."
              : "Configure a new MCP server connection."}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          {/* Name */}
          <div className="grid gap-1.5">
            <label className="text-sm font-medium">Name</label>
            <Input
              placeholder="e.g. filesystem-server"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          {/* Description */}
          <div className="grid gap-1.5">
            <label className="text-sm font-medium">Description</label>
            <Textarea
              placeholder="Optional description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
            />
          </div>

          {/* Transport */}
          <div className="grid gap-1.5">
            <label className="text-sm font-medium">Transport</label>
            <div className="flex gap-2">
              {allowStdio && (
                <Button
                  type="button"
                  variant={transport === "stdio" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTransport("stdio")}
                >
                  STDIO
                </Button>
              )}
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
          {transport === "stdio" && allowStdio && (
            <>
              <div className="grid gap-1.5">
                <label className="text-sm font-medium">Command</label>
                <Input
                  placeholder="e.g. npx or python"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <label className="text-sm font-medium">Arguments</label>
                <Input
                  placeholder="Comma-separated, e.g. -y, @modelcontextprotocol/server-filesystem, /tmp"
                  value={args}
                  onChange={(e) => setArgs(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Separate multiple arguments with commas
                </p>
              </div>
              <div className="grid gap-1.5">
                <label className="text-sm font-medium">Working Directory <span className="text-muted-foreground font-normal">(optional)</span></label>
                <Input
                  placeholder="e.g. /home/user/my-server"
                  value={workingDir}
                  onChange={(e) => setWorkingDir(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Environment Variables</label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 gap-1 text-xs"
                    onClick={addEnvPair}
                  >
                    <Plus className="h-3 w-3" />
                    Add
                  </Button>
                </div>
                {envPairs.map((pair, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <Input
                      placeholder="KEY"
                      className="flex-1 font-mono text-xs"
                      value={pair.key}
                      onChange={(e) => updateEnvPair(idx, "key", e.target.value)}
                    />
                    <span className="text-muted-foreground text-xs">=</span>
                    <Input
                      placeholder="value"
                      className="flex-1 text-xs"
                      value={pair.value}
                      onChange={(e) => updateEnvPair(idx, "value", e.target.value)}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => removeEnvPair(idx)}
                      className="shrink-0 text-muted-foreground hover:text-destructive"
                    >
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
                <label className="text-sm font-medium">Server URL</label>
                <Input
                  placeholder={transport === "sse" ? "e.g. http://localhost:3001/sse" : "e.g. http://localhost:3001/mcp"}
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">HTTP Headers</label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 gap-1 text-xs"
                    onClick={addHeaderPair}
                  >
                    <Plus className="h-3 w-3" />
                    Add
                  </Button>
                </div>
                {headerPairs.map((pair, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <Input
                      placeholder="Header-Name"
                      className="flex-1 font-mono text-xs"
                      value={pair.key}
                      onChange={(e) => updateHeaderPair(idx, "key", e.target.value)}
                    />
                    <span className="text-muted-foreground text-xs">:</span>
                    <Input
                      placeholder="value"
                      className="flex-1 text-xs"
                      value={pair.value}
                      onChange={(e) => updateHeaderPair(idx, "value", e.target.value)}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => removeHeaderPair(idx)}
                      className="shrink-0 text-muted-foreground hover:text-destructive"
                    >
                      <X className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
                <p className="text-xs text-muted-foreground">e.g. Authorization: Bearer token</p>
              </div>
            </>
          )}

          {/* Active toggle */}
          <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
            <div>
              <p className="text-sm font-medium">Active</p>
              <p className="text-xs text-muted-foreground">
                Enable this server for agent tool usage
              </p>
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

        <DialogFooter>
          <Button variant="ghost" onClick={() => handleClose(false)} disabled={isSaving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!name.trim() || isSaving}>
            {isSaving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
            {isEdit ? "Save Changes" : "Add Server"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <AlertDialog open={showCloseConfirm} onOpenChange={setShowCloseConfirm}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
          <AlertDialogDescription>
            You have unsaved changes. Closing will discard them.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep editing</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => onOpenChange(false)}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            Discard & close
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    </>
  )
}
