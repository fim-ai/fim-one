"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import hljs from "highlight.js"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  Download,
  Loader2,
  File,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  Globe,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetFooter,
} from "@/components/ui/sheet"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import type { ArtifactInfo } from "./types"

// ---------- helpers ----------

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function fileExt(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? ""
}

type FilterType = "images" | "html" | "code" | "files"

function getFilter(mime: string): FilterType {
  if (mime.startsWith("image/")) return "images"
  if (mime === "text/html") return "html"
  if (mime.startsWith("text/") || mime === "application/json") return "code"
  return "files"
}

function getTypeLabel(mime: string, name: string): string {
  const ext = fileExt(name).toUpperCase()
  const filter = getFilter(mime)
  if (filter === "images") return ext ? `Image \u00b7 ${ext}` : "Image"
  if (filter === "html") return "Web Page \u00b7 HTML"
  if (mime === "application/json") return "Data \u00b7 JSON"
  if (mime === "text/csv") return "Spreadsheet \u00b7 CSV"
  if (mime === "text/markdown" || fileExt(name) === "md") return "Document \u00b7 MD"
  if (filter === "code") return ext ? `Code \u00b7 ${ext}` : "Text"
  return ext ? `File \u00b7 ${ext}` : "File"
}

function getThumbnailStyle(mime: string): { Icon: typeof File; bg: string; fg: string } {
  const filter = getFilter(mime)
  if (filter === "images") return { Icon: FileImage, bg: "bg-violet-50 dark:bg-violet-950/30", fg: "text-violet-500/70" }
  if (filter === "html") return { Icon: Globe, bg: "bg-blue-50 dark:bg-blue-950/30", fg: "text-blue-500/70" }
  if (filter === "code") return { Icon: FileCode, bg: "bg-emerald-50 dark:bg-emerald-950/30", fg: "text-emerald-500/70" }
  if (mime.includes("spreadsheet") || mime.includes("excel") || mime === "text/csv")
    return { Icon: FileSpreadsheet, bg: "bg-green-50 dark:bg-green-950/30", fg: "text-green-500/70" }
  return { Icon: FileText, bg: "bg-muted/50", fg: "text-muted-foreground/50" }
}

const EXT_LANG: Record<string, string> = {
  py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
  json: "json", yaml: "yaml", yml: "yaml", sh: "bash", bash: "bash", zsh: "bash",
  css: "css", scss: "scss", less: "less", sql: "sql", xml: "xml", toml: "toml",
  go: "go", rs: "rust", rb: "ruby", php: "php", java: "java",
  c: "c", cpp: "cpp", cs: "csharp", swift: "swift", kt: "kotlin",
  html: "html", env: "bash", cfg: "ini", ini: "ini",
}

function isTextPreviewable(mime: string, name: string): boolean {
  if (mime === "text/html" || mime.startsWith("image/")) return false
  if (mime.startsWith("text/") || mime === "application/json") return true
  return fileExt(name) in EXT_LANG
}

function isMarkdownFile(name: string, mime: string): boolean {
  return fileExt(name) === "md" || mime === "text/markdown"
}

function highlightCode(code: string, name: string): string {
  const lang = EXT_LANG[fileExt(name)]
  try {
    return lang
      ? hljs.highlight(code, { language: lang }).value
      : hljs.highlightAuto(code).value
  } catch {
    return hljs.highlightAuto(code).value
  }
}

async function fetchArtifactBlob(url: string): Promise<string> {
  const token = typeof window !== "undefined" ? localStorage.getItem(ACCESS_TOKEN_KEY) : null
  const headers: Record<string, string> = {}
  if (token) headers["Authorization"] = `Bearer ${token}`
  const res = await fetch(`${getApiBaseUrl()}${url}`, { headers })
  if (!res.ok) throw new Error(`Failed to fetch artifact: ${res.status}`)
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

// ---------- Image thumbnail with auth ----------

function ImageThumbnail({ url, name }: { url: string; name: string }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let objectUrl: string | null = null
    fetchArtifactBlob(url)
      .then((u) => { objectUrl = u; setBlobUrl(u) })
      .catch(() => setBlobUrl(null))
      .finally(() => setLoading(false))
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [url])

  if (loading) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground/50" />
      </div>
    )
  }
  if (!blobUrl) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <File className="h-6 w-6 text-muted-foreground/40" />
      </div>
    )
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={blobUrl} alt={name} className="h-full w-full object-cover" />
  )
}

// ---------- Preview drawer ----------

