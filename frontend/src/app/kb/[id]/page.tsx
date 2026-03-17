"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import Link from "next/link"
import {
  ArrowLeft,
  Loader2,
  Upload,
  FilePlus,
  Search,
  RefreshCw,
  Files,
  FlaskConical,
  Library,
  Sparkles,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { useAuth } from "@/contexts/auth-context"
import { kbApi } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import { DocumentTable } from "@/components/kb/document-table"
import { KBUploadDialog } from "@/components/kb/kb-upload-dialog"
import { MdCreateDialog } from "@/components/kb/md-create-dialog"
import { Pagination } from "@/components/kb/pagination"
import { KBAIPanel } from "@/components/kb/kb-ai-panel"
import type { KBResponse, KBDocumentResponse, KBRetrieveResult } from "@/types/kb"
import { usePageTitle } from "@/hooks/use-page-title"

export default function KBDetailPage() {
  const params = useParams<{ id: string }>()
  const kbId = params.id
  const router = useRouter()
  const { user, isLoading: authLoading } = useAuth()
  const t = useTranslations("kb")
  const tc = useTranslations("common")

  const [kb, setKb] = useState<KBResponse | null>(null)

  usePageTitle(kb?.name)

  const [documents, setDocuments] = useState<KBDocumentResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<"documents" | "search" | "ai">("documents")

  // Document pagination
  const DOC_PAGE_SIZE = 20
  const [docPage, setDocPage] = useState(1)
  const [docTotalPages, setDocTotalPages] = useState(0)
  const [docTotal, setDocTotal] = useState(0)

  // Upload dialog
  const [uploadOpen, setUploadOpen] = useState(false)

  // MD create dialog
  const [mdCreateOpen, setMdCreateOpen] = useState(false)

  // Delete confirmation
  const [pendingDeleteDocId, setPendingDeleteDocId] = useState<string | null>(null)

  // Search
  const [searchQuery, setSearchQuery] = useState("")
  const [searchResults, setSearchResults] = useState<KBRetrieveResult[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)

  // Refresh state
  const [isRefreshing, setIsRefreshing] = useState(false)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const loadKB = useCallback(async () => {
    try {
      const data = await kbApi.get(kbId)
      // Non-owners cannot access KB detail (black box model)
      if (user && data.user_id !== user.id) {
        router.replace("/kb")
        return
      }
      setKb(data)
    } catch (err) {
      console.error("Failed to load KB:", err)
      router.replace("/kb")
    }
  }, [kbId, router, user])

  const loadDocuments = useCallback(async () => {
    try {
      const data = await kbApi.listDocuments(kbId, docPage, DOC_PAGE_SIZE)
      setDocuments(data.items)
      setDocTotalPages(data.pages)
      setDocTotal(data.total)
    } catch (err) {
      console.error("Failed to load documents:", err)
    }
  }, [kbId, docPage])

  const loadAll = useCallback(async () => {
    setIsLoading(true)
    await Promise.all([loadKB(), loadDocuments()])
    setIsLoading(false)
  }, [loadKB, loadDocuments])

  useEffect(() => {
    if (user) loadAll()
  }, [user, loadAll])

  // Auto-poll when any document is processing
  const hasProcessing = documents.some((d) => d.status === "processing")
  useEffect(() => {
    if (!hasProcessing) return
    const id = setInterval(() => {
      loadDocuments()
      loadKB()
    }, 3000)
    return () => clearInterval(id)
  }, [hasProcessing, loadDocuments, loadKB])

  const handleRefresh = async () => {
    setIsRefreshing(true)
    await Promise.all([loadKB(), loadDocuments()])
    setIsRefreshing(false)
  }

  const handleKbChanged = useCallback(async () => {
    await Promise.all([loadKB(), loadDocuments()])
  }, [loadKB, loadDocuments])

  const handleDeleteDocument = (docId: string) => setPendingDeleteDocId(docId)

  const confirmDeleteDocument = async () => {
    if (!pendingDeleteDocId) return
    const docId = pendingDeleteDocId
    setPendingDeleteDocId(null)
    try {
      await kbApi.deleteDocument(kbId, docId)
      setDocuments((prev) => prev.filter((d) => d.id !== docId))
      await loadKB()
    } catch (err) {
      console.error("Failed to delete document:", err)
    }
  }

  const handleUploaded = async () => {
    await Promise.all([loadKB(), loadDocuments()])
  }

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!searchQuery.trim()) return
    setIsSearching(true)
    setHasSearched(true)
    try {
      const results = await kbApi.retrieve(kbId, searchQuery.trim())
      setSearchResults(results)
    } catch (err) {
      console.error("Failed to search:", err)
    } finally {
      setIsSearching(false)
    }
  }

  if (authLoading || !user) return null

  if (isLoading || !kb) {
    return (
      <div className="flex h-full overflow-hidden">
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Header skeleton */}
          <div className="shrink-0 border-b border-border/40 px-6 py-4">
            <div className="flex items-start gap-3">
              <Skeleton className="h-7 w-7 shrink-0 rounded-md mt-1" />
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-5 w-48" />
                <Skeleton className="h-3.5 w-80" />
              </div>
            </div>
          </div>
          {/* Toolbar skeleton */}
          <div className="shrink-0 flex items-center justify-between gap-2 px-6 py-3 border-b border-border/40">
            <div className="flex items-center gap-2">
              <Skeleton className="h-8 w-28 rounded-md" />
              <Skeleton className="h-8 w-24 rounded-md" />
            </div>
            <div className="flex items-center gap-1">
              <Skeleton className="h-7 w-20 rounded-md" />
              <Skeleton className="h-7 w-20 rounded-md" />
              <Skeleton className="h-7 w-20 rounded-md" />
            </div>
          </div>
          {/* Documents table skeleton */}
          <div className="flex-1 overflow-y-auto p-6">
            <div className="rounded-md border divide-y divide-border">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton.TableRow key={i} />
              ))}
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="shrink-0 border-b border-border/40 px-6 py-4">
          <div className="flex items-start gap-3">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  className="mt-1"
                  asChild
                >
                  <Link href="/kb">
                    <ArrowLeft className="h-4 w-4" />
                  </Link>
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right" sideOffset={5}>{t("backToKb")}</TooltipContent>
            </Tooltip>
            <div className="flex-1 min-w-0 flex items-start justify-between gap-4">
              <div className="min-w-0 space-y-0.5">
                <h1 className="text-lg font-semibold text-foreground truncate flex items-center gap-2">
                  <Library className="h-5 w-5 shrink-0" />
                  {kb.name}
                </h1>
                <p className="text-sm text-muted-foreground">
                  {kb.description || t("noDescription")}
                  {" "}&middot;{" "}
                  {t("strategy")}: {kb.chunk_strategy}
                  {" "}&middot;{" "}
                  {t("docAndChunkStats", { docCount: kb.document_count, chunkCount: kb.total_chunks })}
                </p>
              </div>
              <Badge
                variant="secondary"
                className="shrink-0 text-[10px] px-1.5 py-0 h-5 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
              >
                {kb.status}
              </Badge>
            </div>
          </div>
        </div>

        {/* Toolbar */}
        <div className="shrink-0 flex items-center justify-between gap-2 px-6 py-3 border-b border-border/40">
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              onClick={() => setUploadOpen(true)}
              className="gap-1.5"
            >
              <Upload className="h-3.5 w-3.5" />
              {t("addDocuments")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setMdCreateOpen(true)}
              className="gap-1.5"
            >
              <FilePlus className="h-3.5 w-3.5" />
              {t("writeNote")}
            </Button>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={handleRefresh}
                  disabled={isRefreshing}
                  className="gap-1.5 text-muted-foreground"
                >
                  <RefreshCw className={cn("h-3.5 w-3.5", isRefreshing && "animate-spin")} />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom" sideOffset={5}>{t("refreshDocuments")}</TooltipContent>
            </Tooltip>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant={activeTab === "documents" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setActiveTab("documents")}
              className="text-xs h-7 px-2.5 gap-1"
            >
              <Files className="h-3 w-3" />
              {t("documentsTab")}
            </Button>
            <Button
              variant={activeTab === "search" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setActiveTab("search")}
              className="text-xs h-7 px-2.5 gap-1"
            >
              <FlaskConical className="h-3 w-3" />
              {t("retrieveTest")}
            </Button>
            <Button
              variant={activeTab === "ai" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setActiveTab("ai")}
              className="text-xs h-7 px-2.5 gap-1"
            >
              <Sparkles className="h-3 w-3" />
              {t("aiAssistant")}
            </Button>
          </div>
        </div>

        {/* Content */}
        <div className={cn(
          "flex-1 overflow-hidden",
          activeTab !== "ai" && "overflow-y-auto p-6"
        )}>
          {activeTab === "documents" && (
            <div className="space-y-4">
              <DocumentTable
                kbId={kbId}
                documents={documents}
                onDeleteDocument={handleDeleteDocument}
                chunkSize={kb.chunk_size}
              />
              {docTotalPages > 1 && (
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {t("documentsTotal", { count: docTotal })}
                  </span>
                  <Pagination
                    page={docPage}
                    totalPages={docTotalPages}
                    onPageChange={setDocPage}
                  />
                </div>
              )}
            </div>
          )}

          {activeTab === "search" && (
            <div className="space-y-4">
              <form onSubmit={handleSearch} className="flex gap-2">
                <Input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t("searchKbPlaceholder")}
                  className="flex-1"
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

              {isSearching ? (
                <div className="flex justify-center py-10">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              ) : searchResults.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-10">
                  {hasSearched
                    ? t("noSearchResults")
                    : t("searchPrompt")}
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
                          {t("score", { score: result.score.toFixed(3) })}
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
            </div>
          )}

          {activeTab === "ai" && (
            <KBAIPanel kbId={kbId} onKbChanged={handleKbChanged} />
          )}
        </div>
      </div>

      {/* Upload Dialog */}
      <KBUploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        kb={kb}
        onUploaded={handleUploaded}
      />

      {/* MD Create Dialog */}
      <MdCreateDialog
        open={mdCreateOpen}
        onOpenChange={setMdCreateOpen}
        kbId={kbId}
        onCreated={handleUploaded}
      />

      {/* Delete Document Confirmation */}
      <Dialog open={pendingDeleteDocId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteDocId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("deleteDocumentTitle")}</DialogTitle>
            <DialogDescription>
              {t("deleteDocumentDescription", { filename: documents.find((d) => d.id === pendingDeleteDocId)?.filename ?? "" })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteDocId(null)}>{tc("cancel")}</Button>
            <Button variant="destructive" className="px-6" onClick={confirmDeleteDocument}>{tc("delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
