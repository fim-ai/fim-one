"use client"

import { Pencil, Trash2, Globe, GlobeLock } from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { AgentResponse } from "@/types/agent"

interface AgentCardProps {
  agent: AgentResponse
  onEdit: (agent: AgentResponse) => void
  onDelete: (id: string) => void
  onPublish: (id: string) => void
  onUnpublish: (id: string) => void
}

export function AgentCard({
  agent,
  onEdit,
  onDelete,
  onPublish,
  onUnpublish,
}: AgentCardProps) {
  const isPublished = agent.status === "published"

  return (
    <div className="flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-border/80 hover:bg-accent/5">
      {/* Header: name + badges */}
      <div className="flex items-start gap-2 mb-2">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground">
          {agent.name}
        </h3>
        <div className="flex items-center gap-1.5 shrink-0">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5"
          >
            {agent.execution_mode === "react" ? "ReAct" : agent.execution_mode === "dag" ? "DAG" : agent.execution_mode}
          </Badge>
          <Badge
            variant="secondary"
            className={cn(
              "text-[10px] px-1.5 py-0 h-5",
              isPublished
                ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                : "opacity-60"
            )}
          >
            {isPublished ? "Published" : "Draft"}
          </Badge>
        </div>
      </div>

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2 mb-3">
        {agent.description || "No description"}
      </p>

      {/* Action buttons */}
      <div className="flex items-center gap-1 -ml-1">
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onEdit(agent)}
          className="text-muted-foreground hover:text-foreground"
          title="Edit"
        >
          <Pencil className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => isPublished ? onUnpublish(agent.id) : onPublish(agent.id)}
          className={cn(
            "text-muted-foreground",
            isPublished
              ? "hover:text-amber-600 dark:hover:text-amber-400"
              : "hover:text-emerald-600 dark:hover:text-emerald-400"
          )}
          title={isPublished ? "Unpublish" : "Publish"}
        >
          {isPublished ? (
            <GlobeLock className="h-3.5 w-3.5" />
          ) : (
            <Globe className="h-3.5 w-3.5" />
          )}
        </Button>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onDelete(agent.id)}
          className="text-muted-foreground hover:text-destructive"
          title="Delete"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  )
}
