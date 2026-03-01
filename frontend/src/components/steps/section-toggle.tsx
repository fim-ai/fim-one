"use client"

import { useState } from "react"
import { ChevronDown, ChevronUp } from "lucide-react"
import { cn } from "@/lib/utils"

interface SectionToggleProps {
  label: string
  labelClass?: string
  children: React.ReactNode
  defaultOpen?: boolean
}

/** Collapsible section wrapper — click header to expand/collapse. */
export function SectionToggle({
  label,
  labelClass,
  children,
  defaultOpen = false,
}: SectionToggleProps) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className="rounded bg-muted/30 border border-border/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left hover:bg-muted/40 transition-colors"
      >
        <p className={cn("text-[9px] font-medium uppercase tracking-wider flex-1", labelClass ?? "text-muted-foreground")}>
          {label}
        </p>
        {open
          ? <ChevronUp className="h-2.5 w-2.5 text-muted-foreground shrink-0" />
          : <ChevronDown className="h-2.5 w-2.5 text-muted-foreground shrink-0" />
        }
      </button>
      {open && (
        <div className="px-2 pb-2">
          {children}
        </div>
      )}
    </div>
  )
}