function ArtifactPreviewSheet({
  artifact,
  open,
  onOpenChange,
}: {
  artifact: ArtifactInfo | null
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const t = useTranslations("dag")
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [textContent, setTextContent] = useState<string | null>(null)
  const [highlightedHtml, setHighlightedHtml] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const filter = artifact ? getFilter(artifact.mime_type) : null
  const isText = artifact ? isTextPreviewable(artifact.mime_type, artifact.name) : false
  const isMd = artifact ? isMarkdownFile(artifact.name, artifact.mime_type) : false
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
        .then((u) => { objectUrl = u; setBlobUrl(u) })
        .catch(() => setBlobUrl(null))
        .finally(() => setLoading(false))
    } else if (isText) {
      fetchArtifactText(artifact.url)
        .then((txt) => {
          setTextContent(txt)
          if (!isMd) setHighlightedHtml(highlightCode(txt, artifact.name))
        })
        .catch(() => setTextContent(null))
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }

    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [open, artifact, needsBlob, isText, isMd])

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
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-2xl flex flex-col gap-0 p-0">
        <SheetHeader className="shrink-0 px-4 py-3 border-b space-y-0">
          <SheetTitle className="text-sm truncate pr-6">{artifact.name}</SheetTitle>
          <p className="text-xs text-muted-foreground">
            {getTypeLabel(artifact.mime_type, artifact.name)} &middot; {formatSize(artifact.size)}
          </p>
        </SheetHeader>

        {/* Preview content */}
        <div className="flex-1 min-h-0 overflow-y-auto p-4">
          {loading ? (
            <div className="flex h-full min-h-32 items-center justify-center">
              <Loader2 className="h-7 w-7 animate-spin text-muted-foreground" />
            </div>
          ) : filter === "images" && blobUrl ? (
            <div className="flex items-center justify-center">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={blobUrl} alt={artifact.name} className="max-w-full rounded object-contain" />
            </div>
          ) : filter === "html" && blobUrl ? (
            <iframe
              src={blobUrl}
              sandbox="allow-scripts"
              className="h-full min-h-[400px] w-full rounded border border-border"
              title={artifact.name}
            />
          ) : isText && isMd && textContent !== null ? (
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
                  {artifact.mime_type} &middot; {formatSize(artifact.size)}
                </p>
              </div>
            </div>
          )}
        </div>

        <SheetFooter className="shrink-0 px-4 py-3 border-t sm:justify-start">
          <Button variant="outline" size="sm" onClick={handleDownload} className="gap-2">
            <Download className="h-4 w-4" />
            {t("download")}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

// ---------- Single artifact card ----------

function ArtifactCard({
  artifact,
  onPreview,
  onDownload,
}: {
  artifact: ArtifactInfo
  onPreview: () => void
  onDownload: () => void
}) {
  const t = useTranslations("dag")
  const filter = getFilter(artifact.mime_type)
  const { Icon, bg, fg } = getThumbnailStyle(artifact.mime_type)
  const typeLabel = getTypeLabel(artifact.mime_type, artifact.name)
  const displayName = artifact.name.replace(/\.[^.]+$/, "") || artifact.name

  return (
    <div
      onClick={onPreview}
      className="group flex items-center gap-0 rounded-lg border border-border/60 overflow-hidden cursor-pointer hover:border-border hover:shadow-sm transition-all"
    >
      {/* Thumbnail */}
      <div className={`shrink-0 w-16 h-16 overflow-hidden ${filter === "images" ? "" : bg}`}>
        {filter === "images" ? (
          <ImageThumbnail url={artifact.url} name={artifact.name} />
        ) : (
          <div className={`flex h-full w-full items-center justify-center ${bg}`}>
            <Icon className={`h-7 w-7 ${fg}`} />
          </div>
        )}
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0 px-3 py-2">
        <p className="text-sm font-medium truncate" title={artifact.name}>
          {displayName}
        </p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {typeLabel} &middot; {formatSize(artifact.size)}
        </p>
      </div>

      {/* Download button */}
      <div className="shrink-0 pr-3">
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          onClick={(e) => {
            e.stopPropagation()
            onDownload()
          }}
        >
          <Download className="h-3.5 w-3.5" />
          {t("download")}
        </Button>
      </div>
    </div>
  )
}

// ---------- Main component ----------

interface ArtifactChipsProps {
  artifacts: ArtifactInfo[]
  className?: string
}

export function ArtifactChips({ artifacts, className }: ArtifactChipsProps) {
  const [previewArtifact, setPreviewArtifact] = useState<ArtifactInfo | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)

  const handleDownload = useCallback(async (artifact: ArtifactInfo) => {
    try {
      const blobUrl = await fetchArtifactBlob(artifact.url)
      const a = document.createElement("a")
      a.href = blobUrl
      a.download = artifact.name
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(blobUrl)
    } catch {
      window.open(`${getApiBaseUrl()}${artifact.url}`, "_blank")
    }
  }, [])

  if (!artifacts.length) return null

  return (
    <>
      <div className={`flex flex-col gap-2 ${className ?? ""}`}>
        {artifacts.map((artifact, idx) => (
          <ArtifactCard
            key={idx}
            artifact={artifact}
            onPreview={() => {
              setPreviewArtifact(artifact)
              setPreviewOpen(true)
            }}
            onDownload={() => handleDownload(artifact)}
          />
        ))}
      </div>

      <ArtifactPreviewSheet
        artifact={previewArtifact}
        open={previewOpen}
        onOpenChange={(v) => {
          setPreviewOpen(v)
          if (!v) setPreviewArtifact(null)
        }}
      />
    </>
  )
}
