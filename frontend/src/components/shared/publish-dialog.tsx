"use client"

import { useState, useEffect } from "react"
import { Loader2, Clock, Store, Building2, Library, Bot, Database, Server, Info } from "lucide-react"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { marketApi } from "@/lib/api"
import type { UserOrg, DependencyManifest } from "@/lib/api"
import { MARKET_ORG_ID } from "@/lib/constants"

type PublishTarget = "organization" | "marketplace"

interface PublishDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  orgs: UserOrg[]
  orgsLoading: boolean
  selectedOrgId: string
  onOrgChange: (id: string) => void
  /** Whether the selected org requires publish review */
  requiresReview: boolean
  /** Read-only: current fallback status, shown as informational text when defined */
  allowFallback?: boolean
  fallbackLabel?: string
  noOrgsText: string
  selectOrgPlaceholder: string
  onConfirm: () => void
  /** When true, hide the marketplace target and show org-only mode */
  hideMarketplace?: boolean
  /** Resource type being published (for dependency analysis) */
  resourceType?: string
  /** Resource ID being published (for dependency analysis) */
  resourceId?: string
}

export function PublishDialog({
  open,
  onOpenChange,
  title,
  description,
  orgs,
  orgsLoading,
  selectedOrgId,
  onOrgChange,
  requiresReview,
  allowFallback,
  fallbackLabel,
  noOrgsText,
  selectOrgPlaceholder,
  onConfirm,
  hideMarketplace,
  resourceType,
  resourceId,
}: PublishDialogProps) {
  const tc = useTranslations("common")
  const to = useTranslations("organizations")
  const tm = useTranslations("market")

  const [publishTarget, setPublishTarget] = useState<PublishTarget>("organization")
  const [deps, setDeps] = useState<DependencyManifest | null>(null)

  // When switching to marketplace, set org_id to MARKET_ORG_ID
  // When switching back, reset to first org or empty
  // Filter out the Market org — it's the global marketplace, not a user org
  const filteredOrgs = orgs.filter((o) => o.id !== MARKET_ORG_ID)

  useEffect(() => {
    if (publishTarget === "marketplace") {
      onOrgChange(MARKET_ORG_ID)
    } else {
      // Reset to first user org when switching back
      if (filteredOrgs.length > 0 && selectedOrgId === MARKET_ORG_ID) {
        onOrgChange(filteredOrgs[0].id)
      }
    }
  }, [publishTarget]) // eslint-disable-line react-hooks/exhaustive-deps

  // Reset target when dialog opens
  useEffect(() => {
    if (open) {
      setPublishTarget("organization")
    }
  }, [open])

  // Fetch dependencies for Solution types
  useEffect(() => {
    if (open && resourceType && resourceId && ['agent', 'skill', 'workflow'].includes(resourceType)) {
      marketApi.dependencies({ resource_type: resourceType, resource_id: resourceId })
        .then(res => setDeps(res.data))
        .catch(() => setDeps(null))
    } else {
      setDeps(null)
    }
  }, [open, resourceType, resourceId])

  const isMarketplace = publishTarget === "marketplace"
  const effectiveRequiresReview = isMarketplace ? true : requiresReview
  const canConfirm = isMarketplace
    ? true
    : (!orgsLoading && filteredOrgs.length > 0 && !!selectedOrgId)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2">
          {/* Publish target selector */}
          {!hideMarketplace && (
            <div className="space-y-2">
              <Label className="text-sm font-medium">{tm("publishTarget")}</Label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setPublishTarget("organization")}
                  className={`flex items-center gap-2 rounded-md border p-2.5 text-sm transition-colors ${
                    !isMarketplace
                      ? "border-primary bg-primary/5 text-primary"
                      : "border-border text-muted-foreground hover:bg-muted/50"
                  }`}
                >
                  <Building2 className="h-4 w-4 shrink-0" />
                  {tm("publishTargetOrg")}
                </button>
                <button
                  type="button"
                  onClick={() => setPublishTarget("marketplace")}
                  className={`flex items-center gap-2 rounded-md border p-2.5 text-sm transition-colors ${
                    isMarketplace
                      ? "border-primary bg-primary/5 text-primary"
                      : "border-border text-muted-foreground hover:bg-muted/50"
                  }`}
                >
                  <Store className="h-4 w-4 shrink-0" />
                  {tm("publishTargetMarketplace")}
                </button>
              </div>
            </div>
          )}

          <div className="space-y-2">
            {isMarketplace ? (
              /* Marketplace selected — no org dropdown, always requires review */
              <div className="flex items-center gap-2 text-sm text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 p-2 rounded-md">
                <Clock className="h-4 w-4 shrink-0" />
                <span>{tm("marketplaceReviewRequired")}</span>
              </div>
            ) : (
              /* Organization selected — show org dropdown */
              <>
                {orgsLoading ? (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  </div>
                ) : filteredOrgs.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{noOrgsText}</p>
                ) : (
                  <>
                    <Select value={selectedOrgId} onValueChange={onOrgChange}>
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder={selectOrgPlaceholder} />
                      </SelectTrigger>
                      <SelectContent>
                        {filteredOrgs.map((org) => (
                          <SelectItem key={org.id} value={org.id}>{org.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    {/* Review notice */}
                    {effectiveRequiresReview && (
                      <div className="flex items-center gap-2 text-sm text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 p-2 rounded-md">
                        <Clock className="h-4 w-4 shrink-0" />
                        <span>{to("publishRequiresReview")}</span>
                      </div>
                    )}
                  </>
                )}
              </>
            )}

            {/* allow_fallback read-only status — shown for Component types (connector, mcp_server) */}
            {allowFallback !== undefined && (
              <div className="flex items-center gap-2 rounded-md bg-muted/50 p-2.5 text-xs text-muted-foreground">
                <Info className="h-3.5 w-3.5 shrink-0" />
                <p>
                  {fallbackLabel}: <span className="font-medium">{allowFallback ? tc("enabled") : tc("disabled")}</span>
                </p>
              </div>
            )}
          </div>
          {/* Dependency preview -- per-item rows with type icons */}
          {deps && (deps.content_deps.length > 0 || deps.connection_deps.length > 0) && (
            <div className="space-y-1.5 border-t pt-3">
              <p className="text-xs font-medium text-muted-foreground">{tm("dependenciesLabel")}</p>
              {deps.content_deps.map((d) => (
                <div key={`${d.resource_type}:${d.resource_id}`} className="flex items-center gap-2 text-sm text-muted-foreground bg-muted/50 border border-border/50 px-2.5 py-1.5 rounded-md">
                  {d.resource_type === "agent" ? <Bot className="h-3.5 w-3.5 shrink-0" /> : <Library className="h-3.5 w-3.5 shrink-0" />}
                  <span>{tm("depIncluded", { name: d.resource_name })}</span>
                </div>
              ))}
              {deps.connection_deps.map((d) => (
                <div
                  key={`${d.resource_type}:${d.resource_id}`}
                  className={`flex items-center gap-2 text-sm px-2.5 py-1.5 rounded-md ${
                    d.allow_fallback
                      ? "text-muted-foreground bg-muted/50 border border-border/50"
                      : "text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20"
                  }`}
                >
                  {d.resource_type === "mcp_server" ? <Server className="h-3.5 w-3.5 shrink-0" /> : <Database className="h-3.5 w-3.5 shrink-0" />}
                  <span>{d.allow_fallback ? tm("depIncluded", { name: d.resource_name }) : tm("depRequiresSetup", { name: d.resource_name })}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" className="px-6" onClick={() => onOpenChange(false)}>{tc("cancel")}</Button>
          <Button
            className="px-6"
            onClick={onConfirm}
            disabled={!canConfirm}
          >
            {tc("publish")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
