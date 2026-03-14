"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"
import {
  Activity,
  CheckCircle2,
  Clock,
  Copy,
  Download,
  Eye,
  GitBranch,
  Globe,
  GlobeLock,
  MoreHorizontal,
  PackageMinus,
  Pencil,
  Play,
  RotateCw,
  Star,
  Store,
  Trash2,
  Users,
  XCircle,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { MARKET_ORG_ID } from "@/lib/constants"
import type { WorkflowResponse } from "@/types/workflow"

interface WorkflowCardProps {
  workflow: WorkflowResponse
  currentUserId?: string
  isFavorite?: boolean
  onToggleFavorite?: () => void
  onDelete: (id: string) => void
  onExport: (id: string) => void
  onDuplicate: (id: string) => void
  onPublish: (id: string) => void
  onUnpublish: (id: string) => void
  onUninstall?: (id: string) => void
  onResubmit?: (id: string) => void
}

export function WorkflowCard({
  workflow,
  currentUserId,
  isFavorite = false,
  onToggleFavorite,
  onDelete,
  onExport,
  onDuplicate,
  onPublish,
  onUnpublish,
  onUninstall,
  onResubmit,
}: WorkflowCardProps) {
  const t = useTranslations("workflows")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const isPublished = workflow.visibility !== "personal"
  const isOwner = !currentUserId || workflow.user_id === currentUserId
  const isOrgResource = workflow.visibility !== "personal"
  const isActive = workflow.status === "active"
  const source = (workflow as unknown as { source?: string }).source
  const isInstalled = source === "installed"
  const isOrgShared = source === "org" || (!source && !isOwner)
  const nodeCount = workflow.blueprint?.nodes?.length ?? 0

  return (
    <div className="group flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-ring/40 hover:bg-accent/10">
      {/* Header: icon + name + dropdown menu */}
      <div className="flex items-center gap-2 mb-1.5">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground flex items-center gap-1.5">
          {workflow.icon ? (
            <span className="shrink-0 text-base leading-none">{workflow.icon}</span>
          ) : (
            <GitBranch className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          {workflow.name}
        </h3>
        {onToggleFavorite && (
          <Button
            variant="ghost"
            size="icon-xs"
            className={cn(
              "shrink-0 h-7 w-7 transition-colors",
              isFavorite
                ? "text-amber-400"
                : "text-muted-foreground opacity-0 group-hover:opacity-100",
            )}
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onToggleFavorite()
            }}
          >
            <Star
              className={cn(
                "h-4 w-4",
                isFavorite && "fill-amber-400",
              )}
            />
          </Button>
        )}
        {isOwner ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0 text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 transition-opacity"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                <Link href={`/workflows/${workflow.id}`}>
                  <Pencil className="h-4 w-4" />
                  {tc("edit")}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href={`/workflows/${workflow.id}?run=true`}>
                  <Play className="h-4 w-4" />
                  {t("editorRun")}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => onExport(workflow.id)}>
                <Download className="h-4 w-4" />
                {tc("export")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => onDuplicate(workflow.id)}>
                <Copy className="h-4 w-4" />
                {t("editorDuplicate")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => isPublished ? onUnpublish(workflow.id) : onPublish(workflow.id)}>
                {isPublished ? <GlobeLock className="h-4 w-4" /> : <Globe className="h-4 w-4" />}
                {isPublished ? tc("unpublish") : tc("publish")}
              </DropdownMenuItem>
              {workflow.publish_status === "rejected" && onResubmit && (
                <DropdownMenuItem onClick={() => onResubmit(workflow.id)}>
                  <RotateCw className="h-4 w-4" />
                  {to("resubmit")}
                </DropdownMenuItem>
              )}
              {onToggleFavorite && (
                <DropdownMenuItem onClick={onToggleFavorite}>
                  <Star className={cn("h-4 w-4", isFavorite && "fill-amber-400 text-amber-400")} />
                  {isFavorite ? t("unfavorite") : t("favorite")}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => onDelete(workflow.id)}>
                <Trash2 className="h-4 w-4" />
                {tc("delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : isInstalled && onUninstall ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0 text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 transition-opacity"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem variant="destructive" onClick={() => onUninstall(workflow.id)}>
                <PackageMinus className="h-4 w-4" />
                {tc("uninstall")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>

      {/* Status badges */}
      <div className="flex items-center gap-1.5 mb-2 flex-wrap">
        <Badge
          variant="secondary"
          className={cn(
            "text-[10px] px-1.5 py-0 h-5",
            isPublished
              ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
              : isActive
                ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                : "opacity-60",
          )}
        >
          {isPublished ? tc("published") : isActive ? t("statusActive") : t("statusDraft")}
        </Badge>
        {isInstalled && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-violet-500/10 text-violet-500 dark:text-violet-400 border-violet-500/20"
          >
            <Download className="h-2.5 w-2.5 mr-0.5" />
            {tc("installed")}
          </Badge>
        )}
        {!isInstalled && isOrgShared && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
          >
            <Users className="h-2.5 w-2.5 mr-0.5" />
            {tc("shared")}
          </Badge>
        )}
        {nodeCount > 0 && (
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5 opacity-60">
            {t("nodeCount", { count: nodeCount })}
          </Badge>
        )}

        {/* Owner visibility badge */}
        {isOwner && isOrgResource && (
          <Badge
            variant="secondary"
            className={cn(
              "text-[10px] px-1.5 py-0 h-5",
              workflow.org_id === MARKET_ORG_ID
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                : "bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
            )}
          >
            {workflow.org_id === MARKET_ORG_ID ? (
              <><Store className="h-2.5 w-2.5 mr-0.5" />{tc("publishedToMarket")}</>
            ) : (
              <><Globe className="h-2.5 w-2.5 mr-0.5" />{tc("published")}</>
            )}
          </Badge>
        )}

        {/* Publish review status badges */}
        {isOwner && workflow.publish_status === "pending_review" && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20"
          >
            <Clock className="h-2.5 w-2.5 mr-0.5" />
            {to("publishStatusPending")}
          </Badge>
        )}
        {isOwner && workflow.publish_status === "approved" && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
          >
            <CheckCircle2 className="h-2.5 w-2.5 mr-0.5" />
            {to("publishStatusApproved")}
          </Badge>
        )}
        {isOwner && workflow.publish_status === "rejected" && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant="secondary"
                  className="text-[10px] px-1.5 py-0 h-5 bg-red-500/10 text-red-500 dark:text-red-400 border-red-500/20 cursor-default"
                >
                  <XCircle className="h-2.5 w-2.5 mr-0.5" />
                  {to("publishStatusRejected")}
                </Badge>
              </TooltipTrigger>
              {workflow.review_note && (
                <TooltipContent>
                  <p>{to("rejectedNote", { note: workflow.review_note })}</p>
                </TooltipContent>
              )}
            </Tooltip>
          </TooltipProvider>
        )}
      </div>

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2 mb-2">
        {workflow.description || t("noDescription")}
      </p>

      {/* Run stats */}
      {workflow.total_runs > 0 ? (
        <div className="flex items-center gap-2 mb-3 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-0.5">
            <Activity className="h-3 w-3" />
            {t("runCount", { count: workflow.total_runs })}
          </span>
          {workflow.success_rate != null && (
            <span className={cn(
              workflow.success_rate >= 80 ? "text-emerald-600 dark:text-emerald-400" :
              workflow.success_rate >= 50 ? "text-amber-600 dark:text-amber-400" :
              "text-destructive",
            )}>
              {t("successRate", { rate: workflow.success_rate })}
            </span>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-1 mb-3 text-[10px] text-muted-foreground opacity-50">
          <Activity className="h-3 w-3" />
          {t("noRuns")}
        </div>
      )}

      {/* CTA */}
      <Button
        variant="outline"
        size="sm"
        className="mt-auto w-full gap-1.5 text-xs h-7"
        asChild
      >
        <Link href={`/workflows/${workflow.id}`}>
          {isOwner ? <Pencil className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
          {isOwner ? tc("edit") : tc("view")}
        </Link>
      </Button>
    </div>
  )
}
