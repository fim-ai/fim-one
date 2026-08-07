"use client"

import React, { useState } from "react"
import { Check, Copy, Download } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useEvidenceSources } from "@/contexts/evidence-context"
import { useTranslations } from "next-intl"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import remarkCjkFriendly from "remark-cjk-friendly"
import remarkCjkFriendlyGfmStrikethrough from "remark-cjk-friendly-gfm-strikethrough"
import rehypeKatex from "rehype-katex"
import rehypeHighlight from "rehype-highlight"
import rehypeRaw from "rehype-raw"

function CitationBadge({ index }: { index: number }) {
  const sources = useEvidenceSources()
  const t = useTranslations("playground")
  const source = sources.find(s => s.index === index)

  const badgeClassName = "inline-flex items-center justify-center min-w-[1.1em] h-[1.1em] px-0.5 ml-0.5 rounded text-[0.65em] font-medium bg-primary/10 text-primary align-super"

  // No context or no matching source — fallback to plain badge
  if (!source) {
    return <sup className={`${badgeClassName} cursor-default`}>{index}</sup>
  }

  // Source found — Popover with citation details
  return (
    <Popover>
      <PopoverTrigger asChild>
        <sup className={`${badgeClassName} cursor-pointer hover:bg-primary/20 transition-colors`}>{index}</sup>
      </PopoverTrigger>
      <PopoverContent side="top" className="w-72 p-3 space-y-1.5">
        <div className="text-xs font-medium truncate">{source.displayName}</div>
        {source.kbName && (
          <span className="text-[10px] text-muted-foreground">{source.kbName}</span>
        )}
        {source.quote && (
          <p className="text-[11px] italic text-muted-foreground/80 line-clamp-3">&ldquo;{source.quote}&rdquo;</p>
        )}
        <div className="text-[10px] text-muted-foreground/60">
          {t("citationRelevance", { value: (source.relevance * 100).toFixed(0) })}
          {source.page != null && ` \u00b7 p.${source.page}`}
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** Replace [N] citation markers in text with styled <sup> badges */
function processCitations(children: React.ReactNode): React.ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child !== "string") return child
    const parts = child.split(/(\[\d+\])/)
    if (parts.length === 1) return child
    return parts.map((part, i) => {
      const m = part.match(/^\[(\d+)\]$/)
      if (m) {
        return <CitationBadge key={i} index={parseInt(m[1])} />
      }
      return part
    })
  })
}

