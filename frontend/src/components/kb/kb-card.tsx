"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"
import { Building2, Eye, MoreHorizontal, PackageMinus, Pencil, ShoppingBag, Trash2 } from "lucide-react"
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
  const isOwner = !currentUserId || kb.user_id === currentUserId
  const isOrgResource = kb.visibility === "org" || kb.visibility === "global"
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const source = (kb as any).source as string | undefined
  const isFromMarket = source === "market"
  const isFromOrg = source === "org"
  const isSubscribed = isFromMarket || isFromOrg
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
        ) : !isOwner && isSubscribed && onUninstall ? (
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
              <DropdownMenuItem variant="destructive" onClick={() => onUninstall(kb.id)}>
                <PackageMinus className="h-4 w-4" />
                {tc("uninstall")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>

      {/* Subscriber badge — Market */}
      {isFromMarket && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
          >
            <ShoppingBag className="h-2.5 w-2.5 mr-0.5" />
            {tc("subscribedMarket")}
          </Badge>
        </div>
      )}
      {/* Subscriber badge — Organization */}
      {isFromOrg && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
          >
            <Building2 className="h-2.5 w-2.5 mr-0.5" />
            {tc("subscribedOrg")}
          </Badge>
        </div>
      )}

      {/* Owner visibility badge — Market */}
      {isOwner && isOrgResource && kb.org_id === MARKET_ORG_ID && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
          >
            <ShoppingBag className="h-2.5 w-2.5 mr-0.5" />
            {tc("publishedMarket")}
          </Badge>
        </div>
      )}

      {/* Owner visibility badge — Organization */}
      {isOwner && isOrgResource && kb.org_id && kb.org_id !== MARKET_ORG_ID && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
          >
            <Building2 className="h-2.5 w-2.5 mr-0.5" />
            {tc("publishedOrg")}
          </Badge>
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
