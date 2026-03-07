"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { Trash2, Loader2, Eye, RotateCw } from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { ChunkDrawer } from "@/components/kb/chunk-drawer"
import { kbApi } from "@/lib/api"
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
  const t = useTranslations("kb")
  const tc = useTranslations("common")
  const [selectedDoc, setSelectedDoc] = useState<KBDocumentResponse | null>(
    null,
  )
  const [retryingIds, setRetryingIds] = useState<Set<string>>(new Set())
  const [retriedIds, setRetriedIds] = useState<Set<string>>(new Set())

  const handleRetry = async (doc: KBDocumentResponse) => {
    setRetryingIds((prev) => new Set(prev).add(doc.id))
    try {
      await kbApi.retryDocument(kbId, doc.id)
      // Mark as retried so we show "processing" optimistically until parent re-fetches
      setRetriedIds((prev) => new Set(prev).add(doc.id))
    } catch (err) {
      console.error("Failed to retry document:", err)
    } finally {
      setRetryingIds((prev) => {
        const next = new Set(prev)
        next.delete(doc.id)
        return next
      })
    }
  }

  // Derive effective status: if retried and still showing "failed", override to "processing"
  const getEffectiveStatus = (doc: KBDocumentResponse) => {
    if (retriedIds.has(doc.id) && doc.status === "failed") return "processing"
    return doc.status
  }

  if (documents.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-10">
        {t("noDocuments")}
      </p>
    )
  }

  return (
    <>
      <div className="rounded-lg border border-border overflow-hidden">
        {/* Header row */}
        <div className="grid grid-cols-[1fr_60px_70px_70px_80px] gap-2 px-3 py-2 bg-muted/50 text-[11px] font-medium text-muted-foreground uppercase tracking-wider border-b border-border">
          <span>{t("filename")}</span>
          <span>{tc("type")}</span>
          <span>{t("chunks")}</span>
          <span>{tc("status")}</span>
          <span className="text-right">{tc("actions")}</span>
        </div>

        {/* Document rows */}
        {documents.map((doc) => (
          <div
            key={doc.id}
            className="grid grid-cols-[1fr_60px_70px_70px_80px] gap-2 px-3 py-2 text-sm items-center border-b border-border/50 hover:bg-accent/5 transition-colors"
          >
            {/* Filename */}
            <div className="flex items-center gap-1.5 min-w-0">
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="truncate">
                    {doc.filename}
                  </span>
                </TooltipTrigger>
                <TooltipContent side="bottom" sideOffset={5}>{doc.filename}</TooltipContent>
              </Tooltip>
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
            {(() => {
              const effectiveStatus = getEffectiveStatus(doc)
              if (effectiveStatus === "failed" && doc.error_message) {
                return (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge
                        variant="secondary"
                        className={cn(
                          "text-[10px] px-1.5 py-0 h-5 w-fit gap-1 cursor-help",
                          statusColor(effectiveStatus),
                        )}
                      >
                        {effectiveStatus}
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      {doc.error_message}
                    </TooltipContent>
                  </Tooltip>
                )
              }
              return (
                <Badge
                  variant="secondary"
                  className={cn(
                    "text-[10px] px-1.5 py-0 h-5 w-fit gap-1",
                    statusColor(effectiveStatus),
                  )}
                >
                  {effectiveStatus === "processing" && (
                    <Loader2 className="h-2.5 w-2.5 animate-spin" />
                  )}
                  {effectiveStatus}
                </Badge>
              )
            })()}

            {/* Actions */}
            <div className="flex items-center justify-end gap-0.5">
              {getEffectiveStatus(doc) === "failed" && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => handleRetry(doc)}
                      disabled={retryingIds.has(doc.id)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <RotateCw
                        className={cn(
                          "h-3.5 w-3.5",
                          retryingIds.has(doc.id) && "animate-spin",
                        )}
                      />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" sideOffset={5}>{t("retryProcessing")}</TooltipContent>
                </Tooltip>
              )}
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => setSelectedDoc(doc)}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <Eye className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom" sideOffset={5}>{t("viewChunks")}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => onDeleteDocument(doc.id)}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom" sideOffset={5}>{t("deleteDocument")}</TooltipContent>
              </Tooltip>
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
