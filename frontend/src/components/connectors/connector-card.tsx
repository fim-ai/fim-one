"use client"

import Link from "next/link"
import { Building2, Clock, Lock, MoreHorizontal, PackageMinus, Pencil, Plug, Trash2, Globe, GlobeLock, RotateCw, Database, Download, Copy, ShoppingBag, XCircle } from "lucide-react"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from "@/components/ui/tooltip"
import { MARKET_ORG_ID } from "@/lib/constants"
import type { ConnectorResponse } from "@/types/connector"

interface ConnectorCardProps {
  connector: ConnectorResponse
  currentUserId?: string
  onDelete: (id: string) => void
  onPublish?: (id: string) => void
  onUnpublish?: (id: string) => void
  onResubmit?: (id: string) => void
  onExport?: (id: string) => void
  onFork?: (id: string) => void
  onUninstall?: (id: string) => void
}

const AUTH_LABELS: Record<string, string> = {
  none: "noAuth",
  bearer: "Bearer",
  api_key: "API Key",
  basic: "Basic",
  oauth2: "OAuth2",
}

export function ConnectorCard({
  connector,
  currentUserId,
  onDelete,
  onPublish,
  onUnpublish,
  onResubmit,
  onExport,
  onFork,
  onUninstall,
}: ConnectorCardProps) {
  const t = useTranslations("connectors")
  const tc = useTranslations("common")
  const to = useTranslations("organizations")

  const isDatabase = connector.type === "database"
  const authLabel = AUTH_LABELS[connector.auth_type]
  const authDisplay = authLabel === "noAuth" ? t("noAuth") : (authLabel || connector.auth_type)
  const isOwner = currentUserId ? connector.user_id === currentUserId : true
  const isOrgResource = connector.visibility === "org" || connector.visibility === "global"
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const source = (connector as any).source as string | undefined
  const isFromMarket = source === "market"
  const isFromOrg = source === "org"
  const isSubscribed = isFromMarket || isFromOrg

  // For database connectors, show host:port/database
  const dbConfig = connector.db_config
  const dbEndpoint = dbConfig ? `${dbConfig.host}:${dbConfig.port}/${dbConfig.database}` : ""

  return (
    <div className="group flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-ring/40 hover:bg-accent/10">

      {/* Header: name + hover menu */}
      <div className="flex items-center gap-2 mb-1.5">
        <h3 className="flex-1 min-w-0 text-sm font-medium truncate text-card-foreground flex items-center gap-1.5">
          {connector.icon ? (
            <span className="shrink-0 text-base leading-none">{connector.icon}</span>
          ) : (
            <Plug className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          {connector.name}
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
                <Link href={`/connectors/${connector.id}`}>
                  <Pencil className="h-4 w-4" />
                  {tc("edit")}
                </Link>
              </DropdownMenuItem>
              {onExport && (
                <DropdownMenuItem onClick={() => onExport(connector.id)}>
                  <Download className="h-4 w-4" />
                  {t("exportConnector")}
                </DropdownMenuItem>
              )}
              {onFork && (
                <DropdownMenuItem onClick={() => onFork(connector.id)}>
                  <Copy className="h-4 w-4" />
                  {t("forkConnector")}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              {/* Publish / Unpublish */}
              {onPublish && onUnpublish && (
                <DropdownMenuItem
                  onClick={() => isOrgResource ? onUnpublish(connector.id) : onPublish(connector.id)}
                >
                  {isOrgResource
                    ? <GlobeLock className="h-4 w-4" />
                    : <Globe className="h-4 w-4" />
                  }
                  {isOrgResource ? tc("unpublish") : tc("publish")}
                </DropdownMenuItem>
              )}
              {/* Resubmit -- only when rejected */}
              {onResubmit && connector.publish_status === "rejected" && (
                <DropdownMenuItem onClick={() => onResubmit(connector.id)}>
                  <RotateCw className="h-4 w-4" />
                  {t("resubmit")}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => onDelete(connector.id)}>
                <Trash2 className="h-4 w-4" />
                {tc("delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : !isOwner && isSubscribed && onUninstall ? (
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
              <DropdownMenuItem variant="destructive" onClick={() => onUninstall(connector.id)}>
                <PackageMinus className="h-4 w-4" />
                {tc("uninstall")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>

      {/* Subscriber badge — Market */}
      {isFromMarket && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
          >
            <ShoppingBag className="h-2.5 w-2.5 mr-0.5" />
            {tc("subscribedMarket")}
          </Badge>
        </div>
      )}
      {/* Subscriber badge — Organization */}
      {isFromOrg && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
          >
            <Building2 className="h-2.5 w-2.5 mr-0.5" />
            {tc("subscribedOrg")}
          </Badge>
        </div>
      )}

      {/* Owner visibility badge — Market */}
      {isOwner && isOrgResource && connector.org_id === MARKET_ORG_ID && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
          >
            <ShoppingBag className="h-2.5 w-2.5 mr-0.5" />
            {tc("publishedMarket")}
          </Badge>
        </div>
      )}

      {/* Owner visibility badge — Organization */}
      {isOwner && isOrgResource && connector.org_id && connector.org_id !== MARKET_ORG_ID && (
        <div className="flex items-center gap-1.5 mb-1.5">
          <Badge
            variant="secondary"
            className="text-[10px] px-1.5 py-0 h-5 bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
          >
            <Building2 className="h-2.5 w-2.5 mr-0.5" />
            {tc("publishedOrg")}
          </Badge>
        </div>
      )}

      {/* Publish review status badges -- owner only */}
      {isOwner && (connector.publish_status === "pending_review" || connector.publish_status === "rejected") && (
        <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
          {connector.publish_status === "pending_review" && (
            <Badge
              variant="secondary"
              className="text-[10px] px-1.5 py-0 h-5 bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20"
            >
              <Clock className="h-2.5 w-2.5 mr-0.5" />
              {to("publishStatusPending")}
            </Badge>
          )}
          {connector.publish_status === "rejected" && (
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
                {connector.review_note && (
                  <TooltipContent>
                    <p>{to("rejectedNote", { note: connector.review_note })}</p>
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
      )}

      {/* Type badge */}
      <div className="flex items-center gap-1.5 mb-2">
        <span className={`text-[10px] px-1.5 py-0 h-5 inline-flex items-center rounded-full font-medium ${
          isDatabase
            ? "bg-blue-500/10 text-blue-500"
            : "bg-amber-500/10 text-amber-500"
        }`}>
          {isDatabase ? t("typeBadgeDatabase") : t("typeBadgeApi")}
        </span>
        {isOwner && connector.auth_type !== "none" && connector.allow_fallback === false && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="text-[10px] px-1.5 py-0 h-5 inline-flex items-center gap-1 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 font-medium cursor-default">
                  <Lock className="h-3 w-3" />
                  {t("privateDefaultBadge")}
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom" sideOffset={5} className="max-w-sm whitespace-normal break-words leading-relaxed">
                {t("privateDefaultHelp")}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
        {isDatabase && dbConfig && (
          <span className="text-[10px] px-1.5 py-0 h-5 inline-flex items-center rounded-full bg-muted text-muted-foreground font-medium">
            {dbConfig.driver}
          </span>
        )}
      </div>

      {/* Info line: auth/actions for API, driver info for DB — owner only */}
      {isOwner && (
        isDatabase ? (
          <p className="text-xs text-muted-foreground mb-1">
            {dbConfig?.driver ?? "database"}
            {dbConfig?.read_only && " \u00B7 read-only"}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground mb-1">
            {authDisplay}
            {" \u00B7 "}
            {t("actionCount", { count: connector.actions.length })}
          </p>
        )
      )}

      {/* Endpoint — owner only (black box for non-owners) */}
      {isOwner && (
        <Tooltip>
          <TooltipTrigger asChild>
            <p className="text-xs text-muted-foreground truncate mb-1">
              {isDatabase ? (
                <>
                  <Database className="inline h-3 w-3 mr-1 -mt-0.5" />
                  {dbEndpoint}
                </>
              ) : (
                <>
                  <Globe className="inline h-3 w-3 mr-1 -mt-0.5" />
                  {connector.base_url}
                </>
              )}
            </p>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>
            {isDatabase ? dbEndpoint : connector.base_url}
          </TooltipContent>
        </Tooltip>
      )}

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2">
        {connector.description || t("noDescription")}
      </p>
    </div>
  )
}
