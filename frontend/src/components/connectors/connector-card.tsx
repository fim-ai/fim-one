"use client"

import Link from "next/link"
import { MoreHorizontal, Pencil, Plug, Trash2, Globe, Database } from "lucide-react"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { ConnectorResponse } from "@/types/connector"

interface ConnectorCardProps {
  connector: ConnectorResponse
  onDelete: (id: string) => void
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
  onDelete,
}: ConnectorCardProps) {
  const t = useTranslations("connectors")
  const tc = useTranslations("common")

  const isDatabase = connector.type === "database"
  const authLabel = AUTH_LABELS[connector.auth_type]
  const authDisplay = authLabel === "noAuth" ? t("noAuth") : (authLabel || connector.auth_type)

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
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={() => onDelete(connector.id)}>
              <Trash2 className="h-4 w-4" />
              {tc("delete")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Type badge */}
      <div className="flex items-center gap-1.5 mb-2">
        <span className={`text-[10px] px-1.5 py-0 h-5 inline-flex items-center rounded-full font-medium ${
          isDatabase
            ? "bg-blue-500/10 text-blue-500"
            : "bg-amber-500/10 text-amber-500"
        }`}>
          {isDatabase ? t("typeBadgeDatabase") : t("typeBadgeApi")}
        </span>
        {isDatabase && dbConfig && (
          <span className="text-[10px] px-1.5 py-0 h-5 inline-flex items-center rounded-full bg-muted text-muted-foreground font-medium">
            {dbConfig.driver}
          </span>
        )}
      </div>

      {/* Info line: auth/actions for API, driver info for DB */}
      {isDatabase ? (
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
      )}

      {/* Endpoint */}
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

      {/* Description */}
      <p className="flex-1 text-xs text-muted-foreground line-clamp-2">
        {connector.description || t("noDescription")}
      </p>
    </div>
  )
}
