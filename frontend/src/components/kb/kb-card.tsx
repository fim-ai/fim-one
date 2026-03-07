"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"
import { Eye, Pencil, Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { KBResponse } from "@/types/kb"

interface KBCardProps {
  kb: KBResponse
  onEdit: (kb: KBResponse) => void
  onDelete: (id: string) => void
}

export function KBCard({
  kb,
  onEdit,
  onDelete,
}: KBCardProps) {
  const t = useTranslations("kb")
  const tc = useTranslations("common")
  return (
    <div className="flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-ring/40 hover:bg-accent/10">
      {/* Header: name + badges */}
      <div className="flex items-start gap-2 mb-2">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground">
          {kb.name}
        </h3>
        <div className="flex items-center gap-1.5 shrink-0">
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
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5"
          >
            {kb.retrieval_mode}
          </Badge>
        </div>
      </div>

      {/* Stats */}
      <p className="text-xs text-muted-foreground mb-1">
        {t("docCount", { count: kb.document_count })} &middot; {t("chunkCount", { count: kb.total_chunks })}
      </p>

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2 mb-3">
        {kb.description || t("noDescription")}
      </p>

      {/* Action buttons */}
      <div className="flex items-center gap-1 -ml-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-muted-foreground hover:text-foreground"
              asChild
            >
              <Link href={`/kb/${kb.id}`}>
                <Eye className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>{t("view")}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => onEdit(kb)}
              className="text-muted-foreground hover:text-foreground"
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>{tc("edit")}</TooltipContent>
        </Tooltip>
        <div className="flex-1" />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => onDelete(kb.id)}
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>{tc("delete")}</TooltipContent>
        </Tooltip>
      </div>
    </div>
  )
}
