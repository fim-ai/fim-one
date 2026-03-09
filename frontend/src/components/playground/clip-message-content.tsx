"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { FileText, ChevronDown, ChevronUp } from "lucide-react"

export interface ClipMetadataItem {
  content: string
  preview: string
  charCount: number
}

export interface ClipMessageMetadata {
  clips: ClipMetadataItem[]
  userQuery: string
}

interface ClipMessageContentProps {
  metadata: ClipMessageMetadata
}

/**
 * Renders a user message that contains clip metadata: clip cards (expandable) + user query text.
 * Used for both history messages and pending (in-flight) messages.
 */
export function ClipMessageContent({ metadata }: ClipMessageContentProps) {
  const t = useTranslations("playground")
  const [expandedIndices, setExpandedIndices] = useState<Set<number>>(new Set())

  const toggleExpand = (index: number) => {
    setExpandedIndices((prev) => {
      const next = new Set(prev)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }

  return (
    <div className="space-y-2">
      {/* Clip cards */}
      {metadata.clips.map((clip, index) => {
        const isExpanded = expandedIndices.has(index)
        return (
          <div
            key={index}
            className="rounded-lg border border-border/60 bg-muted/50 text-xs overflow-hidden"
          >
            <button
              type="button"
              onClick={() => toggleExpand(index)}
              className="flex w-full items-center gap-2 px-3 py-2 cursor-pointer hover:bg-muted/80 transition-colors text-left"
              aria-label={isExpanded ? t("collapseClip") : t("expandClip")}
            >
              <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="flex-1 min-w-0 truncate text-foreground">{clip.preview}</span>
              <span className="shrink-0 text-muted-foreground">
                ({clip.charCount.toLocaleString()} {t("chars")})
              </span>
              {isExpanded ? (
                <ChevronUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              )}
            </button>
            {isExpanded && (
              <div className="border-t border-border/40 bg-muted px-3 py-2 max-h-[200px] overflow-y-auto">
                <pre className="whitespace-pre-wrap break-words font-mono text-xs text-foreground/80">
                  {clip.content}
                </pre>
              </div>
            )}
          </div>
        )
      })}

      {/* User query text */}
      {metadata.userQuery && (
        <p className="text-sm text-foreground whitespace-pre-wrap">{metadata.userQuery}</p>
      )}
    </div>
  )
}
