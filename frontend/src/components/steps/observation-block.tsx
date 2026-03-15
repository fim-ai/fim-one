"use client"

import { useMemo } from "react"
import { useTranslations } from "next-intl"
import { MarkdownContent } from "@/lib/markdown"
import { parseInsufficientEvidence } from "@/lib/evidence-utils"
import type { ArtifactInfo } from "./types"
import { ArtifactChips } from "./artifact-chips"
import { InsufficientEvidenceBlock } from "./insufficient-evidence-block"

interface ObservationBlockProps {
  observation: string
  size?: "default" | "compact"
  hideLabel?: boolean
  contentType?: string
  artifacts?: ArtifactInfo[]
}

const DEFAULT_MD_CLS = "text-xs [&_pre]:my-0 [&_pre]:p-0 [&_pre]:bg-transparent [&_pre]:rounded-none"
const COMPACT_MD_CLS = "text-[11px] [&_pre]:my-0 [&_pre]:p-2"

/**
 * Detect JSON-like content and return { text, lang }.
 * - Valid JSON → pretty-printed + lang "json"
 * - Looks like JSON but truncated/invalid → raw + lang "json" (still gets highlighting)
 * - Not JSON → raw + lang null (plain text)
 */
function formatObservation(raw: string): { text: string; lang: string | null } {
  const trimmed = raw.trim()
  const looksLikeJson =
    (trimmed.startsWith("{") || trimmed.startsWith("[")) &&
    /[}\]"]/.test(trimmed.slice(0, 20))

  if (!looksLikeJson) return { text: raw, lang: null }

  try {
    const pretty = JSON.stringify(JSON.parse(trimmed), null, 2)
    return { text: pretty, lang: "json" }
  } catch {
    // Truncated or invalid JSON — still wrap as json for syntax highlighting
    return { text: raw, lang: "json" }
  }
}

export function ObservationBlock({
  observation,
  size = "default",
  hideLabel = false,
  contentType,
  artifacts,
}: ObservationBlockProps) {
  const t = useTranslations("dag")
  const isCompact = size === "compact"
  const mdCls = isCompact ? COMPACT_MD_CLS : DEFAULT_MD_CLS

  const { text, lang } = useMemo(() => formatObservation(observation), [observation])

  const insufficientEvidence = useMemo(() => parseInsufficientEvidence(observation), [observation])

  if (insufficientEvidence) {
    return <InsufficientEvidenceBlock data={insufficientEvidence} />
  }

  // Explicit contentType overrides auto-detection
  const effectiveType = contentType || (lang === "json" ? "json" : "text")

  return (
    <div className={`rounded${isCompact ? "" : "-md"} border border-border/30 ${isCompact ? "bg-muted/30 p-2" : "border-border/50 bg-muted/30 p-3"}`}>
      {!hideLabel && (
        <p className={`font-medium text-muted-foreground ${isCompact ? "text-[10px] mb-0.5" : "text-xs mb-1"} uppercase tracking-wider`}>
          {t("output")}
        </p>
      )}
      {effectiveType === "html" ? (
        <iframe
          sandbox="allow-scripts"
          srcDoc={observation}
          className="w-full rounded border border-border/20 bg-white"
          style={{ height: "300px" }}
          title={t("htmlPreview")}
        />
      ) : effectiveType === "markdown" ? (
        <MarkdownContent
          content={observation}
          className={mdCls}
        />
      ) : effectiveType === "json" ? (
        <MarkdownContent
          content={`\`\`\`json\n${text}\n\`\`\``}
          className={mdCls}
        />
      ) : (
        <pre className="whitespace-pre-wrap break-all text-xs text-foreground/90 font-mono leading-relaxed overflow-x-auto">
          {observation}
        </pre>
      )}
      {artifacts && artifacts.length > 0 && (
        <div className="mt-2">
          <ArtifactChips artifacts={artifacts} />
        </div>
      )}
    </div>
  )
}
