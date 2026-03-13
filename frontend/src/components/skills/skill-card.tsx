"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"
import {
  BookOpen,
  Globe,
  GlobeLock,
  MoreHorizontal,
  Pencil,
  RotateCw,
  Trash2,
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
import type { SkillResponse } from "@/types/skill"

interface SkillCardProps {
  skill: SkillResponse
  currentUserId?: string
  onDelete: (id: string) => void
  onPublish: (id: string) => void
  onUnpublish: (id: string) => void
  onResubmit?: (id: string) => void
}

export function SkillCard({
  skill,
  currentUserId,
  onDelete,
  onPublish,
  onUnpublish,
  onResubmit,
}: SkillCardProps) {
  const t = useTranslations("skills")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const isPublished = skill.visibility !== "personal"
  const isOwner = !currentUserId || skill.user_id === currentUserId
  const isActive = skill.is_active

  return (
    <div className="group flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-ring/40 hover:bg-accent/10">
      {/* Header: icon + name + dropdown menu */}
      <div className="flex items-center gap-2 mb-1.5">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground flex items-center gap-1.5">
          <BookOpen className="h-4 w-4 shrink-0 text-muted-foreground" />
          {skill.name}
        </h3>
        {isOwner && (
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
                <Link href={`/skills/${skill.id}`}>
                  <Pencil className="h-4 w-4" />
                  {tc("edit")}
                </Link>
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
        )}
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

        {/* Publish review status badges */}
        {skill.publish_status === "pending_review" && (
          <Badge
            variant="outline"
            className="text-[10px] px-1.5 py-0 h-5 border-amber-400 text-amber-600 dark:text-amber-400"
          >
            {to("publishStatusPending")}
          </Badge>
        )}
        {skill.publish_status === "approved" && (
          <Badge
            variant="outline"
            className="text-[10px] px-1.5 py-0 h-5 border-emerald-400 text-emerald-600 dark:text-emerald-400"
          >
            {to("publishStatusApproved")}
          </Badge>
        )}
        {skill.publish_status === "rejected" && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant="outline"
                  className="text-[10px] px-1.5 py-0 h-5 border-destructive text-destructive cursor-default"
                >
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

      {/* Edit CTA */}
      <Button
        variant="outline"
        size="sm"
        className="mt-auto w-full gap-1.5 text-xs h-7"
        asChild
      >
        <Link href={`/skills/${skill.id}`}>
          <Pencil className="h-3 w-3" />
          {tc("edit")}
        </Link>
      </Button>
    </div>
  )
}
