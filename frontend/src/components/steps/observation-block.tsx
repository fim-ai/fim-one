"use client"

import { CollapsibleBlock } from "./collapsible-block"

interface ObservationBlockProps {
  observation: string
  size?: "default" | "compact"
  defaultCollapsed?: boolean
}

export function ObservationBlock({
  observation,
  size = "default",
  defaultCollapsed = false,
}: ObservationBlockProps) {
  const isCompact = size === "compact"
  return (
    <div className={`rounded${isCompact ? "" : "-md"} ${isCompact ? "bg-muted/30" : "border border-border/50 bg-muted/30"} border border-border/30 ${isCompact ? "p-2" : "p-3"}`}>
      <p className={`font-medium text-muted-foreground ${isCompact ? "text-[10px] mb-0.5" : "text-xs mb-1"} uppercase tracking-wider`}>
        Observation
      </p>
      <CollapsibleBlock defaultExpanded={!defaultCollapsed}>
        <pre className="whitespace-pre-wrap text-xs text-foreground/90 font-mono leading-relaxed">
          {observation}
        </pre>
      </CollapsibleBlock>
    </div>
  )
}
