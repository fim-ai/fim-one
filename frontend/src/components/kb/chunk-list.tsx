"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { Loader2, Pencil, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { kbApi } from "@/lib/api"
import { ChunkEditor } from "@/components/kb/chunk-editor"
import { Pagination } from "@/components/kb/pagination"
import type { ChunkResponse } from "@/types/kb"

interface ChunkListProps {
  kbId: string
  docId: string
}

const PAGE_SIZE = 20

export function ChunkList({ kbId, docId }: ChunkListProps) {
  const t = useTranslations("kb")
  const [chunks, setChunks] = useState<ChunkResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(0)
  const [total, setTotal] = useState(0)
  const [editingChunkId, setEditingChunkId] = useState<string | null>(null)

  const loadChunks = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await kbApi.listChunks(kbId, docId, page, PAGE_SIZE)
      setChunks(data.items)
      setTotalPages(data.pages)
      setTotal(data.total)
    } catch (err) {
      console.error("Failed to load chunks:", err)
    } finally {
      setIsLoading(false)
    }
  }, [kbId, docId, page])

  useEffect(() => {
    loadChunks()
  }, [loadChunks])

  const handleUpdateChunk = async (chunkId: string, text: string) => {
    await kbApi.updateChunk(kbId, chunkId, { text })
    setChunks((prev) =>
      prev.map((c) => (c.id === chunkId ? { ...c, text } : c)),
    )
    setEditingChunkId(null)
  }

  const handleDeleteChunk = async (chunkId: string) => {
    try {
      await kbApi.deleteChunk(kbId, chunkId)
      setChunks((prev) => prev.filter((c) => c.id !== chunkId))
      setTotal((t) => Math.max(0, t - 1))
    } catch (err) {
      console.error("Failed to delete chunk:", err)
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-6">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (chunks.length === 0) {
    return (
      <p className="text-xs text-muted-foreground text-center py-4">
        {t("noChunksFound")}
      </p>
    )
  }

  return (
    <div className="space-y-1.5">
      {chunks.map((chunk) => (
        <div
          key={chunk.id}
          className="rounded border border-border/60 bg-muted/30 px-3 py-2"
        >
          {editingChunkId === chunk.id ? (
            <ChunkEditor
              content={chunk.text}
              onSave={(text) => handleUpdateChunk(chunk.id, text)}
              onCancel={() => setEditingChunkId(null)}
            />
          ) : (
            <div className="flex items-start gap-2">
              <span className="shrink-0 text-[10px] font-mono text-muted-foreground mt-0.5">
                #{chunk.chunk_index}
              </span>
              <p className="flex-1 min-w-0 text-xs text-foreground whitespace-pre-wrap break-all line-clamp-3">
                {chunk.text}
              </p>
              <div className="flex items-center gap-0.5 shrink-0">
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
          )}
        </div>
      ))}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-2">
          <span className="text-[10px] text-muted-foreground">
            {t("chunksTotal", { count: total })}
          </span>
          <Pagination
            page={page}
            totalPages={totalPages}
            onPageChange={setPage}
          />
        </div>
      )}
    </div>
  )
}
