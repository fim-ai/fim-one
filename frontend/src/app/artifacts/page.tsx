"use client"

import { useState, useEffect, useCallback, Suspense } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
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
  X,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { apiFetch } from "@/lib/api"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import { Button } from "@/components/ui/button"

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
}: {
  artifact: ArtifactItem
  onClose: () => void
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

  const handleDownload = useCallback(async () => {
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

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between gap-2 px-4 py-3 border-b">
        <p className="text-sm font-medium truncate" title={artifact.name}>
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
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <Markdown remarkPlugins={[remarkGfm]}>{textContent}</Markdown>
          </div>
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

  const handleFilterChange = (tab: FilterType) => {
    if (tab === "all") {
      router.replace("/artifacts")
    } else {
      router.replace(`/artifacts?tab=${tab}`)
    }
  }

  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<ArtifactItem | null>(null)

  useEffect(() => {
    setLoading(true)
    apiFetch<{ data: ArtifactItem[] }>("/api/artifacts")
      .then((res) => setArtifacts(res.data))
      .catch(() => setArtifacts([]))
      .finally(() => setLoading(false))
  }, [])

  // Esc to close panel
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") setSelected(null) }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
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

  return (
    <div className="h-full flex overflow-hidden">
      {/* Left: scrollable grid area */}
      <div className="flex-1 min-w-0 overflow-y-auto">
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
                onClick={() => handleFilterChange(f.key)}
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

          {/* Grid */}
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
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              {filtered.map((artifact) => {
                const isActive =
                  selected?.id === artifact.id &&
                  selected?.conversation_id === artifact.conversation_id
                return (
                  <div
                    key={`${artifact.conversation_id}-${artifact.id}`}
                    onClick={() => setSelected(isActive ? null : artifact)}
                    className={cn(
                      "group rounded-lg border overflow-hidden cursor-pointer transition-all",
                      isActive
                        ? "border-primary ring-2 ring-primary/20 shadow-md"
                        : "border-border hover:shadow-md",
                    )}
                  >
                    <div className="aspect-square bg-muted/30 overflow-hidden">
                      <ArtifactThumbnail artifact={artifact} />
                    </div>
                    <div className="px-3 py-2">
                      <p className="text-sm font-medium truncate" title={artifact.name}>
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
                        {formatSize(artifact.size)} · {formatRelativeTime(artifact.created_at, tLayout)}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Right: preview panel — width animates 0 → 420px */}
      <aside
        className={cn(
          "shrink-0 border-l flex flex-col overflow-hidden transition-all duration-200 ease-in-out",
          selected ? "w-1/2" : "w-0 border-l-0",
        )}
      >
        {selected && <PreviewPanel artifact={selected} onClose={() => setSelected(null)} />}
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
