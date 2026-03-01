"use client"

import { useState } from "react"
import { Trash2, Loader2, Eye } from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ChunkDrawer } from "@/components/kb/chunk-drawer"
import type { KBDocumentResponse } from "@/types/kb"

interface DocumentTableProps {
  kbId: string
  documents: KBDocumentResponse[]
  onDeleteDocument: (docId: string) => void
  chunkSize?: number
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function statusColor(status: string): string {
  switch (status) {
    case "ready":
      return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
    case "processing":
      return "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/20"
    case "failed":
      return "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/20"
    default:
      return ""
  }
}

export function DocumentTable({
  kbId,
  documents,
  onDeleteDocument,
  chunkSize,
}: DocumentTableProps) {
  const [selectedDoc, setSelectedDoc] = useState<KBDocumentResponse | null>(
    null,
  )

  if (documents.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-10">
        No documents yet. Upload files or create a markdown document to get started.
      </p>
    )
  }

  return (
    <>
      <div className="rounded-lg border border-border overflow-hidden">
        {/* Header row */}
        <div className="grid grid-cols-[1fr_60px_70px_70px_80px] gap-2 px-3 py-2 bg-muted/50 text-[11px] font-medium text-muted-foreground uppercase tracking-wider border-b border-border">
          <span>Filename</span>
          <span>Type</span>
          <span>Chunks</span>
          <span>Status</span>
          <span className="text-right">Actions</span>
        </div>

        {/* Document rows */}
        {documents.map((doc) => (
          <div
            key={doc.id}
            className="grid grid-cols-[1fr_60px_70px_70px_80px] gap-2 px-3 py-2 text-sm items-center border-b border-border/50 hover:bg-accent/5 transition-colors"
          >
            {/* Filename */}
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="truncate" title={doc.filename}>
                {doc.filename}
              </span>
              <span className="text-[10px] text-muted-foreground shrink-0">
                {formatFileSize(doc.file_size)}
              </span>
            </div>

            {/* Type */}
            <span className="text-xs text-muted-foreground">
              {doc.file_type}
            </span>

            {/* Chunks */}
            <span className="text-xs tabular-nums">{doc.chunk_count}</span>

            {/* Status */}
            <Badge
              variant="secondary"
              className={cn(
                "text-[10px] px-1.5 py-0 h-5 w-fit gap-1",
                statusColor(doc.status),
              )}
            >
              {doc.status === "processing" && (
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
              )}
              {doc.status}
            </Badge>

            {/* Actions */}
            <div className="flex items-center justify-end gap-0.5">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => setSelectedDoc(doc)}
                className="text-muted-foreground hover:text-foreground"
                title="View chunks"
              >
                <Eye className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => onDeleteDocument(doc.id)}
                className="text-muted-foreground hover:text-destructive"
                title="Delete document"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        ))}
      </div>

      {/* Chunk Drawer */}
      {selectedDoc && (
        <ChunkDrawer
          open={selectedDoc !== null}
          onOpenChange={(open) => {
            if (!open) setSelectedDoc(null)
          }}
          kbId={kbId}
          document={selectedDoc}
          chunkSize={chunkSize}
        />
      )}
    </>
  )
}
