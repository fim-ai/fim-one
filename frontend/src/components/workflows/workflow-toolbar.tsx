"use client"

import { useState } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import {
  ArrowLeft,
  Copy,
  Download,
  Globe,
  GlobeLock,
  History,
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
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
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

interface WorkflowToolbarProps {
  name: string
  status: "draft" | "active"
  visibility?: string
  publishStatus?: string | null
  isSaving: boolean
  isRunning: boolean
  isDuplicating?: boolean
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
  onNameChange: (name: string) => void
  onSave: () => void
  onRun: () => void
  onExport: () => void
  onImport: () => void
  onDuplicate: () => void
  onDelete: () => void
  onHistory: () => void
  onAutoLayout: () => void
  onPublish?: () => void
  onUnpublish?: () => void
  onResubmit?: () => void
}

export function WorkflowToolbar({
  name,
  status,
  visibility = "personal",
  publishStatus,
  isSaving,
  isRunning,
  isDuplicating = false,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  onNameChange,
  onSave,
  onRun,
  onExport,
  onImport,
  onDuplicate,
  onDelete,
  onAutoLayout,
  onHistory,
  onPublish,
  onUnpublish,
  onResubmit,
}: WorkflowToolbarProps) {
  const t = useTranslations("workflows")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const [isEditing, setIsEditing] = useState(false)
  const [editValue, setEditValue] = useState(name)
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

      {/* Workflow name */}
      <div className="flex items-center gap-2 flex-1 min-w-0">
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
