"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Loader2, Pencil, Trash2, FileText, Search, X } from "lucide-react"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { kbApi } from "@/lib/api"
import { ChunkEditor } from "@/components/kb/chunk-editor"
import type { KBDocumentResponse, ChunkResponse } from "@/types/kb"

interface ChunkDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  kbId: string
  document: KBDocumentResponse
  chunkSize?: number
}

const PAGE_SIZE = 20

/** Split text by query (case-insensitive) and wrap matches in <mark>. */
function highlightMatches(text: string, query: string) {
  if (!query) return text
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  const parts = text.split(new RegExp(`(${escaped})`, "gi"))
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase() ? (
      <mark key={i} className="bg-amber-500/30 text-amber-200 rounded-sm px-0.5">
        {part}
      </mark>
    ) : (
      part
    ),
  )
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

export function ChunkDrawer({
  open,
  onOpenChange,
  kbId,
  document,
  chunkSize,
}: ChunkDrawerProps) {
  const [chunks, setChunks] = useState<ChunkResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(0)
  const [total, setTotal] = useState(0)
  const [editingChunkId, setEditingChunkId] = useState<string | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchInput, setSearchInput] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const searchInputRef = useRef<HTMLInputElement>(null)

  const t = useTranslations("kb")

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(searchInput), 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  // Focus input when search opens
  useEffect(() => {
    if (searchOpen) {
      // Small delay to let the element render/expand
      requestAnimationFrame(() => searchInputRef.current?.focus())
    }
  }, [searchOpen])

  // Reset page when search query changes
  useEffect(() => {
    setPage(1)
  }, [searchQuery])

  const loadChunks = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await kbApi.listChunks(kbId, document.id, page, PAGE_SIZE, searchQuery)
      setChunks(data.items)
      setTotalPages(data.pages)
      setTotal(data.total)
    } catch (err) {
      console.error("Failed to load chunks:", err)
    } finally {
      setIsLoading(false)
    }
  }, [kbId, document.id, page, searchQuery])

  // Load chunks when the drawer opens or page changes
  useEffect(() => {
    if (open) {
      loadChunks()
    }
  }, [open, loadChunks])

  // Reset state when document changes
  useEffect(() => {
    setPage(1)
    setEditingChunkId(null)
    setSearchOpen(false)
    setSearchInput("")
    setSearchQuery("")
  }, [document.id])

  const handleUpdateChunk = async (chunkId: string, text: string) => {
    try {
      await kbApi.updateChunk(kbId, chunkId, { text })
      setChunks((prev) =>
        prev.map((c) => (c.id === chunkId ? { ...c, text } : c)),
      )
      setEditingChunkId(null)
      toast.success(t("chunkUpdated"))
    } catch {
      toast.error(t("failedToUpdateChunk"))
    }
  }

  const handleDeleteChunk = async (chunkId: string) => {
    try {
      await kbApi.deleteChunk(kbId, chunkId)
      setChunks((prev) => prev.filter((c) => c.id !== chunkId))
      setTotal((t) => Math.max(0, t - 1))
      toast.success(t("chunkDeleted"))
    } catch {
      toast.error(t("failedToDeleteChunk"))
    }
  }

  const goToPage = (p: number) => {
    setPage(p)
    setEditingChunkId(null)
  }

  // Build page numbers array for pagination
  const buildPageNumbers = (): (number | "ellipsis")[] => {
    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, i) => i + 1)
    }
    const pages: (number | "ellipsis")[] = [1]
    if (page > 3) pages.push("ellipsis")
    const start = Math.max(2, page - 1)
    const end = Math.min(totalPages - 1, page + 1)
    for (let i = start; i <= end; i++) {
      pages.push(i)
    }
    if (page < totalPages - 2) pages.push("ellipsis")
    pages.push(totalPages)
    return pages
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="sm:max-w-2xl w-full flex flex-col p-0 gap-0"
      >
        {/* Header */}
        <div className="shrink-0 px-6 pt-6 pb-4 border-b border-border/40">
          <SheetHeader className="gap-1">
            <SheetTitle className="flex items-center gap-2 text-base">
              <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
              <span className="truncate">{document.filename}</span>
            </SheetTitle>
            <SheetDescription asChild>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge
                    variant="secondary"
                    className="text-[10px] px-1.5 py-0 h-5"
                  >
                    {document.file_type}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {t("chunkCount", { count: total > 0 ? total : document.chunk_count })}
                  </span>
                  <Badge
                    variant="secondary"
                    className={cn(
                      "text-[10px] px-1.5 py-0 h-5",
                      statusColor(document.status),
                    )}
                  >
                    {document.status}
                  </Badge>
                </div>
                {!searchOpen && (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => setSearchOpen(true)}
                    className="text-muted-foreground hover:text-foreground"
                    title={t("searchChunks")}
                  >
                    <Search className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </SheetDescription>
          </SheetHeader>
          {searchOpen && (
            <div className="relative mt-3">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
              <Input
                ref={searchInputRef}
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setSearchOpen(false)
                    setSearchInput("")
                    setSearchQuery("")
                  }
                }}
                placeholder={t("searchChunksPlaceholder")}
                className="h-8 pl-8 pr-8 text-xs bg-background/50"
              />
              <button
                onClick={() => { setSearchOpen(false); setSearchInput(""); setSearchQuery("") }}
                className="absolute right-2 top-1/2 -translate-y-1/2"
              >
                <X className="h-3.5 w-3.5 text-muted-foreground hover:text-foreground" />
              </button>
            </div>
          )}
        </div>

        {/* Scrollable chunk list */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : chunks.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-16">
              {t("noChunksFound")}
            </p>
          ) : (
            <div className="space-y-2">
              {chunks.map((chunk) => (
                <div
                  key={chunk.id}
                  className="rounded-lg border border-border/60 bg-muted/30 px-4 py-3"
                >
                  {editingChunkId === chunk.id ? (
                    <ChunkEditor
                      content={chunk.text}
                      maxLength={chunkSize}
                      onSave={(text) => handleUpdateChunk(chunk.id, text)}
                      onCancel={() => setEditingChunkId(null)}
                    />
                  ) : (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] font-mono text-muted-foreground bg-muted/60 px-1.5 py-0.5 rounded">
                          #{chunk.chunk_index}
                        </span>
                        <div className="flex items-center gap-0.5">
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            onClick={() => setEditingChunkId(chunk.id)}
                            className="text-muted-foreground hover:text-foreground"
                            title={t("editChunk")}
                          >
                            <Pencil className="h-3 w-3" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            onClick={() => handleDeleteChunk(chunk.id)}
                            className="text-muted-foreground hover:text-destructive"
                            title={t("deleteChunk")}
                          >
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        </div>
                      </div>
                      <p className="text-sm text-foreground whitespace-pre-wrap break-all leading-relaxed">
                        {highlightMatches(chunk.text, searchQuery)}
                      </p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer with page-number pagination */}
        {totalPages > 1 && (
          <div className="shrink-0 border-t border-border/40 px-6 py-3 flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              {t("chunksTotal", { count: total })}
            </span>
            <div className="flex items-center gap-1">
              {buildPageNumbers().map((p, idx) =>
                p === "ellipsis" ? (
                  <span
                    key={`ellipsis-${idx}`}
                    className="text-xs text-muted-foreground px-1"
                  >
                    ...
                  </span>
                ) : (
                  <Button
                    key={p}
                    variant={page === p ? "secondary" : "ghost"}
                    size="sm"
                    onClick={() => goToPage(p)}
                    className={cn(
                      "h-7 w-7 p-0 text-xs",
                      page === p && "font-semibold",
                    )}
                  >
                    {p}
                  </Button>
                ),
              )}
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
