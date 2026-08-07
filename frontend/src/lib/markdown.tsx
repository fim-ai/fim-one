"use client"

import React, { useEffect, useRef, useState } from "react"
import { Code2, Download, Eye } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { CopyButton } from "@/components/ui/copy-button"
import { useEvidenceSources } from "@/contexts/evidence-context"
import type { ParsedSource } from "@/lib/evidence-utils"
import { cn } from "@/lib/utils"
import { useTheme } from "next-themes"
import { useTranslations } from "next-intl"
import Markdown, { type Options as MarkdownOptions } from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import remarkCjkFriendly from "remark-cjk-friendly"
import remarkCjkFriendlyGfmStrikethrough from "remark-cjk-friendly-gfm-strikethrough"
import rehypeKatex from "rehype-katex"
import rehypeHighlight from "rehype-highlight"
import rehypeRaw from "rehype-raw"
import rehypeSanitize, { defaultSchema } from "rehype-sanitize"

/**
 * A run of adjacent citation markers: `[1]`, `[1][2]`, `[1] [2]`.  Spaces
 * between markers are absorbed so the whole run collapses into one pill, but a
 * trailing space is left alone so the pill does not swallow the next word.
 */
const CITATION_RUN = /((?:\[\d+\])(?:[  ]*\[\d+\])*)/

/**
 * Named source pill for one run of citation markers.  Shows the first source's
 * document name plus a `+N` count for the rest; the popover lists them all.
 */
function SourcePill({ indices }: { indices: number[] }) {
  const sources = useEvidenceSources()
  const t = useTranslations("playground")
  const matched = indices
    .map((i) => sources.find((s) => s.index === i))
    .filter((s): s is ParsedSource => Boolean(s))

  // No evidence context (DAG mode, replayed history) — keep the bare marker
  if (matched.length === 0) {
    return (
      <sup className="ml-0.5 inline-flex h-[1.1em] min-w-[1.1em] items-center justify-center rounded bg-primary/10 px-0.5 align-super text-[0.65em] font-medium text-primary">
        {indices.join(",")}
      </sup>
    )
  }

  const extra = matched.length - 1

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="mx-0.5 inline-flex max-w-[14rem] cursor-pointer items-center gap-1 rounded-full border border-border/60 bg-muted/50 px-2 py-px align-middle text-[0.8em] leading-normal text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <span className="truncate">{matched[0].displayName}</span>
          {extra > 0 && <span className="shrink-0 opacity-70">+{extra}</span>}
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" className="w-80 space-y-3 p-3">
        {matched.map((source) => (
          <div key={source.index} className="space-y-1">
            <div className="flex items-baseline gap-1.5">
              <span className="shrink-0 text-[10px] text-muted-foreground/60">[{source.index}]</span>
              <span className="truncate text-xs font-medium">{source.displayName}</span>
            </div>
            {source.kbName && (
              <div className="text-[10px] text-muted-foreground">{source.kbName}</div>
            )}
            {source.quote && (
              <p className="line-clamp-3 text-[11px] italic text-muted-foreground/80">&ldquo;{source.quote}&rdquo;</p>
            )}
            <div className="text-[10px] text-muted-foreground/60">
              {t("citationRelevance", { value: (source.relevance * 100).toFixed(0) })}
              {source.page != null && ` · p.${source.page}`}
            </div>
          </div>
        ))}
      </PopoverContent>
    </Popover>
  )
}

