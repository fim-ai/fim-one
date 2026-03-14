"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"
import { Building2, CheckCircle2, Clock, Download, Eye, MoreHorizontal, PackageMinus, Pencil, ShoppingBag, Trash2, XCircle } from "lucide-react"
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
import type { KBResponse } from "@/types/kb"

interface KBCardProps {
  kb: KBResponse
  currentUserId?: string
  onEdit: (kb: KBResponse) => void
  onDelete: (id: string) => void
  onUninstall?: (id: string) => void
}

export function KBCard({
  kb,
  currentUserId,
  onEdit,
  onDelete,
  onUninstall,
}: KBCardProps) {
  const t = useTranslations("kb")
  const tc = useTranslations("common")
  const to = useTranslations("organizations")
  const isOwner = !currentUserId || kb.user_id === currentUserId
  const isOrgResource = kb.visibility === "org" || kb.visibility === "global"
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const source = (kb as any).source as string | undefined
  const isInstalled = source === "installed"
  const isOrgShared = source === "org" || (!source && !isOwner)
  return (
    <div className="group flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-ring/40 hover:bg-accent/10">
      {/* Header: name + hover menu */}
      <div className="flex items-center gap-2 mb-1.5">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground">
          {kb.name}
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
              <DropdownMenuItem asChild>
                <Link href={`/kb/${kb.id}`}>
                  <Eye className="h-4 w-4" />
                  {t("view")}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => onEdit(kb)}>
                <Pencil className="h-4 w-4" />
                {tc("edit")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => onDelete(kb.id)}>
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
              <DropdownMenuItem asChild>
                <Link href={`/kb/${kb.id}`}>
                  <Eye className="h-4 w-4" />
                  {t("view")}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => onUninstall(kb.id)}>
                <PackageMinus className="h-4 w-4" />
                {tc("uninstall")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
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
                <Link href={`/kb/${kb.id}`}>
                  <Eye className="h-4 w-4" />
                  {t("view")}
                </Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {/* Installed / Shared badge (non-owner) */}
      {isInstalled && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-violet-500/10 text-violet-500 dark:text-violet-400 border-violet-500/20"
          >
            <Download className="h-2.5 w-2.5 mr-0.5" />
            {tc("installed")}
          </Badge>
        </div>
      )}
      {!isInstalled && isOrgShared && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
          >
            <Building2 className="h-2.5 w-2.5 mr-0.5" />
            {tc("shared")}
          </Badge>
        </div>
      )}

      {/* Owner visibility badge */}
      {isOwner && isOrgResource && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className={cn(
              "text-[10px] px-1.5 py-0 h-5",
              kb.org_id === MARKET_ORG_ID
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                : "bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
            )}
          >
            {kb.org_id === MARKET_ORG_ID ? (
              <><ShoppingBag className="h-2.5 w-2.5 mr-0.5" />{tc("published")}</>
            ) : (
              <><Building2 className="h-2.5 w-2.5 mr-0.5" />{tc("published")}</>
            )}
          </Badge>
        </div>
      )}

      {/* Publish review status badges — owner only */}
      {isOwner && (kb.publish_status === "pending_review" || kb.publish_status === "rejected") && (
        <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
          {kb.publish_status === "pending_review" && (
            <Badge
              variant="secondary"
              className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20"
            >
              <Clock className="h-2.5 w-2.5 mr-0.5" />
              {to("publishStatusPending")}
            </Badge>
          )}
          {kb.publish_status === "rejected" && (
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
                {kb.review_note && (
                  <TooltipContent>
                    <p>{to("rejectedNote", { note: kb.review_note })}</p>
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
      )}

      {/* Badges */}
      <div className="flex items-center gap-1.5 mb-2">
        <Badge
          variant="secondary"
          className={cn(
            "text-[10px] px-1.5 py-0 h-5",
            kb.status === "active"
              ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
              : "opacity-60"
          )}
        >
          {kb.status}
        </Badge>
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5">
          {kb.retrieval_mode}
        </Badge>
      </div>

      {/* Stats */}
      <p className="text-xs text-muted-foreground mb-1">
        {t("docCount", { count: kb.document_count })} &middot; {t("chunkCount", { count: kb.total_chunks })}
      </p>

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2">
        {kb.description || t("noDescription")}
      </p>
    </div>
  )
}
