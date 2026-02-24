"use client"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Maximize2, Minimize2 } from "lucide-react"
import { cn } from "@/lib/utils"

interface RightSidebarProps {
  title: string
  subtitle?: string
  badge?: string | number
  expanded?: boolean
  onToggleExpand?: () => void
  children: React.ReactNode
  className?: string
}

export function RightSidebar({ title, subtitle, badge, expanded, onToggleExpand, children, className }: RightSidebarProps) {
  return (
    <div className={cn(
      "flex flex-col rounded-lg border border-border/50 bg-muted/10 overflow-hidden animate-in slide-in-from-right-2 duration-300",
      className
    )}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border/30 shrink-0">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium truncate">{title}</span>
            {badge != null && (
              <Badge variant="secondary" className="text-[10px] shrink-0">
                {badge}
              </Badge>
            )}
          </div>
          {subtitle && (
            <p className="text-[10px] text-muted-foreground mt-0.5 truncate">{subtitle}</p>
          )}
        </div>
        {onToggleExpand && (
          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleExpand}
            className="h-7 w-7 text-muted-foreground shrink-0"
            title={expanded ? "Minimize" : "Maximize"}
          >
            {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </Button>
        )}
      </div>
      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {children}
      </div>
    </div>
  )
}
