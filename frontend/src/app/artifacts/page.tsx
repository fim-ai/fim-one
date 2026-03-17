"use client"

import { useState, useEffect, useCallback, Suspense } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useTranslations } from "next-intl"
import hljs from "highlight.js"
import { MarkdownContent } from "@/lib/markdown"
import {
  Layers,
  Loader2,
  File,
  FileCode,
  Globe,
  MessageSquare,
  Download,
  X,
  ChevronLeft,
  ChevronRight,
  MoreHorizontal,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"
import { apiFetch } from "@/lib/api"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

// ---------- types ----------

interface ArtifactItem {
  id: string
  name: string
  mime_type: string
  size: number
  url: string
  conversation_id: string
  conversation_title: string
  created_at: string
}

type FilterType = "all" | "images" | "html" | "code" | "files"

// ---------- helpers ----------

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatRelativeTime(
  dateStr: string,
  t: (key: string, values?: Record<string, number>) => string,
): string {
  const now = Date.now()
  const d = new Date(dateStr).getTime()
  const diff = now - d
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return t("justNow")
  if (mins < 60) return t("minutesAgo", { minutes: mins })
  const hours = Math.floor(mins / 60)
  if (hours < 24) return t("hoursAgo", { hours })
  const days = Math.floor(hours / 24)
  if (days < 30) return t("daysAgo", { days })
  const months = Math.floor(days / 30)
  return t("monthsAgo", { months })
}

function getFilter(mime: string): FilterType {
  if (mime.startsWith("image/")) return "images"
  if (mime === "text/html") return "html"
  if (mime.startsWith("text/") || mime === "application/json") return "code"
  return "files"
}

async function fetchArtifactBlob(url: string): Promise<string> {
  const token = typeof window !== "undefined" ? localStorage.getItem(ACCESS_TOKEN_KEY) : null
  const headers: Record<string, string> = {}
  if (token) headers["Authorization"] = `Bearer ${token}`
  const res = await fetch(`${getApiBaseUrl()}${url}`, { headers })
  if (!res.ok) throw new Error(`Failed to fetch: ${res.status}`)
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

async function fetchArtifactText(url: string): Promise<string> {
  const token = typeof window !== "undefined" ? localStorage.getItem(ACCESS_TOKEN_KEY) : null
  const headers: Record<string, string> = {}
  if (token) headers["Authorization"] = `Bearer ${token}`
  const res = await fetch(`${getApiBaseUrl()}${url}`, { headers })
  if (!res.ok) throw new Error(`Failed to fetch: ${res.status}`)
  return res.text()
}

const EXT_LANG: Record<string, string> = {
  py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
  json: "json", yaml: "yaml", yml: "yaml", sh: "bash", bash: "bash", zsh: "bash",
  css: "css", scss: "scss", less: "less", sql: "sql", xml: "xml", toml: "toml",
  go: "go", rs: "rust", rb: "ruby", php: "php", java: "java",
  c: "c", cpp: "cpp", cs: "csharp", swift: "swift", kt: "kotlin",
  html: "html", env: "bash", cfg: "ini", ini: "ini",
}

function fileExt(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? ""
}

function isMarkdownFile(name: string, mimeType: string): boolean {
  return fileExt(name) === "md" || mimeType === "text/markdown"
}

async function downloadArtifact(artifact: ArtifactItem) {
  try {
    const url = await fetchArtifactBlob(artifact.url)
    const a = document.createElement("a")
    a.href = url
    a.download = artifact.name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch {
    window.open(`${getApiBaseUrl()}${artifact.url}`, "_blank")
  }
}

function isTextPreviewable(mime: string, name: string): boolean {
  if (mime === "text/html" || mime.startsWith("image/")) return false
  if (mime.startsWith("text/") || mime === "application/json") return true
  if (isMarkdownFile(name, mime)) return true
  return fileExt(name) in EXT_LANG
}

function highlight(code: string, name: string): string {
  const lang = EXT_LANG[fileExt(name)]
  try {
    return lang
      ? hljs.highlight(code, { language: lang }).value
      : hljs.highlightAuto(code).value
  } catch {
    return hljs.highlightAuto(code).value
  }
}

// ---------- thumbnail ----------

function ImageThumbnail({ artifact }: { artifact: ArtifactItem }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let objectUrl: string | null = null
    fetchArtifactBlob(artifact.url)
      .then((url) => { objectUrl = url; setBlobUrl(url) })
      .catch(() => setBlobUrl(null))
      .finally(() => setLoading(false))
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [artifact.url])

  if (loading) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-muted/30">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground/50" />
      </div>
    )
  }
  if (!blobUrl) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-muted/30">
        <File className="h-8 w-8 text-muted-foreground/40" />
      </div>
    )
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={blobUrl} alt={artifact.name} className="h-full w-full object-cover" />
  )
}

