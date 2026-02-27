"use client"

import { Eye, Upload, Pencil, Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { KBResponse } from "@/types/kb"

interface KBCardProps {
  kb: KBResponse
  onView: (kb: KBResponse) => void
  onUpload: (kb: KBResponse) => void
  onEdit: (kb: KBResponse) => void
  onDelete: (id: string) => void
}

export function KBCard({
  kb,
  onView,
  onUpload,
  onEdit,
  onDelete,
}: KBCardProps) {
  return (
    <div className="flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-border/80 hover:bg-accent/5">
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
        {kb.document_count} docs &middot; {kb.total_chunks} chunks
      </p>

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2 mb-3">
        {kb.description || "No description"}
      </p>

      {/* Action buttons */}
      <div className="flex items-center gap-1 -ml-1">
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onView(kb)}
          className="text-muted-foreground hover:text-foreground"
          title="View"
        >
          <Eye className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onUpload(kb)}
          className="text-muted-foreground hover:text-foreground"
          title="Upload Document"
        >
          <Upload className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onEdit(kb)}
          className="text-muted-foreground hover:text-foreground"
          title="Edit"
        >
          <Pencil className="h-3.5 w-3.5" />
        </Button>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onDelete(kb.id)}
          className="text-muted-foreground hover:text-destructive"
          title="Delete"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  )
}
