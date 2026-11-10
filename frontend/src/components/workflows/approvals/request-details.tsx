"use client"

import { useMemo, useState } from "react"
import { useTranslations } from "next-intl"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ApprovalInboxItem } from "@/types/workflow"

/** Arguments longer than this (as pretty JSON) start collapsed. */
const LONG_ARGUMENTS = 240

/**
 * How many characters of a request's arguments are not shown until they are
 * expanded; 0 when everything is in view. A request with hidden arguments
 * cannot be approved before they have been opened.
 */
export function hiddenArgumentCharacters(
  item: Pick<ApprovalInboxItem, "kind" | "arguments">,
): number {
  if (item.kind !== "confirmation" || !item.arguments) return 0
  const length = JSON.stringify(item.arguments, null, 2).length
  return length > LONG_ARGUMENTS ? length : 0
}

/**
 * What a request asks: the message of a Human Review step, or the action a
 * connector step is about to call and what it is called with.
 */
export function RequestDetails({
  item,
  compact = false,
  onExpanded,
}: {
  item: Pick<ApprovalInboxItem, "kind" | "title" | "description" | "action" | "arguments">
  compact?: boolean
  /** The collapsed arguments were opened (once is enough to have seen them). */
  onExpanded?: () => void
}) {
  const t = useTranslations("workflows")
  const json = useMemo(
    () => (item.arguments ? JSON.stringify(item.arguments, null, 2) : ""),
    [item.arguments],
  )
  const isLong = json.length > LONG_ARGUMENTS
  const [expanded, setExpanded] = useState(false)
  const text = compact ? "text-xs" : "text-sm"

  if (item.kind !== "confirmation") {
    return (
      <div className="space-y-1">
        {item.title && <p className={cn(text, "font-medium text-foreground")}>{item.title}</p>}
        {item.description && (
          <p className={cn(text, "text-muted-foreground whitespace-pre-wrap break-words")}>
            {item.description}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      <p className={text}>
        <span className="text-muted-foreground">{t("approvalAction")}: </span>
        <span className="font-mono font-medium text-foreground break-all">
          {item.action || item.title}
        </span>
      </p>
      {json && (
        <div>
          <button
            type="button"
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            aria-expanded={!isLong || expanded}
            disabled={!isLong}
            onClick={() => {
              if (!expanded) onExpanded?.()
              setExpanded((v) => !v)
            }}
          >
            {isLong && (
              <ChevronDown
                className={cn("h-3 w-3 transition-transform duration-200", !expanded && "-rotate-90")}
              />
            )}
            {t("approvalArguments")}
          </button>
          {(!isLong || expanded) && (
            <pre className="mt-1 max-h-[240px] overflow-auto rounded-md border border-border bg-muted/30 p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
              {json}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
