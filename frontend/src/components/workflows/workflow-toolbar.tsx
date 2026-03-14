"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Bell,
  Braces,
  CheckCircle2,
  Copy,
  Download,
  GitBranch,
  Globe,
  GlobeLock,
  History,
  Key,
  LayoutGrid,
  Loader2,
  MoreHorizontal,
  Play,
  Redo2,
  RotateCw,
  Save,
  Trash2,
  Undo2,
  Upload,
  XCircle,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

export interface ValidationResult {
  valid: boolean
  warnings: Array<{ node_id: string | null; code: string; message: string }>
  error?: string
}

interface WorkflowToolbarProps {
  name: string
  description?: string | null
  status: "draft" | "active"
  visibility?: string
  publishStatus?: string | null
  isSaving: boolean
  isDirty?: boolean
  lastSavedAt?: Date | null
  isRunning: boolean
  isDuplicating?: boolean
  isValidating?: boolean
  validationResult?: ValidationResult | null
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
  onNameChange: (name: string) => void
  onDescriptionChange?: (description: string) => void
  onSave: () => void
  onRun: () => void
  onExport: () => void
  onImport: () => void
  onDuplicate: () => void
  onDelete: () => void
  onHistory: () => void
  onVersionHistory?: () => void
  onStats: () => void
  onAutoLayout: () => void
  onNodeStats?: () => void
  onValidate?: () => void
  onPublish?: () => void
  onUnpublish?: () => void
  onResubmit?: () => void
  onEnvVars?: () => void
  onVariables?: () => void
  onAnalytics?: () => void
  onWebhook?: () => void
  webhookConfigured?: boolean
}

