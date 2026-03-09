"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { ChevronRight, ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

interface CollapsibleTextProps {
  content: string
  threshold?: number
  className?: string
}

const baseClass = "text-sm text-foreground whitespace-pre-wrap"

export function CollapsibleText({
  content,
  threshold = 500,
  className,
}: CollapsibleTextProps) {
  const t = useTranslations("playground")
  const [expanded, setExpanded] = useState(false)

  const isLong = content.length > threshold
  const textClass = className ?? baseClass

  if (!isLong) {
    return <p className={textClass}>{content}</p>
  }

  return (
    <div className="rounded-lg border border-border/40 bg-muted/20 overflow-hidden">
      {/* Full-width clickable bar */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-2.5 cursor-pointer hover:bg-muted/40 transition-colors text-xs text-muted-foreground"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        )}
        <span className="truncate flex-1 text-left">
          {expanded ? t("showLessText") : t("showMoreText", { chars: content.length.toLocaleString() })}
        </span>
      </button>

      {/* Content — expanded */}
      {expanded && (
        <div className="px-4 pb-3 max-h-[400px] overflow-y-auto">
          <p className={cn(textClass, "break-words")}>{content}</p>
        </div>
      )}
    </div>
  )
}
