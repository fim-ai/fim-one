"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { ChevronDown, ChevronUp } from "lucide-react"
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
    <div>
      {expanded ? (
        <div className="max-h-[400px] overflow-y-auto">
          <p className={cn(textClass, "break-words")}>{content}</p>
        </div>
      ) : (
        <div className="relative">
          <p className={cn(textClass, "line-clamp-3")}>{content}</p>
          <div className="absolute inset-x-0 bottom-0 h-6 bg-gradient-to-t from-background to-transparent pointer-events-none" />
        </div>
      )}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 mt-1 cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        {expanded ? (
          <>
            <ChevronUp className="h-3 w-3" />
            {t("showLessText")}
          </>
        ) : (
          <>
            <ChevronDown className="h-3 w-3" />
            {t("showMoreText", { chars: content.length.toLocaleString() })}
          </>
        )}
      </button>
    </div>
  )
}
