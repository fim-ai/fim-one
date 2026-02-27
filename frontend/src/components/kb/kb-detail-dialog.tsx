"use client"

import { useState, useEffect, useCallback } from "react"
import { Loader2, Search, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { kbApi } from "@/lib/api"
import type { KBResponse, KBDocumentResponse, KBRetrieveResult } from "@/types/kb"

interface KBDetailDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  kb: KBResponse | null
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function KBDetailDialog({
  open,
  onOpenChange,
  kb,
}: KBDetailDialogProps) {
  const [documents, setDocuments] = useState<KBDocumentResponse[]>([])
  const [isLoadingDocs, setIsLoadingDocs] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const [searchResults, setSearchResults] = useState<KBRetrieveResult[]>([])
  const [isSearching, setIsSearching] = useState(false)

  const loadDocuments = useCallback(async () => {
    if (!kb) return
    setIsLoadingDocs(true)
    try {
      const docs = await kbApi.listDocuments(kb.id)
      setDocuments(docs)
    } catch (err) {
      console.error("Failed to load documents:", err)
    } finally {
      setIsLoadingDocs(false)
    }
  }, [kb])

  useEffect(() => {
    if (open && kb) {
      loadDocuments()
      setSearchQuery("")
      setSearchResults([])
    }
  }, [open, kb, loadDocuments])

  const handleDeleteDocument = async (docId: string) => {
    if (!kb) return
    try {
      await kbApi.deleteDocument(kb.id, docId)
      setDocuments((prev) => prev.filter((d) => d.id !== docId))
    } catch (err) {
      console.error("Failed to delete document:", err)
    }
  }

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!kb || !searchQuery.trim()) return
    setIsSearching(true)
    try {
      const results = await kbApi.retrieve(kb.id, searchQuery.trim())
      setSearchResults(results)
    } catch (err) {
      console.error("Failed to search:", err)
    } finally {
      setIsSearching(false)
    }
  }

  if (!kb) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>{kb.name}</DialogTitle>
        </DialogHeader>

        <Tabs defaultValue="documents" className="flex-1 min-h-0">
          <TabsList>
            <TabsTrigger value="documents">Documents</TabsTrigger>
            <TabsTrigger value="search">Search</TabsTrigger>
          </TabsList>

          <TabsContent value="documents" className="overflow-y-auto mt-4">
            {isLoadingDocs ? (
              <div className="flex items-center justify-center py-10">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : documents.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-10">
                No documents yet. Upload files to get started.
              </p>
            ) : (
              <div className="space-y-2">
                {documents.map((doc) => (
                  <div
                    key={doc.id}
                    className="flex items-center gap-3 rounded-md border border-border px-3 py-2 text-sm"
                  >
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">{doc.filename}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatFileSize(doc.file_size)} &middot; {doc.chunk_count} chunks
                      </p>
                    </div>
                    <Badge
                      variant="secondary"
                      className="text-[10px] px-1.5 py-0 h-5 shrink-0"
                    >
                      {doc.status}
                    </Badge>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => handleDeleteDocument(doc.id)}
                      className="text-muted-foreground hover:text-destructive shrink-0"
                      title="Delete document"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </TabsContent>

          <TabsContent value="search" className="overflow-y-auto mt-4">
            <form onSubmit={handleSearch} className="flex gap-2 mb-4">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search knowledge base..."
                className="flex h-9 flex-1 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              />
              <Button
                type="submit"
                size="sm"
                disabled={isSearching || !searchQuery.trim()}
              >
                {isSearching ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Search className="h-4 w-4" />
                )}
              </Button>
            </form>

            {searchResults.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-10">
                {searchQuery.trim()
                  ? "No results found."
                  : "Enter a query to search this knowledge base."}
              </p>
            ) : (
              <div className="space-y-3">
                {searchResults.map((result, i) => (
                  <div
                    key={i}
                    className="rounded-md border border-border p-3"
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <Badge
                        variant="secondary"
                        className="text-[10px] px-1.5 py-0 h-5"
                      >
                        Score: {result.score.toFixed(3)}
                      </Badge>
                      {"source" in result.metadata && result.metadata.source != null && (
                        <span className="text-xs text-muted-foreground truncate max-w-[200px]">
                          {String(result.metadata.source)}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-foreground whitespace-pre-wrap line-clamp-6">
                      {result.content}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