function ClickableImage({ src, alt }: { src: string; alt: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={alt}
        className="max-h-72 w-auto max-w-full rounded-lg my-2 block cursor-zoom-in hover:opacity-90 transition-opacity"
        onClick={() => setOpen(true)}
      />
      {open && (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col gap-3 pt-4">
            <a
              href={src}
              download
              target="_blank"
              rel="noopener noreferrer"
              className="absolute right-12 top-4 rounded-sm opacity-70 hover:opacity-100 transition-opacity text-foreground"
              onClick={(e) => e.stopPropagation()}
            >
              <Download className="h-4 w-4" />
            </a>
            <DialogTitle className="leading-normal pb-1 pr-24 truncate text-xs font-medium">{alt || "Image"}</DialogTitle>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt={alt} className="max-h-[calc(90vh-6rem)] max-w-full w-auto mx-auto block rounded object-contain" />
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

/**
 * Normalise markdown so that ATX headings without a space after the `#`
 * sequence (e.g. `###标题`) are parsed correctly.  CommonMark requires
 * `### heading` (with a space), but many LLMs omit the space before CJK text.
 */
function normalizeHeadings(md: string): string {
  return md.replace(/^(#{1,6})([^\s#])/gm, "$1 $2")
}

/** Extract plain text from React children recursively */
function extractText(node: React.ReactNode): string {
  if (typeof node === "string") return node
  if (typeof node === "number") return String(node)
  if (!node) return ""
  if (Array.isArray(node)) return node.map(extractText).join("")
  if (React.isValidElement(node) && node.props) {
    return extractText((node.props as { children?: React.ReactNode }).children)
  }
  return ""
}

/** Code block wrapper with a copy button */
function CodeBlock({ children, ...props }: React.ComponentProps<"pre">) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    const text = extractText(children)
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <pre
      className="group relative overflow-x-auto rounded-lg bg-muted/50 p-4 text-sm font-mono my-3 max-w-full"
      {...props}
    >
      <button
        type="button"
        onClick={handleCopy}
        className="absolute right-2 top-2 rounded-md p-1.5 text-muted-foreground/50 hover:text-muted-foreground hover:bg-muted transition-all opacity-0 group-hover:opacity-100"
        aria-label="Copy code"
      >
        {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      {children}
    </pre>
  )
}

/** Stable remark/rehype plugin arrays — allocated once at module scope */
const remarkPlugins = [remarkCjkFriendly, remarkCjkFriendlyGfmStrikethrough, remarkGfm, remarkMath]
const rehypePlugins = [rehypeRaw, rehypeKatex, rehypeHighlight]

/* ---- Card-style comparison table ---- */

interface ComparisonTable {
  /** Data column headers (first header cell — the attribute-column title — is dropped) */
  header: React.ReactNode[]
  rows: { label: React.ReactNode; values: React.ReactNode[] }[]
}

const CARD_TABLE_MAX_ROWS = 10
const CARD_TABLE_MAX_LABEL_CHARS = 30

type ElementWithChildren = React.ReactElement<{ children?: React.ReactNode }>

/** Valid element children of a rendered thead/tbody/tr element */
function elementChildren(el: React.ReactNode): ElementWithChildren[] {
  const kids = React.isValidElement<{ children?: React.ReactNode }>(el)
    ? el.props.children
    : undefined
  return React.Children.toArray(kids).filter((c): c is ElementWithChildren =>
    React.isValidElement(c)
  )
}

/**
 * Detect a small "attribute comparison" table: 2-3 columns whose first column
 * holds short attribute labels, 2-10 body rows. Those render as a card layout
 * (label repeated inside each value cell); anything else falls back to the
 * classic grid table.
 */
function extractComparisonTable(children: React.ReactNode): ComparisonTable | null {
  const sections = React.Children.toArray(children).filter(React.isValidElement)
  if (sections.length !== 2) return null
  const [thead, tbody] = sections
  const headRows = elementChildren(thead)
  if (headRows.length !== 1) return null
  const headerCells = elementChildren(headRows[0]).map((c) => c.props.children)
  if (headerCells.length < 2 || headerCells.length > 3) return null
  const trs = elementChildren(tbody)
  if (trs.length < 2 || trs.length > CARD_TABLE_MAX_ROWS) return null
  const rows: ComparisonTable["rows"] = []
  for (const tr of trs) {
    const cells = elementChildren(tr).map((c) => c.props.children)
    if (cells.length !== headerCells.length) return null
    const labelText = extractText(cells[0]).trim()
    if (!labelText || labelText.length > CARD_TABLE_MAX_LABEL_CHARS) return null
    rows.push({ label: cells[0], values: cells.slice(1) })
  }
  return { header: headerCells.slice(1), rows }
}

function CardTable({ table }: { table: ComparisonTable }) {
  const gridCols = table.header.length === 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"
  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border">
      <div className={`grid ${gridCols} gap-x-6 gap-y-1 border-b border-border bg-muted/40 px-4 py-3`}>
        {table.header.map((cell, i) => (
          <div key={i} className="text-sm font-semibold">
            {cell}
          </div>
        ))}
      </div>
      <div className="divide-y divide-border/50">
        {table.rows.map((row, ri) => (
          <div key={ri} className={`grid ${gridCols} gap-x-6 gap-y-3 px-4 py-3`}>
            {row.values.map((value, vi) => (
              <div key={vi}>
                <div className="mb-0.5 text-xs text-muted-foreground">{row.label}</div>
                <div className="text-sm leading-relaxed">{processCitations(value)}</div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * Stable component overrides for react-markdown.
 * Hoisted to module scope so the object reference never changes between renders,
 * which allows React.memo on MarkdownContent to work effectively.
 * All referenced helpers (processCitations, ClickableImage) are module-level.
 */
const markdownComponents = {
  pre: CodeBlock,
  code({ children, className: codeClassName, ...props }: React.ComponentProps<"code">) {
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
  p({ children, ...props }: React.ComponentProps<"p">) {
    return (
      <p className="mb-3 last:mb-0 leading-relaxed" {...props}>
        {processCitations(children)}
      </p>
    )
  },
  ul({ children, ...props }: React.ComponentProps<"ul">) {
    return (
      <ul className="mb-3 list-disc pl-6 last:mb-0 space-y-1" {...props}>
        {children}
      </ul>
    )
  },
  ol({ children, ...props }: React.ComponentProps<"ol">) {
    return (
      <ol className="mb-3 list-decimal pl-6 last:mb-0 space-y-1" {...props}>
        {children}
      </ol>
    )
  },
  li({ children, ...props }: React.ComponentProps<"li">) {
    return (
      <li className="leading-relaxed" {...props}>
        {processCitations(children)}
      </li>
    )
  },
  h1({ children, ...props }: React.ComponentProps<"h1">) {
    return (
      <h1 className="mt-6 mb-3 text-xl font-bold first:mt-0" {...props}>
        {children}
      </h1>
    )
  },
  h2({ children, ...props }: React.ComponentProps<"h2">) {
    return (
      <h2 className="mt-5 mb-2 text-lg font-semibold first:mt-0" {...props}>
        {children}
      </h2>
    )
  },
  h3({ children, ...props }: React.ComponentProps<"h3">) {
    return (
      <h3 className="mt-4 mb-2 text-base font-semibold first:mt-0" {...props}>
        {children}
      </h3>
    )
  },
  table({ children, ...props }: React.ComponentProps<"table">) {
    const comparison = extractComparisonTable(children)
    if (comparison) return <CardTable table={comparison} />
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
  thead({ children, ...props }: React.ComponentProps<"thead">) {
    return (
      <thead className="bg-muted/40" {...props}>
        {children}
      </thead>
    )
  },
  th({ children, ...props }: React.ComponentProps<"th">) {
    return (
      <th
        className="border-b border-border px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        {...props}
      >
        {children}
      </th>
    )
  },
  td({ children, ...props }: React.ComponentProps<"td">) {
    return (
      <td className="border-b border-border/50 px-3 py-2" {...props}>
        {processCitations(children)}
      </td>
    )
  },
  blockquote({ children, ...props }: React.ComponentProps<"blockquote">) {
    return (
      <blockquote
        className="my-3 border-l-2 border-primary/40 pl-4 italic text-muted-foreground"
        {...props}
      >
        {children}
      </blockquote>
    )
  },
  hr(props: React.ComponentProps<"hr">) {
    return <hr className="my-4 border-border" {...props} />
  },
  img({ src, alt }: React.ComponentProps<"img">) {
    return <ClickableImage src={src ?? ""} alt={alt ?? ""} />
  },
  a({ children, ...props }: React.ComponentProps<"a">) {
    return (
      <a target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2 hover:text-primary/80" {...props}>
        {children}
      </a>
    )
  },
  strong({ children, ...props }: React.ComponentProps<"strong">) {
    return (
      <strong className="font-semibold text-foreground" {...props}>
        {children}
      </strong>
    )
  },
}

export const MarkdownContent = React.memo(function MarkdownContent({ content, className }: MarkdownContentProps) {
  const normalized = normalizeHeadings(content)
  return (
    <div className={`min-w-0 overflow-hidden ${className ?? ""}`}>
      <Markdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={markdownComponents}
      >
        {normalized}
      </Markdown>
    </div>
  )
})