/** Replace runs of [N] citation markers in text with named source pills */
function processCitations(children: React.ReactNode): React.ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child !== "string") return child
    const parts = child.split(CITATION_RUN)
    if (parts.length === 1) return child
    return parts.map((part, i) => {
      if (!CITATION_RUN.test(part)) return part
      const indices = [...part.matchAll(/\[(\d+)\]/g)].map((m) => parseInt(m[1]))
      return indices.length > 0 ? <SourcePill key={i} indices={indices} /> : part
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

/* ---- Fenced blocks: code, mermaid diagrams, inline SVG ---- */

/** Only the tag set DOMPurify needs to keep a diagram intact, nothing scriptable. */
export const SVG_SANITIZE_CONFIG = { USE_PROFILES: { svg: true, svgFilters: true } } as const

/**
 * Mermaid options that must stay in lockstep with SVG_SANITIZE_CONFIG.
 * Exported so the regression test exercises the real values rather than a copy.
 */
export const MERMAID_INIT = {
  startOnLoad: false,
  securityLevel: "strict",
  /*
   * Draw labels as <text>, never <foreignObject>: DOMPurify lists
   * foreignObject alongside <script> as disallowed, so HTML labels are stripped
   * after layout — boxes come out correctly sized but empty.
   *
   * This key must stay top-level. mermaid resolves it as
   * `config.htmlLabels ?? config.flowchart?.htmlLabels ?? true`, and the nested
   * `flowchart.htmlLabels` form is deprecated and did not take effect.
   */
  htmlLabels: false,
} as const

/** Toolbar shared by the mermaid and SVG renderers */
const BLOCK_TOOLBAR_BUTTON =
  "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"

/**
 * Hover-revealed action group shared by every block renderer, so code blocks,
 * diagrams and tables all expose their actions the same way.  `floating` is for
 * blocks with no header bar to sit in.  Keyboard users reach the buttons via
 * focus-within, which a hover-only control would lock them out of.
 */
function BlockActions({ children, floating }: { children: React.ReactNode; floating?: boolean }) {
  return (
    <div
      className={cn(
        "flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100",
        floating &&
          "absolute right-2 top-2 z-10 rounded-md border border-border/60 bg-background/90 p-0.5 shadow-sm backdrop-blur",
      )}
    >
      {children}
    </div>
  )
}

/** Toolbar download anchor offering a text payload as a data: URI. */
function DownloadButton({
  content,
  mime,
  filename,
  title,
  label,
}: {
  content: string
  mime: string
  filename: string
  title: string
  label?: string
}) {
  return (
    <a
      href={`data:${mime};charset=utf-8,${encodeURIComponent(content)}`}
      download={filename}
      title={title}
      aria-label={title}
      className={BLOCK_TOOLBAR_BUTTON}
    >
      <Download className="h-3.5 w-3.5" />
      {label && <span>{label}</span>}
    </a>
  )
}

/**
 * File extension for downloading a fenced code block. Falls back to the fence
 * language itself so unknown-but-valid hints ("dockerfile") still round-trip.
 */
const CODE_FILE_EXTENSIONS: Record<string, string> = {
  python: "py",
  javascript: "js",
  typescript: "ts",
  bash: "sh",
  shell: "sh",
  zsh: "sh",
  yaml: "yml",
  markdown: "md",
  mermaid: "mmd",
  rust: "rs",
  ruby: "rb",
  kotlin: "kt",
  "c++": "cpp",
  csharp: "cs",
  "c#": "cs",
  text: "txt",
  plaintext: "txt",
}

function codeFileExtension(language: string | null): string {
  if (!language) return "txt"
  const ext = CODE_FILE_EXTENSIONS[language] ?? language
  return /^[\w+-]+$/.test(ext) ? ext : "txt"
}

/** Read the highlight.js language hint off the inner <code> element */
function codeLanguage(children: React.ReactNode): string | null {
  const child = React.Children.toArray(children).find(React.isValidElement) as
    | React.ReactElement<{ className?: string }>
    | undefined
  const match = child?.props?.className?.match(/language-([\w+#-]+)/)
  return match ? match[1].toLowerCase() : null
}

/**
 * Render a ```mermaid block as a diagram, falling back to the plain code block
 * whenever the source does not parse.  During streaming the source arrives a
 * few characters at a time, so every intermediate state is invalid syntax — the
 * debounce keeps us from thrashing the parser, and the fallback means a
 * half-written diagram reads as code instead of flashing an error.
 */
/**
 * Monotonic id source for mermaid renders.
 *
 * It must be unique per *attempt*, not per component: `mermaid.render(id, …)`
 * stamps that id onto the `<svg>` it returns, which we then inject into the
 * document.  Reusing the id — as a per-instance `useId()` would across the many
 * re-renders a streaming answer triggers — lets a later render find and delete
 * the SVG an earlier one already injected, corrupting a tree React believes it
 * owns.
 */
let mermaidRenderSeq = 0

function MermaidDiagram({ code, fallback }: { code: string; fallback: React.ReactNode }) {
  const t = useTranslations("playground")
  const [svg, setSvg] = useState<string | null>(null)
  const { resolvedTheme } = useTheme()
  const stageRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(async () => {
      const stage = stageRef.current
      if (!stage) return
      try {
        const [{ default: mermaid }, { default: DOMPurify }] = await Promise.all([
          import("mermaid"),
          import("dompurify"),
        ])
        mermaid.initialize({
          ...MERMAID_INIT,
          theme: resolvedTheme === "dark" ? "dark" : "default",
        })
        await mermaid.parse(code)
        const { svg: rendered } = await mermaid.render(
          `mermaid-${++mermaidRenderSeq}`,
          code,
          stage,
        )
        if (!cancelled) setSvg(DOMPurify.sanitize(rendered, SVG_SANITIZE_CONFIG))
      } catch {
        // Invalid or still-incomplete diagram source — keep showing the code
        if (!cancelled) setSvg(null)
      } finally {
        stage.innerHTML = ""
      }
    }, 150)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [code, resolvedTheme])

  /*
   * Offscreen stage mermaid measures text in.
   *
   * Passing a container is load-bearing, not a convenience: given none, mermaid
   * appends its scratch node straight to `document.body` and only removes it
   * after an await. The App Router renders <body>, so React owns those
   * children — an interloper appearing there mid-commit desynchronises React's
   * fiber tree from the DOM and the next update dies with "removeChild: the
   * node to be removed is not a child of this node".
   *
   * It must stay laid out (offscreen, not `display:none`) or getBBox reports
   * zero and every label collapses.
   */
  const stage = (
    <div
      ref={stageRef}
      aria-hidden
      className="pointer-events-none fixed left-[-10000px] top-0 w-[800px]"
    />
  )

  if (!svg)
    return (
      <>
        {stage}
        {fallback}
      </>
    )

  // Keyed so React unmounts the code-block subtree and mounts a fresh one,
  // rather than reconciling a <pre> root against this <div> root in place.
  return (
    <>
      {stage}
      <div
        key="diagram"
        className="group relative my-3 overflow-x-auto rounded-lg border border-border bg-muted/20 p-4"
      >
        <BlockActions floating>
          <DownloadButton
            content={svg}
            mime="image/svg+xml"
            filename="diagram.svg"
            title={t("downloadSvg")}
          />
          <CopyButton text={code} />
        </BlockActions>
        <div
          className="[&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
    </>
  )
}

/**
 * Render a ```svg block as an image with a source toggle.  The markdown
 * pipeline runs rehype-raw, and SVG permits <script>/<foreignObject>, so the
 * markup is sanitized before it is ever injected.
 */
function SvgFigure({ code, children }: { code: string; children: React.ReactNode }) {
  const t = useTranslations("playground")
  const [showSource, setShowSource] = useState(false)
  const [sanitized, setSanitized] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    import("dompurify").then(({ default: DOMPurify }) => {
      if (cancelled) return
      const clean = DOMPurify.sanitize(code, SVG_SANITIZE_CONFIG)
      setSanitized(clean.includes("<svg") ? clean : null)
    })
    return () => {
      cancelled = true
    }
  }, [code])

  const sourceOnly = !sanitized
  const showingSource = sourceOnly || showSource

  return (
    <div className="group my-3 max-w-full overflow-hidden rounded-lg border border-border bg-muted/20">
      <div className="flex items-center justify-between border-b border-border/60 px-3 py-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">svg</span>
        <BlockActions>
          {!sourceOnly && (
            <button
              type="button"
              onClick={() => setShowSource((v) => !v)}
              title={showSource ? t("viewPreview") : t("viewSource")}
              aria-label={showSource ? t("viewPreview") : t("viewSource")}
              className={BLOCK_TOOLBAR_BUTTON}
            >
              {showSource ? <Eye className="h-3.5 w-3.5" /> : <Code2 className="h-3.5 w-3.5" />}
            </button>
          )}
          {sanitized && (
            <DownloadButton
              content={sanitized}
              mime="image/svg+xml"
              filename="diagram.svg"
              title={t("downloadSvg")}
            />
          )}
          <CopyButton text={code} />
        </BlockActions>
      </div>
      {showingSource ? (
        <pre className="overflow-x-auto p-4 font-mono text-sm">{children}</pre>
      ) : (
        /* Model-authored SVG almost always assumes a light canvas: black text,
           no background. Pinning a light surface (and an explicit dark `color`
           for anything drawn with `currentColor`) keeps it legible in both
           themes instead of turning dark-on-dark. Dimmed slightly in dark mode
           so the panel does not glare. */
        <div
          className="overflow-x-auto bg-white p-4 text-neutral-900 dark:brightness-90 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full"
          dangerouslySetInnerHTML={{ __html: sanitized as string }}
        />
      )}
    </div>
  )
}

/** Code block with a persistent header carrying the language, copy and download */
function CodeBlock({ children, ...props }: React.ComponentProps<"pre">) {
  const tc = useTranslations("common")
  const language = codeLanguage(children)
  const source = extractText(children)
  const ext = codeFileExtension(language)
  // The BOM is what makes Excel open a CSV as UTF-8 instead of mojibake.
  const isCsv = ext === "csv"

  const plain = (
    <div key="code" className="group my-3 max-w-full overflow-hidden rounded-lg border border-border bg-muted/40">
      <div className="flex items-center justify-between border-b border-border/60 px-3 py-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          {language ?? "text"}
        </span>
        <BlockActions>
          <DownloadButton
            content={isCsv ? "﻿" + source : source}
            mime={isCsv ? "text/csv" : "text/plain"}
            filename={`snippet.${ext}`}
            title={tc("download")}
          />
          <CopyButton text={source} />
        </BlockActions>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-sm" {...props}>
        {children}
      </pre>
    </div>
  )

  if (language === "mermaid") return <MermaidDiagram code={source} fallback={plain} />
  if (language === "svg") return <SvgFigure code={source}>{children}</SvgFigure>
  return plain
}

/**
 * Sanitize schema for the raw HTML that reaches us from model output and from
 * user-uploaded markdown.
 *
 * `remark-math` marks formulas with `math-inline` / `math-display` on the
 * `<code>` element, and rehype-katex — which runs *after* this pass — needs
 * those classes to survive, so they are allowed explicitly alongside the
 * `language-*` classes rehype-highlight keys off.
 */
const sanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "details", "summary"],
  attributes: {
    ...defaultSchema.attributes,
    code: [["className", /^language-./, "math-inline", "math-display"]],
  },
}

