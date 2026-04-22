"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"
import { ArrowRight, Bot, Building2, Clock, Copy, MoreHorizontal, PackageMinus, Pencil, Trash2, Globe, GlobeLock, MessageSquare, RotateCw, ShoppingBag, XCircle } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { MARKET_ORG_ID } from "@/lib/constants"
import type { AgentResponse } from "@/types/agent"

interface AgentCardProps {
  agent: AgentResponse
  currentUserId?: string
  onDelete: (id: string) => void
  onPublish: (id: string) => void
  onUnpublish: (id: string) => void
  onFork?: (id: string) => void
  onUninstall?: (id: string) => void
  onResubmit?: (id: string) => void
}

export function AgentCard({
  agent,
  currentUserId,
  onDelete,
  onPublish,
  onUnpublish,
  onFork,
  onUninstall,
  onResubmit,
}: AgentCardProps) {
  const t = useTranslations("agents")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const isOwner = !currentUserId || agent.user_id === currentUserId
  const isOrgResource = agent.visibility !== "personal"
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const source = (agent as any).source as string | undefined
  const isFromMarket = source === "market"
  const isFromOrg = source === "org"
  const isSubscribed = isFromMarket || isFromOrg

  return (
    <div className="group flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-ring/40 hover:bg-accent/10">
      {/* Header: name + hover menu */}
      <div className="flex items-center gap-2 mb-1.5">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground flex items-center gap-1.5">
          {agent.icon ? (
            <span className="shrink-0 text-base leading-none">{agent.icon}</span>
          ) : (
            <Bot className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          {agent.name}
        </h3>
        {isOwner ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0 text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 transition-opacity"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                <Link href={`/new?agent=${agent.id}`}>
                  <MessageSquare className="h-4 w-4" />
                  {t("startChat")}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href={`/agents/${agent.id}`}>
                  <Pencil className="h-4 w-4" />
                  {tc("edit")}
                </Link>
              </DropdownMenuItem>
              {onFork && (
                <DropdownMenuItem onClick={() => onFork(agent.id)}>
                  <Copy className="h-4 w-4" />
                  {t("forkAgent")}
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => isOrgResource ? onUnpublish(agent.id) : onPublish(agent.id)}>
                {isOrgResource ? <GlobeLock className="h-4 w-4" /> : <Globe className="h-4 w-4" />}
                {isOrgResource ? tc("unpublish") : tc("publish")}
              </DropdownMenuItem>
              {agent.publish_status === "rejected" && onResubmit && (
                <DropdownMenuItem onClick={() => onResubmit(agent.id)}>
                  <RotateCw className="h-4 w-4" />
                  {to("resubmit")}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => onDelete(agent.id)}>
                <Trash2 className="h-4 w-4" />
                {tc("delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : isSubscribed && onUninstall ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0 text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 transition-opacity"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                <Link href={`/new?agent=${agent.id}`}>
                  <MessageSquare className="h-4 w-4" />
                  {t("startChat")}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => onUninstall(agent.id)}>
                <PackageMinus className="h-4 w-4" />
                {tc("uninstall")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>

      {/* Status badges */}
      <div className="flex items-center gap-1.5 mb-2 flex-wrap">
        {isFromMarket && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
          >
            <ShoppingBag className="h-2.5 w-2.5 mr-0.5" />
            {tc("subscribedMarket")}
          </Badge>
        )}
        {isFromOrg && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
          >
            <Building2 className="h-2.5 w-2.5 mr-0.5" />
            {tc("subscribedOrg")}
          </Badge>
        )}

        {/* Owner visibility badge — Market */}
        {isOwner && isOrgResource && agent.org_id === MARKET_ORG_ID && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
          >
            <ShoppingBag className="h-2.5 w-2.5 mr-0.5" />
            {tc("publishedMarket")}
          </Badge>
        )}

        {/* Owner visibility badge — Organization */}
        {isOwner && isOrgResource && agent.org_id && agent.org_id !== MARKET_ORG_ID && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
          >
            <Building2 className="h-2.5 w-2.5 mr-0.5" />
            {tc("publishedOrg")}
          </Badge>
        )}

        {/* Publish review status badges */}
        {isOwner && agent.publish_status === "pending_review" && (
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20"
          >
            <Clock className="h-2.5 w-2.5 mr-0.5" />
            {to("publishStatusPending")}
          </Badge>
        )}
        {isOwner && agent.publish_status === "rejected" && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant="secondary"
                  className="text-[10px] px-1.5 py-0 h-5 bg-red-500/10 text-red-500 dark:text-red-400 border-red-500/20 cursor-default"
                >
                  <XCircle className="h-2.5 w-2.5 mr-0.5" />
                  {to("publishStatusRejected")}
                </Badge>
              </TooltipTrigger>
              {agent.review_note && (
                <TooltipContent>
                  <p>{to("rejectedNote", { note: agent.review_note })}</p>
                </TooltipContent>
              )}
            </Tooltip>
          </TooltipProvider>
        )}
      </div>

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2 mb-3">
        {agent.description || t("noDescription")}
      </p>

      <div className="mt-auto flex flex-col gap-2 sm:flex-row sm:justify-end">
        <Button
          asChild
          size="sm"
          className="gap-1.5 opacity-100 md:translate-y-1 md:opacity-0 md:group-hover:translate-y-0 md:group-hover:opacity-100 md:group-focus-within:translate-y-0 md:group-focus-within:opacity-100"
        >
          <Link href={`/new?agent=${agent.id}`}>
            <MessageSquare className="h-3.5 w-3.5" />
            {t("startChat")}
          </Link>
        </Button>
        <Button
          asChild
          size="sm"
          variant="outline"
          className="gap-1.5 opacity-100 md:translate-y-1 md:opacity-0 md:group-hover:translate-y-0 md:group-hover:opacity-100 md:group-focus-within:translate-y-0 md:group-focus-within:opacity-100"
        >
          <Link href={`/agents/${agent.id}`}>
            {tc("edit")}
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </Button>
      </div>

    </div>
  )
}