function ArtifactThumbnail({ artifact }: { artifact: ArtifactItem }) {
  const filter = getFilter(artifact.mime_type)
  if (filter === "images") return <ImageThumbnail artifact={artifact} />
  if (filter === "html") {
    return (
      <div className="flex h-full w-full items-center justify-center bg-blue-50 dark:bg-blue-950/30">
        <Globe className="h-10 w-10 text-blue-500/60" />
      </div>
    )
  }
  if (filter === "code") {
    return (
      <div className="flex h-full w-full items-center justify-center bg-emerald-50 dark:bg-emerald-950/30">
        <FileCode className="h-10 w-10 text-emerald-500/60" />
      </div>
    )
  }
  return (
    <div className="flex h-full w-full items-center justify-center bg-muted/50">
      <File className="h-10 w-10 text-muted-foreground/40" />
    </div>
  )
}

// ---------- preview panel ----------

function PreviewPanel({
  artifact,
  onClose,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
}: {
  artifact: ArtifactItem
  onClose: () => void
  onPrev: () => void
  onNext: () => void
  hasPrev: boolean
  hasNext: boolean
}) {
  const t = useTranslations("artifacts")
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [textContent, setTextContent] = useState<string | null>(null)
  const [highlightedHtml, setHighlightedHtml] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const filter = getFilter(artifact.mime_type)
  const isText = isTextPreviewable(artifact.mime_type, artifact.name)
  const isMarkdown = isMarkdownFile(artifact.name, artifact.mime_type)
  const needsBlob = filter === "images" || filter === "html"

  useEffect(() => {
    setBlobUrl(null)
    setTextContent(null)
    setHighlightedHtml(null)
    let objectUrl: string | null = null
    setLoading(true)

    if (needsBlob) {
      fetchArtifactBlob(artifact.url)
        .then((url) => { objectUrl = url; setBlobUrl(url) })
        .catch(() => setBlobUrl(null))
        .finally(() => setLoading(false))
    } else if (isText) {
      fetchArtifactText(artifact.url)
        .then((text) => {
          setTextContent(text)
          if (!isMarkdown) setHighlightedHtml(highlight(text, artifact.name))
        })
        .catch(() => setTextContent(null))
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }

    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [artifact.url, artifact.name, needsBlob, isText, isMarkdown])

  const handleDownload = useCallback(() => downloadArtifact(artifact), [artifact])

  return (
    <div className="absolute inset-0 flex flex-col">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b">
        <button
          onClick={onPrev}
          disabled={!hasPrev}
          title={t("previous")}
          className="shrink-0 flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors disabled:opacity-30 disabled:pointer-events-none"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <button
          onClick={onNext}
          disabled={!hasNext}
          title={t("next")}
          className="shrink-0 flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors disabled:opacity-30 disabled:pointer-events-none"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        <p className="flex-1 text-sm font-medium truncate" title={artifact.name}>
          {artifact.name}
        </p>
        <button
          onClick={onClose}
          className="shrink-0 flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-7 w-7 animate-spin text-muted-foreground" />
          </div>
        ) : filter === "images" && blobUrl ? (
          <div className="flex items-center justify-center">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={blobUrl}
              alt={artifact.name}
              className="max-w-full rounded object-contain"
            />
          </div>
        ) : filter === "html" && blobUrl ? (
          <iframe
            src={blobUrl}
            sandbox="allow-scripts"
            className="h-full min-h-[400px] w-full rounded border border-border"
            title={artifact.name}
          />
        ) : isText && isMarkdown && textContent !== null ? (
          <MarkdownContent content={textContent} />
        ) : isText && highlightedHtml !== null ? (
          <pre className="overflow-x-auto rounded border border-border bg-muted/30 p-4 text-xs leading-relaxed">
            <code className="hljs" dangerouslySetInnerHTML={{ __html: highlightedHtml }} />
          </pre>
        ) : isText && textContent !== null ? (
          <pre className="overflow-x-auto rounded border border-border bg-muted/30 p-4 text-xs leading-relaxed whitespace-pre-wrap break-words">
            {textContent}
          </pre>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center py-12">
            <File className="h-12 w-12 text-muted-foreground/30" />
            <div>
              <p className="text-sm font-medium">{artifact.name}</p>
              <p className="text-xs text-muted-foreground mt-1">
                {artifact.mime_type} · {formatSize(artifact.size)}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="shrink-0 flex items-center justify-between gap-2 px-4 py-3 border-t">
        <Button variant="outline" size="sm" onClick={handleDownload} className="gap-2">
          <Download className="h-4 w-4" />
          {t("download")}
        </Button>
        <Button asChild size="sm" variant="ghost" className="gap-2">
          <Link href={`/?c=${artifact.conversation_id}`}>
            <MessageSquare className="h-4 w-4" />
            {t("openConversation")}
          </Link>
        </Button>
      </div>
    </div>
  )
}

// ---------- main page ----------

function ArtifactsContent() {
  const t = useTranslations("artifacts")
  const tLayout = useTranslations("layout")
  const router = useRouter()
  const searchParams = useSearchParams()

  const activeFilter = (searchParams.get("tab") as FilterType) || "all"

  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<ArtifactItem | null>(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const pageSize = 24
  const totalPages = Math.ceil(total / pageSize)

  useEffect(() => {
    setLoading(true)
    setSelected(null)
    const typeParam = activeFilter !== "all" ? `&type=${activeFilter}` : ""
    apiFetch<{ items: ArtifactItem[]; total: number; page: number; size: number }>(
      `/api/artifacts?page=${page}&size=${pageSize}${typeParam}`,
    )
      .then((res) => {
        setArtifacts(res.items)
        setTotal(res.total)
      })
      .catch(() => {
        setArtifacts([])
        setTotal(0)
      })
      .finally(() => setLoading(false))
  }, [page, activeFilter])

  const filtered = artifacts

  const selectedIndex = selected
    ? filtered.findIndex(
        (a) => a.id === selected.id && a.conversation_id === selected.conversation_id,
      )
    : -1
  const hasPrev = selectedIndex > 0
  const hasNext = selectedIndex >= 0 && selectedIndex < filtered.length - 1

  const navigatePrev = useCallback(() => {
    if (selectedIndex > 0) setSelected(filtered[selectedIndex - 1])
  }, [selectedIndex, filtered])

  const navigateNext = useCallback(() => {
    if (selectedIndex >= 0 && selectedIndex < filtered.length - 1)
      setSelected(filtered[selectedIndex + 1])
  }, [selectedIndex, filtered])

  // Esc / arrow key navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null)
      if (!selected) return
      if (e.key === "ArrowLeft") { e.preventDefault(); navigatePrev() }
      if (e.key === "ArrowRight") { e.preventDefault(); navigateNext() }
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [selected, navigatePrev, navigateNext])

  const filters: { key: FilterType; label: string }[] = [
    { key: "all", label: t("filterAll") },
    { key: "images", label: t("filterImages") },
    { key: "html", label: t("filterHtml") },
    { key: "code", label: t("filterCode") },
    { key: "files", label: t("filterFiles") },
  ]

  return (
    <div className="flex-1 min-h-0 flex overflow-hidden">
      {/* Left: scrollable grid area */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        <div className="px-6 py-6">
          {/* Header */}
          <div className="mb-6">
            <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
              <Layers className="h-5 w-5" />
              {t("title")}
            </h1>
            <p className="text-sm text-muted-foreground">{t("description")}</p>
          </div>

          {/* Filter tabs */}
          <div className="flex flex-wrap gap-1.5 mb-6">
            {filters.map((f) => (
              <Link
                key={f.key}
                href={f.key === "all" ? "/artifacts" : `/artifacts?tab=${f.key}`}
                onClick={() => setPage(1)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  activeFilter === f.key
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                )}
              >
                {f.label}
              </Link>
            ))}
          </div>

          {/* Grid */}
          {loading ? (
            <div className={cn("grid gap-4", selected ? "grid-cols-2 md:grid-cols-3 lg:grid-cols-4" : "grid-cols-2 md:grid-cols-4 lg:grid-cols-6")}>
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton.ArtifactCard key={i} />
              ))}
            </div>
          ) : artifacts.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-24 gap-3 text-center">
              <Layers className="h-12 w-12 text-muted-foreground/30" />
              <p className="text-base font-medium">{t("noArtifacts")}</p>
              <p className="text-sm text-muted-foreground max-w-xs">{t("noArtifactsDesc")}</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-24 gap-3 text-center">
              <File className="h-12 w-12 text-muted-foreground/30" />
              <p className="text-sm text-muted-foreground">{t("noResults")}</p>
            </div>
          ) : (
            <div className={cn("grid gap-4", selected ? "grid-cols-2 md:grid-cols-3 lg:grid-cols-4" : "grid-cols-2 md:grid-cols-4 lg:grid-cols-6")}>
              {filtered.map((artifact) => {
                const isActive =
                  selected?.id === artifact.id &&
                  selected?.conversation_id === artifact.conversation_id
                return (
                  <div
                    key={`${artifact.conversation_id}-${artifact.id}`}
                    onClick={() => setSelected(isActive ? null : artifact)}
                    className={cn(
                      "group relative rounded-lg border overflow-hidden cursor-pointer transition-all",
                      isActive
                        ? "border-primary ring-2 ring-primary/20 shadow-md"
                        : "border-border hover:shadow-md",
                    )}
                  >
                    {/* Hover dropdown menu */}
                    <div className="absolute top-1.5 right-1.5 z-10 opacity-0 group-hover:opacity-100 has-[[data-state=open]]:opacity-100 transition-opacity">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            onClick={(e) => e.stopPropagation()}
                            className="flex h-7 w-7 items-center justify-center rounded-md bg-background/80 backdrop-blur-sm shadow-sm border border-border/50 text-muted-foreground hover:bg-background hover:text-foreground transition-colors"
                          >
                            <MoreHorizontal className="h-4 w-4" />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                          <DropdownMenuItem onClick={() => downloadArtifact(artifact)}>
                            <Download className="h-4 w-4 mr-2" />
                            {t("download")}
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => router.push(`/?c=${artifact.conversation_id}`)}>
                            <MessageSquare className="h-4 w-4 mr-2" />
                            {t("openConversation")}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>

                    <div className="aspect-square bg-muted/30 overflow-hidden">
                      <ArtifactThumbnail artifact={artifact} />
                    </div>
                    <div className="px-3 py-2">
                      <p className="text-sm font-medium truncate" title={artifact.name}>
                        {artifact.name}
                      </p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {formatSize(artifact.size)} · {formatRelativeTime(artifact.created_at, tLayout)}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-3 pt-6">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                title={t("previousPage")}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors disabled:opacity-30 disabled:pointer-events-none"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <span className="text-sm text-muted-foreground">
                {t("page")} {page} {t("of")} {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                title={t("nextPage")}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors disabled:opacity-30 disabled:pointer-events-none"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Right: preview panel — width animates 0 → 420px */}
      <aside
        className={cn(
          "shrink-0 relative border-l overflow-hidden transition-all duration-200 ease-in-out",
          selected ? "w-1/2" : "w-0 border-l-0",
        )}
      >
        {selected && (
          <PreviewPanel
            artifact={selected}
            onClose={() => setSelected(null)}
            onPrev={navigatePrev}
            onNext={navigateNext}
            hasPrev={hasPrev}
            hasNext={hasNext}
          />
        )}
      </aside>
    </div>
  )
}

export default function ArtifactsPage() {
  return (
    <Suspense>
      <ArtifactsContent />
    </Suspense>
  )
}