/** Stable remark/rehype plugin arrays — allocated once at module scope */
const remarkPlugins = [remarkCjkFriendly, remarkCjkFriendlyGfmStrikethrough, remarkGfm, remarkMath]
/**
 * Order matters. `rehype-raw` turns raw HTML strings into real nodes, so the
 * sanitize pass sits immediately behind it to scrub that untrusted markup.
 * KaTeX and highlight.js run afterwards: their output is generated by us from
 * already-clean input, so it never needs to survive sanitization.
 */
const rehypePlugins: MarkdownOptions["rehypePlugins"] = [
  rehypeRaw,
  [rehypeSanitize, sanitizeSchema],
  rehypeKatex,
  rehypeHighlight,
]

/* ---- Tables: card layout, grid fallback, export ---- */

interface ComparisonTable {
  /** Data column headers (first header cell — the attribute-column title — is dropped) */
  header: React.ReactNode[]
  rows: { label: React.ReactNode; values: React.ReactNode[] }[]
}

interface TableData {
  header: string[]
  rows: string[][]
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

/** Flatten a rendered table back into plain strings for export */
function extractTableData(children: React.ReactNode): TableData | null {
  const sections = React.Children.toArray(children).filter(React.isValidElement)
  if (sections.length !== 2) return null
  const [thead, tbody] = sections
  const headRows = elementChildren(thead)
  if (headRows.length !== 1) return null
  const header = elementChildren(headRows[0]).map((c) => extractText(c.props.children).trim())
  const rows = elementChildren(tbody).map((tr) =>
    elementChildren(tr).map((c) => extractText(c.props.children).trim())
  )
  if (header.length === 0 || rows.length === 0) return null
  return { header, rows }
}

function toMarkdownTable({ header, rows }: TableData): string {
  const escape = (cell: string) => cell.replace(/\|/g, "\\|").replace(/\s*\n\s*/g, " ")
  return [
    `| ${header.map(escape).join(" | ")} |`,
    `| ${header.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${row.map(escape).join(" | ")} |`),
  ].join("\n")
}

