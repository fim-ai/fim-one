"use client"

import { useState, useEffect } from "react"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import type { KBCreate, KBResponse } from "@/types/kb"

interface KBFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  kb: KBResponse | null
  onSubmit: (data: KBCreate) => Promise<void>
  isSubmitting: boolean
}

export function KBFormDialog({
  open,
  onOpenChange,
  kb,
  onSubmit,
  isSubmitting,
}: KBFormDialogProps) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [chunkStrategy, setChunkStrategy] = useState("recursive")
  const [chunkSize, setChunkSize] = useState(1000)
  const [chunkOverlap, setChunkOverlap] = useState(200)
  const [retrievalMode, setRetrievalMode] = useState("hybrid")

  // Pre-fill when editing or reset when creating
  useEffect(() => {
    if (!open) return
    if (kb) {
      setName(kb.name)
      setDescription(kb.description || "")
      setChunkStrategy(kb.chunk_strategy)
      setChunkSize(kb.chunk_size)
      setChunkOverlap(kb.chunk_overlap)
      setRetrievalMode(kb.retrieval_mode)
    } else {
      setName("")
      setDescription("")
      setChunkStrategy("recursive")
      setChunkSize(1000)
      setChunkOverlap(200)
      setRetrievalMode("hybrid")
    }
  }, [open, kb])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) return

    const data: KBCreate = {
      name: trimmedName,
      ...(description.trim() && { description: description.trim() }),
      chunk_strategy: chunkStrategy,
      chunk_size: chunkSize,
      chunk_overlap: chunkOverlap,
      retrieval_mode: retrievalMode,
    }

    await onSubmit(data)
  }

  const isEditing = kb !== null
  const inputClass =
    "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEditing ? "Edit Knowledge Base" : "Create Knowledge Base"}
          </DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div className="space-y-1.5">
            <label htmlFor="kb-name" className="text-sm font-medium">
              Name <span className="text-destructive">*</span>
            </label>
            <input
              id="kb-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Knowledge Base"
              required
              className={inputClass}
            />
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <label htmlFor="kb-description" className="text-sm font-medium">
              Description
            </label>
            <textarea
              id="kb-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="A brief description of this knowledge base..."
              rows={2}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none"
            />
          </div>

          {/* Chunk Strategy */}
          <div className="space-y-1.5">
            <label htmlFor="kb-chunk-strategy" className="text-sm font-medium">
              Chunk Strategy
            </label>
            <select
              id="kb-chunk-strategy"
              value={chunkStrategy}
              onChange={(e) => setChunkStrategy(e.target.value)}
              className={inputClass}
            >
              <option value="recursive">Recursive</option>
              <option value="markdown">Markdown</option>
              <option value="fixed">Fixed</option>
              <option value="semantic">Semantic</option>
            </select>
            <p className="text-xs text-muted-foreground">
              {chunkStrategy === "recursive" && "Split by paragraphs, sentences, then words. Best for general use."}
              {chunkStrategy === "markdown" && "Split by # headers first, then recursively within sections. Best for .md files."}
              {chunkStrategy === "fixed" && "Fixed character-length chunks. Best for unstructured text or logs."}
              {chunkStrategy === "semantic" && "Split by semantic similarity using embeddings. Best for long documents."}
            </p>
          </div>

          {/* Chunk Size & Overlap */}
          {chunkStrategy === "semantic" && (
            <p className="text-xs text-muted-foreground">
              Semantic chunking primarily splits by meaning. Size/overlap are used as fallback limits.
            </p>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label htmlFor="kb-chunk-size" className="text-sm font-medium">
                Chunk Size
              </label>
              <input
                id="kb-chunk-size"
                type="number"
                min={100}
                max={10000}
                value={chunkSize}
                onChange={(e) => setChunkSize(Number(e.target.value))}
                className={inputClass}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="kb-chunk-overlap" className="text-sm font-medium">
                Chunk Overlap
              </label>
              <input
                id="kb-chunk-overlap"
                type="number"
                min={0}
                max={5000}
                value={chunkOverlap}
                onChange={(e) => setChunkOverlap(Number(e.target.value))}
                className={inputClass}
              />
            </div>
          </div>

          {/* Retrieval Mode */}
          <div className="space-y-1.5">
            <label htmlFor="kb-retrieval-mode" className="text-sm font-medium">
              Retrieval Mode
            </label>
            <select
              id="kb-retrieval-mode"
              value={retrievalMode}
              onChange={(e) => setRetrievalMode(e.target.value)}
              className={inputClass}
            >
              <option value="hybrid">Hybrid</option>
              <option value="dense">Dense</option>
              <option value="fts">Full-Text Search</option>
            </select>
            <p className="text-xs text-muted-foreground">
              {retrievalMode === "hybrid" && "Vector + full-text search with RRF fusion + reranking. Best quality."}
              {retrievalMode === "dense" && "Pure vector similarity search. Good for semantic matching."}
              {retrievalMode === "fts" && "Pure keyword search. Good for exact terms, codes, and names."}
            </p>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting || !name.trim()}>
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              {isEditing ? "Save Changes" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
