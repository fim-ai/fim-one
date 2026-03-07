"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { Pencil, Trash2, Terminal, Globe, FlaskConical, Loader2, CheckCircle2, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import type { MCPServerResponse } from "@/types/mcp-server"

interface MCPServerCardProps {
  server: MCPServerResponse
  onEdit: () => void
  onDelete: () => void
  onToggleActive: (isActive: boolean) => void
  onTest: () => Promise<{ ok: boolean; tool_count?: number; error?: string }>
}

export function MCPServerCard({ server, onEdit, onDelete, onToggleActive, onTest }: MCPServerCardProps) {
  const t = useTranslations("tools")
  const tc = useTranslations("common")
  const endpoint = server.transport === "stdio" ? server.command : server.url
  const isRemoteTransport = server.transport === "sse" || server.transport === "streamable_http"
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; tool_count?: number; error?: string } | null>(null)

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await onTest()
      setTestResult(result)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-ring/40 hover:bg-accent/10">
      {/* Header: name + badges */}
      <div className="flex items-center gap-2 mb-2">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground">
          {server.name}
        </h3>
        <Badge
          variant="outline"
          className="shrink-0 text-[10px] uppercase tracking-wide"
        >
          {server.transport === "streamable_http" ? "HTTP" : server.transport.toUpperCase()}
        </Badge>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              role="switch"
              aria-checked={server.is_active}
              onClick={() => onToggleActive(!server.is_active)}
              className={`relative shrink-0 inline-flex h-4 w-7 items-center rounded-full transition-colors focus-visible:outline-none ${
                server.is_active ? "bg-green-500" : "bg-muted-foreground/30"
              }`}
            >
              <span
                className={`inline-block h-3 w-3 rounded-full bg-white shadow-sm transition-transform ${
                  server.is_active ? "translate-x-[14px]" : "translate-x-0.5"
                }`}
              />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>
            {server.is_active ? tc("disable") : tc("enable")}
          </TooltipContent>
        </Tooltip>
      </div>

      {/* Endpoint */}
      {endpoint && (
        <Tooltip>
          <TooltipTrigger asChild>
            <p className="text-xs text-muted-foreground truncate mb-1">
              {isRemoteTransport ? (
                <Globe className="inline h-3 w-3 mr-1 -mt-0.5" />
              ) : (
                <Terminal className="inline h-3 w-3 mr-1 -mt-0.5" />
              )}
              {endpoint}
            </p>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>
            {endpoint}
          </TooltipContent>
        </Tooltip>
      )}

      {/* Tool count / test result */}
      {testResult ? (
        <p className={`text-xs mb-1 flex items-center gap-1 ${testResult.ok ? "text-green-600 dark:text-green-400" : "text-destructive"}`}>
          {testResult.ok
            ? <><CheckCircle2 className="h-3 w-3" />{t("toolsFound", { count: testResult.tool_count ?? 0 })}</>
            : <><XCircle className="h-3 w-3" /><span className="truncate" title={testResult.error}>{testResult.error}</span></>
          }
        </p>
      ) : server.tool_count > 0 ? (
        <p className="text-xs text-muted-foreground mb-1">
          {t("toolCount", { count: server.tool_count })}
        </p>
      ) : null}

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2 mb-3">
        {server.description || t("noDescription")}
      </p>

      {/* Action buttons */}
      <div className="flex items-center gap-1 -ml-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-muted-foreground hover:text-foreground"
              onClick={onEdit}
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>{tc("edit")}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-muted-foreground hover:text-foreground"
              onClick={handleTest}
              disabled={testing}
            >
              {testing
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : <FlaskConical className="h-3.5 w-3.5" />
              }
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>{t("testConnection")}</TooltipContent>
        </Tooltip>
        <div className="flex-1" />
        <AlertDialog>
          <Tooltip>
            <TooltipTrigger asChild>
              <AlertDialogTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  className="text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </AlertDialogTrigger>
            </TooltipTrigger>
            <TooltipContent side="bottom" sideOffset={5}>{tc("delete")}</TooltipContent>
          </Tooltip>
          <AlertDialogContent className="sm:max-w-sm">
            <AlertDialogHeader>
              <AlertDialogTitle className="flex items-center gap-2">
                <Trash2 className="h-4 w-4" />
                {t("deleteMcpServer")}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {t("deleteMcpServerDescription")}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
              <AlertDialogAction
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                onClick={onDelete}
              >
                {tc("delete")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </div>
  )
}