function toCsv({ header, rows }: TableData): string {
  const escape = (cell: string) =>
    /[",\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell
  return [header, ...rows].map((row) => row.map(escape).join(",")).join("\n")
}

/** Hover-revealed export actions, positioned by the table wrapper */
function TableActions({ data }: { data: TableData }) {
  const t = useTranslations("playground")
  // A leading BOM is what makes Excel read the file as UTF-8; without it a
  // table with CJK content opens as mojibake.
  const csvHref = `data:text/csv;charset=utf-8,${encodeURIComponent("﻿" + toCsv(data))}`
  return (
    <BlockActions floating>
      <CopyButton text={() => toMarkdownTable(data)} label="MD" title={t("copyTableMarkdown")} />
      <a
        href={csvHref}
        download="table.csv"
        title={t("downloadTableCsv")}
        aria-label={t("downloadTableCsv")}
        className={BLOCK_TOOLBAR_BUTTON}
      >
        <Download className="h-3.5 w-3.5" />
        <span>CSV</span>
      </a>
    </BlockActions>
  )
}

function CardTable({ table, data }: { table: ComparisonTable; data: TableData | null }) {
  const gridCols = table.header.length === 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"
  return (
    <div className="group relative my-3 overflow-hidden rounded-xl border border-border">
      {data && <TableActions data={data} />}
      <div className={cn("grid gap-x-6 gap-y-1 border-b border-border bg-muted/40 px-4 py-3", gridCols)}>
        {table.header.map((cell, i) => (
          <div key={i} className="text-sm font-semibold">
            {cell}
          </div>
        ))}
      </div>
      <div className="divide-y divide-border/50">
        {table.rows.map((row, ri) => (
          <div key={ri} className={cn("grid gap-x-6 gap-y-3 px-4 py-3", gridCols)}>
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
      <ul className="mb-3 list-disc pl-6 last:mb-0 space-y-1 marker:text-muted-foreground" {...props}>
        {children}
      </ul>
    )
  },
  ol({ children, ...props }: React.ComponentProps<"ol">) {
    return (
      <ol className="mb-3 list-decimal pl-6 last:mb-0 space-y-1 marker:text-muted-foreground" {...props}>
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
    const data = extractTableData(children)
    const comparison = extractComparisonTable(children)
    if (comparison) return <CardTable table={comparison} data={data} />
    return (
      <div className="group relative my-3 overflow-x-auto rounded-lg border border-border">
        {data && <TableActions data={data} />}
        <table
          className="w-full border-collapse text-sm [&_tbody_tr]:transition-colors [&_tbody_tr:hover]:bg-muted/30"
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