export function WorkflowToolbar({
  name,
  description,
  status,
  visibility = "personal",
  publishStatus,
  isSaving,
  isDirty = false,
  lastSavedAt,
  isRunning,
  isDuplicating = false,
  isValidating = false,
  validationResult,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  onNameChange,
  onDescriptionChange,
  onSave,
  onRun,
  onExport,
  onImport,
  onDuplicate,
  onDelete,
  onAutoLayout,
  onHistory,
  onVersionHistory,
  onStats,
  onNodeStats,
  onValidate,
  onPublish,
  onUnpublish,
  onResubmit,
  onEnvVars,
  onVariables,
  onAnalytics,
  onWebhook,
  webhookConfigured = false,
}: WorkflowToolbarProps) {
  const t = useTranslations("workflows")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const [isEditing, setIsEditing] = useState(false)
  const [editValue, setEditValue] = useState(name)
  const [descOpen, setDescOpen] = useState(false)
  const [descValue, setDescValue] = useState(description ?? "")
  const isPublished = visibility !== "personal"

  const startEditing = () => {
    setEditValue(name)
    setIsEditing(true)
  }

  const finishEditing = () => {
    setIsEditing(false)
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== name) {
      onNameChange(trimmed)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") finishEditing()
    if (e.key === "Escape") {
      setEditValue(name)
      setIsEditing(false)
    }
  }

  return (
    <div className="flex items-center gap-3 px-4 py-2 border-b border-border/40 bg-background shrink-0">
      {/* Back button */}
      <Button variant="ghost" size="sm" className="gap-1.5 shrink-0" asChild>
        <Link href="/workflows">
          <ArrowLeft className="h-3.5 w-3.5" />
          {t("editorBackToList")}
        </Link>
      </Button>

      {/* Workflow name & description */}
      <div className="flex items-center gap-2 flex-1 min-w-0">
        <div className="flex flex-col min-w-0">
          <div className="flex items-center gap-2">
            {isEditing ? (
              <Input
                className="h-7 text-sm font-medium max-w-[300px]"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onBlur={finishEditing}
                onKeyDown={handleKeyDown}
                autoFocus
              />
            ) : (
              <button
                onClick={startEditing}
                className="text-sm font-medium text-foreground hover:text-foreground/80 truncate max-w-[300px] text-left transition-colors"
              >
                {name || t("editorUntitled")}
              </button>
            )}
          </div>
          {onDescriptionChange && (
            <Popover open={descOpen} onOpenChange={(open) => {
              if (!open && descValue !== (description ?? "")) {
                onDescriptionChange(descValue)
              }
              setDescOpen(open)
            }}>
              <PopoverTrigger asChild>
                <button
                  className="text-[11px] text-muted-foreground hover:text-foreground/70 truncate max-w-[300px] text-left transition-colors leading-tight"
                  onClick={() => {
                    setDescValue(description ?? "")
                    setDescOpen(true)
                  }}
                >
                  {description || t("editorAddDescription")}
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-80 p-2">
                <Textarea
                  className="text-sm min-h-[80px] resize-none"
                  placeholder={t("editorAddDescription")}
                  value={descValue}
                  onChange={(e) => setDescValue(e.target.value)}
                  autoFocus
                />
              </PopoverContent>
            </Popover>
          )}
        </div>
        <Badge
          variant="secondary"
          className={cn(
            "text-[10px] px-1.5 py-0 h-5 shrink-0",
            isPublished
              ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
              : ""
          )}
        >
          {isPublished ? tc("published") : status === "active" ? t("statusActive") : t("statusDraft")}
        </Badge>
        {publishStatus === "pending_review" && (
          <Badge
            variant="outline"
            className="text-[10px] px-1.5 py-0 h-5 shrink-0 border-amber-400 text-amber-600 dark:text-amber-400"
          >
            {to("publishStatusPending")}
          </Badge>
        )}
        {publishStatus === "rejected" && (
          <Badge
            variant="outline"
            className="text-[10px] px-1.5 py-0 h-5 shrink-0 border-destructive text-destructive"
          >
            {to("publishStatusRejected")}
          </Badge>
        )}
        {/* Auto-save status indicator */}
        <SaveStatusIndicator isSaving={isSaving} isDirty={isDirty} lastSavedAt={lastSavedAt ?? null} />
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-1.5 shrink-0">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onUndo}
              disabled={!canUndo}
              aria-label={t("editorUndo")}
            >
              <Undo2 className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">{t("editorUndo")}</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onRedo}
              disabled={!canRedo}
              aria-label={t("editorRedo")}
            >
              <Redo2 className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">{t("editorRedo")}</TooltipContent>
        </Tooltip>

        {/* Validation indicator */}
        {isValidating ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium text-muted-foreground border-border">
                <Loader2 className="h-3 w-3 animate-spin" />
                {t("validationChecking")}
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t("validationChecking")}</TooltipContent>
          </Tooltip>
        ) : validationResult ? (
          validationResult.valid && validationResult.warnings.length === 0 ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400 border-emerald-500/20 bg-emerald-500/10">
                  <CheckCircle2 className="h-3 w-3" />
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom">{t("validationValid")}</TooltipContent>
            </Tooltip>
          ) : validationResult.valid && validationResult.warnings.length > 0 ? (
            <Popover>
              <PopoverTrigger asChild>
                <button
                  className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400 border-amber-400/30 bg-amber-500/10 hover:bg-amber-500/20 transition-colors"
                  aria-label={t("validationWarnings", { count: validationResult.warnings.length })}
                >
                  <AlertTriangle className="h-3 w-3" />
                  {validationResult.warnings.length}
                </button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-80 p-0">
                <div className="px-3 py-2 border-b border-border/40">
                  <p className="text-xs font-medium text-amber-600 dark:text-amber-400">
                    {t("validationWarnings", { count: validationResult.warnings.length })}
                  </p>
                </div>
                <div className="max-h-60 overflow-y-auto">
                  {validationResult.warnings.map((w, i) => (
                    <div
                      key={`${w.node_id ?? "global"}-${w.code}-${i}`}
                      className="px-3 py-2 text-xs border-b border-border/20 last:border-0"
                    >
                      <p className="text-foreground">{w.message}</p>
                      {w.node_id && (
                        <p className="text-muted-foreground mt-0.5">
                          {w.node_id}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </PopoverContent>
            </Popover>
          ) : (
            <Popover>
              <PopoverTrigger asChild>
                <button
                  className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium text-destructive border-destructive/30 bg-destructive/10 hover:bg-destructive/20 transition-colors"
                  aria-label={t("validationInvalid")}
                >
                  <XCircle className="h-3 w-3" />
                </button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-80 p-0">
                <div className="px-3 py-2 border-b border-border/40">
                  <p className="text-xs font-medium text-destructive">
                    {t("validationInvalid")}
                  </p>
                </div>
                <div className="max-h-60 overflow-y-auto">
                  {validationResult.error && (
                    <div className="px-3 py-2 text-xs text-destructive border-b border-border/20">
                      {validationResult.error}
                    </div>
                  )}
                  {validationResult.warnings.map((w, i) => (
                    <div
                      key={`${w.node_id ?? "global"}-${w.code}-${i}`}
                      className="px-3 py-2 text-xs border-b border-border/20 last:border-0"
                    >
                      <p className="text-foreground">{w.message}</p>
                      {w.node_id && (
                        <p className="text-muted-foreground mt-0.5">
                          {w.node_id}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </PopoverContent>
            </Popover>
          )
        ) : null}

        {onValidate && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={onValidate}
                disabled={isValidating}
                aria-label={t("validateButton")}
              >
                {isValidating ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <CheckCircle2 className="h-3.5 w-3.5" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t("validateButton")}</TooltipContent>
          </Tooltip>
        )}

        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={onSave}
          disabled={isSaving}
        >
          {isSaving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          {isSaving ? t("editorSaving") : t("editorSave")}
        </Button>

        <Button
          size="sm"
          className="gap-1.5"
          onClick={onRun}
          disabled={isRunning}
        >
          {isRunning ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
          {isRunning ? t("editorRunning") : t("editorRun")}
        </Button>

        <Button
          variant="ghost"
          size="sm"
          className="gap-1.5"
          onClick={onHistory}
        >
          <History className="h-3.5 w-3.5" />
          {t("historyButton")}
        </Button>

        {onVersionHistory && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={onVersionHistory}
                aria-label={t("versionHistoryButton")}
              >
                <GitBranch className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t("versionHistoryButton")}</TooltipContent>
          </Tooltip>
        )}

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onStats}
              aria-label={t("statsButton")}
            >
              <BarChart3 className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">{t("statsButton")}</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onAutoLayout}
            >
              <LayoutGrid className="h-3.5 w-3.5" />
              <span className="sr-only">{t("editorAutoLayout")}</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t("editorAutoLayout")}</TooltipContent>
        </Tooltip>

        {onVariables && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={onVariables}
                aria-label={t("variablesButton")}
              >
                <Braces className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t("variablesButton")}</TooltipContent>
          </Tooltip>
        )}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm">
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={onImport}>
              <Upload className="h-4 w-4" />
              {tc("import")}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onExport}>
              <Download className="h-4 w-4" />
              {tc("export")}
            </DropdownMenuItem>
            {onEnvVars && (
              <DropdownMenuItem onClick={onEnvVars}>
                <Key className="h-4 w-4" />
                {t("envVarsMenuItem")}
              </DropdownMenuItem>
            )}
            {onWebhook && (
              <DropdownMenuItem onClick={onWebhook}>
                <span className="relative">
                  <Bell className="h-4 w-4" />
                  {webhookConfigured && (
                    <span className="absolute -top-0.5 -right-0.5 h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  )}
                </span>
                {t("webhookMenuItem")}
              </DropdownMenuItem>
            )}
            {onAnalytics && (
              <DropdownMenuItem onClick={onAnalytics}>
                <BarChart3 className="h-4 w-4" />
                {t("analyticsButton")}
              </DropdownMenuItem>
            )}
            {onNodeStats && (
              <DropdownMenuItem onClick={onNodeStats}>
                <Activity className="h-4 w-4" />
                {t("nodeStatsButton")}
              </DropdownMenuItem>
            )}
            <DropdownMenuItem onClick={onDuplicate} disabled={isDuplicating}>
              {isDuplicating ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
              {t("editorDuplicate")}
            </DropdownMenuItem>
            {(onPublish || onUnpublish) && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={isPublished ? onUnpublish : onPublish}>
                  {isPublished ? <GlobeLock className="h-4 w-4" /> : <Globe className="h-4 w-4" />}
                  {isPublished ? tc("unpublish") : tc("publish")}
                </DropdownMenuItem>
              </>
            )}
            {publishStatus === "rejected" && onResubmit && (
              <DropdownMenuItem onClick={onResubmit}>
                <RotateCw className="h-4 w-4" />
                {to("resubmit")}
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={onDelete}>
              <Trash2 className="h-4 w-4" />
              {tc("delete")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}

/** Compact save status: "Saving..." | "Saved X ago" | "Unsaved" */
function SaveStatusIndicator({
  isSaving,
  isDirty,
  lastSavedAt,
}: {
  isSaving: boolean
  isDirty: boolean
  lastSavedAt: Date | null
}) {
  const t = useTranslations("workflows")
  const [, forceRender] = useState(0)

  // Re-render every 30s to update relative time
  useEffect(() => {
    if (!lastSavedAt) return
    const id = setInterval(() => forceRender((n) => n + 1), 30_000)
    return () => clearInterval(id)
  }, [lastSavedAt])

  if (isSaving) {
    return (
      <span className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0">
        <Loader2 className="h-3 w-3 animate-spin" />
        {t("editorSaving")}
      </span>
    )
  }

  if (isDirty) {
    return (
      <span className="text-[10px] text-amber-500 dark:text-amber-400 shrink-0">
        {t("editorUnsaved")}
      </span>
    )
  }

  if (lastSavedAt) {
    const seconds = Math.floor((Date.now() - lastSavedAt.getTime()) / 1000)
    const label =
      seconds < 5
        ? t("editorSavedJustNow")
        : seconds < 60
          ? t("editorSavedSecondsAgo", { seconds })
          : t("editorSavedMinutesAgo", { minutes: Math.floor(seconds / 60) })

    return (
      <span className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0">
        <CheckCircle2 className="h-3 w-3 text-green-500" />
        {label}
      </span>
    )
  }

  return null
}
