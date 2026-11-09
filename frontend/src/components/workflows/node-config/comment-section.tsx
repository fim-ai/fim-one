"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { ChevronDown, MessageSquare } from "lucide-react"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

// --- Comment / annotation section ---

export function CommentSection({
  comment,
  onChange,
}: {
  comment: string
  onChange: (value: string) => void
}) {
  const t = useTranslations("workflows")
  const [expanded, setExpanded] = useState(false)

  const firstLine = comment ? comment.split("\n")[0] : ""
  const hasComment = comment.length > 0

  return (
    <>
      <Separator className="my-1" />
      <button
        type="button"
        className="flex items-center justify-between w-full text-xs font-medium text-muted-foreground hover:text-foreground transition-colors py-1"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="flex items-center gap-1.5">
          <MessageSquare className="h-3 w-3" />
          {t("configCommentSection")}
        </span>
        <span className="flex items-center gap-1.5">
          {!expanded && hasComment && (
            <span className="text-[10px] text-muted-foreground/70 truncate max-w-[120px]">
              {firstLine}
            </span>
          )}
          <ChevronDown
            className={cn(
              "h-3 w-3 transition-transform duration-200",
              expanded && "rotate-180",
            )}
          />
        </span>
      </button>
      {expanded && (
        <div className="pb-1">
          <Textarea
            value={comment}
            onChange={(e) => onChange(e.target.value)}
            placeholder={t("configCommentPlaceholder")}
            className="min-h-[60px] text-xs resize-none"
            rows={3}
            onInput={(e) => {
              const target = e.target as HTMLTextAreaElement
              target.style.height = "auto"
              target.style.height = `${target.scrollHeight}px`
            }}
          />
        </div>
      )}
    </>
  )
}
