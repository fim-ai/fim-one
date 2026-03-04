"use client"

import Link from "next/link"
import { Bot, Pencil, Trash2, Globe, GlobeLock, MessageSquare } from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { AgentResponse } from "@/types/agent"

interface AgentCardProps {
  agent: AgentResponse
  onDelete: (id: string) => void
  onPublish: (id: string) => void
  onUnpublish: (id: string) => void
}

export function AgentCard({
  agent,
  onDelete,
  onPublish,
  onUnpublish,
}: AgentCardProps) {
  const isPublished = agent.status === "published"

  return (
    <div className="flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-border/80 hover:bg-accent/5">
      {/* Header: name + badges */}
      <div className="flex items-start gap-2 mb-2">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground flex items-center gap-1.5">
          {agent.icon ? (
            <span className="shrink-0 text-base leading-none">{agent.icon}</span>
          ) : (
            <Bot className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          {agent.name}
        </h3>
        <div className="flex items-center gap-1.5 shrink-0">
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

      {/* Management buttons */}
      <div className="flex items-center gap-1 -ml-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-muted-foreground hover:text-foreground"
              asChild
            >
              <Link href={`/agents/${agent.id}`}>
                <Pencil className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>Edit</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
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
            >
              {isPublished ? (
                <GlobeLock className="h-3.5 w-3.5" />
              ) : (
                <Globe className="h-3.5 w-3.5" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>{isPublished ? "Unpublish" : "Publish"}</TooltipContent>
        </Tooltip>
        <div className="flex-1" />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => onDelete(agent.id)}
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>Delete</TooltipContent>
        </Tooltip>
      </div>

      {/* Start Chat CTA — only when published */}
      {isPublished && (
        <Button
          variant="outline"
          size="sm"
          className="mt-3 w-full gap-1.5 text-xs h-7"
          asChild
        >
          <Link href={`/new?agent=${agent.id}`}>
            <MessageSquare className="h-3.5 w-3.5" />
            Start Chat
          </Link>
        </Button>
      )}
    </div>
  )
}
