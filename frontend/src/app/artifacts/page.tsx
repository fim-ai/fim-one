"use client"

import { useState, useEffect, useCallback } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import hljs from "highlight.js"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  Layers,
  Loader2,
  File,
  FileCode,
  Globe,
  MessageSquare,
  Download,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { apiFetch } from "@/lib/api"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"

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

function isTextPreviewable(mime: string, name: string): boolean {
  if (mime === "text/html" || mime.startsWith("image/")) return false
  if (mime.startsWith("text/") || mime === "application/json") return true
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

// ---------- sub-components ----------

function ArtifactThumbnail({ artifact }: { artifact: ArtifactItem }) {
  const filter = getFilter(artifact.mime_type)

  if (filter === "images") {
    return <ImageThumbnail artifact={artifact} />
  }

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

function ImageThumbnail({ artifact }: { artifact: ArtifactItem }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let objectUrl: string | null = null
    fetchArtifactBlob(artifact.url)
      .then((url) => {
        objectUrl = url
        setBlobUrl(url)
      })
      .catch(() => {
        setBlobUrl(null)
      })
      .finally(() => setLoading(false))

    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
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
    <img
      src={blobUrl}
      alt={artifact.name}
      className="h-full w-full object-cover"
    />
  )
}

// ---------- preview modal ----------

function PreviewModal({
  artifact,
  open,
  onOpenChange,
}: {
  artifact: ArtifactItem | null
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const t = useTranslations("artifacts")
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [textContent, setTextContent] = useState<string | null>(null)
  const [highlightedHtml, setHighlightedHtml] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const filter = artifact ? getFilter(artifact.mime_type) : null
  const isText = artifact ? isTextPreviewable(artifact.mime_type, artifact.name) : false
  const isMarkdown = artifact ? isMarkdownFile(artifact.name, artifact.mime_type) : false
  const needsBlob = filter === "images" || filter === "html"

  useEffect(() => {
    if (!open || !artifact) {
      setBlobUrl(null)
      setTextContent(null)
      setHighlightedHtml(null)
      return
    }

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
          if (!isMarkdownFile(artifact.name, artifact.mime_type)) {
            setHighlightedHtml(highlight(text, artifact.name))
          }
        })
        .catch(() => setTextContent(null))
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }

    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [open, artifact, needsBlob, isText])

  const handleDownload = useCallback(async () => {
    if (!artifact) return
    try {
      const url = blobUrl ?? (await fetchArtifactBlob(artifact.url))
      const a = document.createElement("a")
      a.href = url
      a.download = artifact.name
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      if (!blobUrl) URL.revokeObjectURL(url)
    } catch {
      window.open(`${getApiBaseUrl()}${artifact.url}`, "_blank")
    }
  }, [artifact, blobUrl])

  if (!artifact) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl w-full">
        <DialogHeader>
          <DialogTitle className="truncate pr-6">{artifact.name}</DialogTitle>
        </DialogHeader>

        <div className="min-h-32 flex items-center justify-center">
          {loading ? (
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          ) : filter === "images" && blobUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={blobUrl}
              alt={artifact.name}
              className="max-h-[70vh] object-contain rounded"
            />
          ) : filter === "html" && blobUrl ? (
            <iframe
              src={blobUrl}
              sandbox="allow-scripts"
              className="h-[60vh] w-full rounded border border-border"
              title={artifact.name}
            />
          ) : isText && isMarkdown && textContent !== null ? (
            <div className="w-full max-h-[65vh] overflow-y-auto rounded border border-border p-4 prose prose-sm dark:prose-invert max-w-none">
              <Markdown remarkPlugins={[remarkGfm]}>{textContent}</Markdown>
            </div>
          ) : isText && highlightedHtml !== null ? (
            <pre className="w-full max-h-[65vh] overflow-auto rounded border border-border bg-muted/30 p-4 text-xs leading-relaxed">
              <code
                className="hljs"
                dangerouslySetInnerHTML={{ __html: highlightedHtml }}
              />
            </pre>
          ) : isText && textContent !== null ? (
            <pre className="w-full max-h-[65vh] overflow-auto rounded border border-border bg-muted/30 p-4 text-xs leading-relaxed whitespace-pre-wrap break-words">
              {textContent}
            </pre>
          ) : (
            <div className="flex flex-col items-center gap-3 py-8 text-center">
              <File className="h-12 w-12 text-muted-foreground/40" />
              <div>
                <p className="font-medium text-sm">{artifact.name}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {artifact.mime_type} &middot; {formatSize(artifact.size)}
                </p>
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="flex-row items-center justify-between gap-2 sm:justify-between">
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
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------- main page ----------

export default function ArtifactsPage() {
  const t = useTranslations("artifacts")
  const tLayout = useTranslations("layout")

  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeFilter, setActiveFilter] = useState<FilterType>("all")
  const [previewArtifact, setPreviewArtifact] = useState<ArtifactItem | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)

  useEffect(() => {
    setLoading(true)
    apiFetch<{ data: ArtifactItem[] }>("/api/artifacts")
      .then((res) => setArtifacts(res.data))
      .catch(() => setArtifacts([]))
      .finally(() => setLoading(false))
  }, [])

  const filtered =
    activeFilter === "all"
      ? artifacts
      : artifacts.filter((a) => getFilter(a.mime_type) === activeFilter)

  const filters: { key: FilterType; label: string }[] = [
    { key: "all", label: t("filterAll") },
    { key: "images", label: t("filterImages") },
    { key: "html", label: t("filterHtml") },
    { key: "code", label: t("filterCode") },
    { key: "files", label: t("filterFiles") },
  ]

  const handleCardClick = (artifact: ArtifactItem) => {
    setPreviewArtifact(artifact)
    setPreviewOpen(true)
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto py-8 px-4">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Layers className="h-6 w-6" />
            {t("title")}
          </h1>
          {!loading && (
            <span className="text-sm text-muted-foreground">
              {t("count", { count: artifacts.length })}
            </span>
          )}
        </div>

        {/* Filter tabs */}
        <div className="flex flex-wrap gap-1.5 mb-6">
          {filters.map((f) => (
            <button
              key={f.key}
              onClick={() => setActiveFilter(f.key)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                activeFilter === f.key
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* Content */}
        {loading ? (
          <div className="flex items-center justify-center py-24">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
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
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {filtered.map((artifact) => (
              <div
                key={`${artifact.conversation_id}-${artifact.id}`}
                onClick={() => handleCardClick(artifact)}
                className="group rounded-lg border border-border overflow-hidden cursor-pointer hover:shadow-md transition-shadow"
              >
                {/* Thumbnail */}
                <div className="aspect-square bg-muted/30 overflow-hidden">
                  <ArtifactThumbnail artifact={artifact} />
                </div>

                {/* Info */}
                <div className="px-3 py-2">
                  <p
                    className="text-sm font-medium truncate"
                    title={artifact.name}
                  >
                    {artifact.name}
                  </p>
                  <Link
                    href={`/?c=${artifact.conversation_id}`}
                    onClick={(e) => e.stopPropagation()}
                    className="flex items-center gap-1 mt-0.5 text-xs text-muted-foreground hover:text-foreground transition-colors truncate"
                    title={artifact.conversation_title}
                  >
                    <MessageSquare className="h-3 w-3 shrink-0" />
                    <span className="truncate">{artifact.conversation_title}</span>
                  </Link>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {formatSize(artifact.size)} &middot; {formatRelativeTime(artifact.created_at, tLayout)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Preview modal */}
      <PreviewModal
        artifact={previewArtifact}
        open={previewOpen}
        onOpenChange={(v) => {
          setPreviewOpen(v)
          if (!v) setPreviewArtifact(null)
        }}
      />
    </div>
  )
}
