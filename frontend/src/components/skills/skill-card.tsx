"use client"

import { useTranslations } from "next-intl"
import {
  BookOpen,
  CheckCircle2,
  Clock,
  Download,
  Globe,
  GlobeLock,
  MoreHorizontal,
  PackageMinus,
  Pencil,
  RotateCw,
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
import type { SkillResponse } from "@/types/skill"

interface SkillCardProps {
  skill: SkillResponse
  currentUserId?: string
  onEdit?: (skill: SkillResponse) => void
  onDelete: (id: string) => void
  onPublish: (id: string) => void
  onUnpublish: (id: string) => void
  onUninstall?: (id: string) => void
  onResubmit?: (id: string) => void
}

export function SkillCard({
  skill,
  currentUserId,
  onEdit,
  onDelete,
  onPublish,
  onUnpublish,
  onUninstall,
  onResubmit,
}: SkillCardProps) {
  const t = useTranslations("skills")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const isPublished = skill.visibility !== "personal"
  const isOwner = !currentUserId || skill.user_id === currentUserId
  const isOrgResource = skill.visibility === "org" || skill.visibility === "global"
  const isActive = skill.is_active
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const source = (skill as any).source as string | undefined
  const isInstalled = source === "installed"
  const isOrgShared = source === "org" || (!source && !isOwner)

  return (
    <div className="group flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-ring/40 hover:bg-accent/10">
      {/* Header: icon + name + dropdown menu */}
      <div className="flex items-center gap-2 mb-1.5">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground flex items-center gap-1.5">
          <BookOpen className="h-4 w-4 shrink-0 text-muted-foreground" />
          {skill.name}
        </h3>
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
              <DropdownMenuItem onClick={() => onEdit?.(skill)}>
                <Pencil className="h-4 w-4" />
                {tc("edit")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => isPublished ? onUnpublish(skill.id) : onPublish(skill.id)}>
                {isPublished ? <GlobeLock className="h-4 w-4" /> : <Globe className="h-4 w-4" />}
                {isPublished ? tc("unpublish") : tc("publish")}
              </DropdownMenuItem>
              {skill.publish_status === "rejected" && onResubmit && (
                <DropdownMenuItem onClick={() => onResubmit(skill.id)}>
                  <RotateCw className="h-4 w-4" />
                  {to("resubmit")}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => onDelete(skill.id)}>
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
              <DropdownMenuItem variant="destructive" onClick={() => onUninstall(skill.id)}>
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

        {/* Owner visibility badge */}
        {isOwner && isOrgResource && (
          <Badge
            variant="secondary"
            className={cn(
              "text-[10px] px-1.5 py-0 h-5",
              skill.org_id === MARKET_ORG_ID
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                : "bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
            )}
          >
            {skill.org_id === MARKET_ORG_ID ? (
              <><Store className="h-2.5 w-2.5 mr-0.5" />{tc("publishedToMarket")}</>
            ) : (
              <><Globe className="h-2.5 w-2.5 mr-0.5" />{tc("published")}</>
            )}
          </Badge>
        )}

        {/* Publish review status badges */}
        {isOwner && skill.publish_status === "pending_review" && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20"
          >
            <Clock className="h-2.5 w-2.5 mr-0.5" />
            {to("publishStatusPending")}
          </Badge>
        )}
        {isOwner && skill.publish_status === "approved" && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
          >
            <CheckCircle2 className="h-2.5 w-2.5 mr-0.5" />
            {to("publishStatusApproved")}
          </Badge>
        )}
        {isOwner && skill.publish_status === "rejected" && (
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
              {skill.review_note && (
                <TooltipContent>
                  <p>{to("rejectedNote", { note: skill.review_note })}</p>
                </TooltipContent>
              )}
            </Tooltip>
          </TooltipProvider>
        )}
      </div>

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2 mb-3">
        {skill.description || t("noDescription")}
      </p>

      {/* Edit CTA - only for owners */}
      {isOwner && onEdit && (
        <Button
          variant="outline"
          size="sm"
          className="mt-auto w-full gap-1.5 text-xs h-7"
          onClick={() => onEdit(skill)}
        >
          <Pencil className="h-3 w-3" />
          {tc("edit")}
        </Button>
      )}
    </div>
  )
}
