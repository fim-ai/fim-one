"use client"

import React, { useState, useEffect } from "react"
import { Download } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import rehypeHighlight from "rehype-highlight"

/** Replace [N] citation markers in text with styled <sup> badges */
function processCitations(children: React.ReactNode): React.ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child !== "string") return child
    const parts = child.split(/(\[\d+\])/)
    if (parts.length === 1) return child
    return parts.map((part, i) => {
      const m = part.match(/^\[(\d+)\]$/)
      if (m) {
        return (
          <sup
            key={i}
            className="inline-flex items-center justify-center min-w-[1.1em] h-[1.1em] px-0.5 ml-0.5 rounded text-[0.65em] font-medium bg-primary/10 text-primary align-super cursor-default"
          >
            {m[1]}
          </sup>
        )
      }
      return part
    })
  })
}

/** Check if a URL points to an authenticated artifact endpoint. */
function isArtifactUrl(url: string): boolean {
  return url.includes("/api/conversations/") && url.includes("/artifacts/")
}

/** Fetch an image through the authenticated artifact API, returning a blob URL. */
async function fetchAuthImage(url: string): Promise<string> {
  const token = typeof window !== "undefined" ? localStorage.getItem(ACCESS_TOKEN_KEY) : null
  const headers: Record<string, string> = {}
  if (token) headers["Authorization"] = `Bearer ${token}`
  const fullUrl = url.startsWith("http") ? url : `${getApiBaseUrl()}${url}`
  const res = await fetch(fullUrl, { headers })
  if (!res.ok) throw new Error(`Failed to fetch image: ${res.status}`)
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

function ClickableImage({ src, alt }: { src: string; alt: string }) {
  const [open, setOpen] = useState(false)
  // For artifact URLs, fetch with auth and use a blob URL for rendering.
  const [imgSrc, setImgSrc] = useState(() => isArtifactUrl(src) ? "" : src)

  useEffect(() => {
    if (!isArtifactUrl(src)) {
      setImgSrc(src)
      return
    }
    let revoked = false
    fetchAuthImage(src).then((blobUrl) => {
      if (!revoked) setImgSrc(blobUrl)
    }).catch(() => {
      // Fallback to raw URL (will likely fail but shows alt text)
      if (!revoked) setImgSrc(src)
    })
    return () => { revoked = true }
  }, [src])

  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    try {
      const blobUrl = isArtifactUrl(src) ? imgSrc || await fetchAuthImage(src) : src
      const a = document.createElement("a")
      a.href = blobUrl
      a.download = alt || "image"
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    } catch {
      window.open(src, "_blank")
    }
  }

  return (
    <>
      {imgSrc ? (
        <img
          src={imgSrc}
          alt={alt}
          className="max-h-72 w-auto max-w-full rounded-lg my-2 block cursor-zoom-in hover:opacity-90 transition-opacity"
          onClick={() => setOpen(true)}
        />
      ) : (
        <div className="max-h-72 w-64 rounded-lg my-2 flex items-center justify-center bg-muted/30 text-muted-foreground text-sm animate-pulse">
          Loading image…
        </div>
      )}
      {open && (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col gap-3 pt-4">
            <button
              onClick={handleDownload}
              className="absolute right-12 top-4 rounded-sm opacity-70 hover:opacity-100 transition-opacity text-foreground"
            >
              <Download className="h-4 w-4" />
            </button>
            <DialogTitle className="leading-normal pb-1 pr-24 truncate text-xs font-medium">{alt || "Image"}</DialogTitle>
            <img src={imgSrc} alt={alt} className="max-h-[calc(90vh-6rem)] max-w-full w-auto mx-auto block rounded object-contain" />
          </DialogContent>
        </Dialog>
      )}
    </>
  )
}

interface MarkdownContentProps {
  content: string
  className?: string
}

export function MarkdownContent({ content, className }: MarkdownContentProps) {
  return (
    <div className={`min-w-0 overflow-hidden ${className ?? ""}`}>
      <Markdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, rehypeHighlight]}
        components={{
          pre({ children, ...props }) {
            return (
              <pre
                className="overflow-x-auto rounded-lg bg-muted/50 p-4 text-sm font-mono my-3 max-w-full"
                {...props}
              >
                {children}
              </pre>
            )
          },
          code({ children, className: codeClassName, ...props }) {
            const isInline = !codeClassName
            if (isInline) {
              return (
                <code
                  className="rounded-md bg-muted/60 px-1.5 py-0.5 text-[0.9em] font-mono"
                  {...props}
                >
                  {children}
                </code>
              )
            }
            return (
              <code className={codeClassName} {...props}>
                {children}
              </code>
            )
          },
          p({ children, ...props }) {
            return (
              <p className="mb-3 last:mb-0 leading-relaxed" {...props}>
                {processCitations(children)}
              </p>
            )
          },
          ul({ children, ...props }) {
            return (
              <ul className="mb-3 list-disc pl-6 last:mb-0 space-y-1" {...props}>
                {children}
              </ul>
            )
          },
          ol({ children, ...props }) {
            return (
              <ol className="mb-3 list-decimal pl-6 last:mb-0 space-y-1" {...props}>
                {children}
              </ol>
            )
          },
          li({ children, ...props }) {
            return (
              <li className="leading-relaxed" {...props}>
                {processCitations(children)}
              </li>
            )
          },
          h1({ children, ...props }) {
            return (
              <h1 className="mt-6 mb-3 text-xl font-bold first:mt-0" {...props}>
                {children}
              </h1>
            )
          },
          h2({ children, ...props }) {
            return (
              <h2 className="mt-5 mb-2 text-lg font-semibold first:mt-0" {...props}>
                {children}
              </h2>
            )
          },
          h3({ children, ...props }) {
            return (
              <h3 className="mt-4 mb-2 text-base font-semibold first:mt-0" {...props}>
                {children}
              </h3>
            )
          },
          table({ children, ...props }) {
            return (
              <div className="my-3 overflow-x-auto rounded-lg border border-border">
                <table
                  className="w-full border-collapse text-sm"
                  {...props}
                >
                  {children}
                </table>
              </div>
            )
          },
          thead({ children, ...props }) {
            return (
              <thead className="bg-muted/40" {...props}>
                {children}
              </thead>
            )
          },
          th({ children, ...props }) {
            return (
              <th
                className="border-b border-border px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground"
                {...props}
              >
                {children}
              </th>
            )
          },
          td({ children, ...props }) {
            return (
              <td className="border-b border-border/50 px-3 py-2" {...props}>
                {processCitations(children)}
              </td>
            )
          },
          blockquote({ children, ...props }) {
            return (
              <blockquote
                className="my-3 border-l-2 border-primary/40 pl-4 italic text-muted-foreground"
                {...props}
              >
                {children}
              </blockquote>
            )
          },
          hr(props) {
            return <hr className="my-4 border-border" {...props} />
          },
          img({ src, alt }) {
            return <ClickableImage src={src ?? ""} alt={alt ?? ""} />
          },
          a({ children, ...props }) {
            return (
              <a target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2 hover:text-primary/80" {...props}>
                {children}
              </a>
            )
          },
          strong({ children, ...props }) {
            return (
              <strong className="font-semibold text-foreground" {...props}>
                {children}
              </strong>
            )
          },
        }}
      >
        {content}
      </Markdown>
    </div>
  )
}
