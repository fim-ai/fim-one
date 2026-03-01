"use client"

import { useState, useRef, useLayoutEffect } from "react"
import { ChevronDown, ChevronUp } from "lucide-react"

const DEFAULT_MAX_HEIGHT = 60

interface CollapsibleBlockProps {
  children: React.ReactNode
  maxHeight?: number
  defaultExpanded?: boolean
}

export function CollapsibleBlock({
  children,
  maxHeight = DEFAULT_MAX_HEIGHT,
  defaultExpanded = true,
}: CollapsibleBlockProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [overflows, setOverflows] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (el) setOverflows(el.scrollHeight > maxHeight)
  }, [children, maxHeight])

  if (!overflows) {
    return <div ref={ref}>{children}</div>
  }

  return (
    <div>
      <div
        ref={ref}
        style={!expanded ? { maxHeight } : undefined}
        className={!expanded ? "overflow-hidden" : undefined}
      >
        {children}
      </div>
      {!expanded && (
        <div className="h-6 -mt-6 bg-gradient-to-t from-muted/80 to-transparent pointer-events-none relative z-[1]" />
      )}
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex items-center gap-1 mt-1 text-[10px] text-amber-400 hover:text-amber-300 transition-colors"
      >
        {expanded ? (
          <>
            <ChevronUp className="h-3 w-3" />
            Collapse
          </>
        ) : (
          <>
            <ChevronDown className="h-3 w-3" />
            Show more
          </>
        )}
      </button>
    </div>
  )
}
