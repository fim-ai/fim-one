"use client"

import { MarkdownContent } from "@/lib/markdown"

interface ToolArgsBlockProps {
  args: Record<string, unknown>
  size?: "default" | "compact"
  hideLabel?: boolean
  className?: string
}

const DEFAULT_MD_CLS = "text-xs [&_pre]:my-0 [&_pre]:p-0 [&_pre]:bg-transparent [&_pre]:rounded-none"
const COMPACT_MD_CLS = "text-[11px] [&_pre]:my-0 [&_pre]:p-2"

export function ToolArgsBlock({
  args,
  size = "default",
  hideLabel = false,
  className,
}: ToolArgsBlockProps) {
  const isCompact = size === "compact"
  const mdCls = isCompact ? COMPACT_MD_CLS : DEFAULT_MD_CLS
  const containerCls = isCompact
    ? "rounded bg-muted/30 border border-border/30 p-2"
    : "rounded-md border border-border/50 bg-muted/30 p-3"
  const labelCls = isCompact
    ? "text-[10px] font-medium text-muted-foreground mb-0.5 uppercase tracking-wider"
    : "text-xs font-medium text-muted-foreground mb-1 uppercase tracking-wider"

  if (typeof args.code === "string") {
    const rest = { ...args }
    delete rest.code
    const hasRest = Object.keys(rest).length > 0
    return (
      <div className={`${containerCls} ${className ?? ""}`}>
        {!hideLabel && <p className={labelCls}>Arguments</p>}
        <MarkdownContent
          content={`\`\`\`python\n${args.code}\n\`\`\``}
          className={mdCls}
        />
        {hasRest && (
          <div className={isCompact ? "mt-1" : "mt-2"}>
            <MarkdownContent
              content={`\`\`\`json\n${JSON.stringify(rest, null, 2)}\n\`\`\``}
              className={mdCls}
            />
          </div>
        )}
      </div>
    )
  }

  return (
    <div className={`${containerCls} ${className ?? ""}`}>
      {!hideLabel && <p className={labelCls}>Arguments</p>}
      <MarkdownContent
        content={`\`\`\`json\n${JSON.stringify(args, null, 2)}\n\`\`\``}
        className={mdCls}
      />
    </div>
  )
}
