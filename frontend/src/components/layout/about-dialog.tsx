"use client"

import { useEffect, useState } from "react"
import { useTranslations } from "next-intl"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { APP_NAME } from "@/lib/constants"
import { apiFetch } from "@/lib/api"
import { useDateFormatter } from "@/hooks/use-date-formatter"

interface VersionInfo {
  version: string
  build_time: string
  app_name: string
}

interface ActiveModelInfo {
  role: string
  model_name: string
  context_size: number
  max_output_tokens: number
}

interface ActiveModelsResponse {
  models: ActiveModelInfo[]
  source: string
  group_name: string | null
}

interface AboutDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const ROLE_I18N_MAP: Record<string, string> = {
  general: "aboutModelGeneral",
  fast: "aboutModelFast",
  reasoning: "aboutModelReasoning",
}

function formatTokenCount(n: number): string {
  return (n / 1000).toFixed(0) + "K"
}

export function AboutDialog({ open, onOpenChange }: AboutDialogProps) {
  const t = useTranslations("common")
  const { formatDateTime } = useDateFormatter()
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null)
  const [activeModels, setActiveModels] = useState<ActiveModelsResponse | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    Promise.all([
      apiFetch<VersionInfo>("/api/version").catch(() => null),
      apiFetch<ActiveModelsResponse>("/api/active-models").catch(() => null),
    ])
      .then(([version, models]) => {
        setVersionInfo(version)
        setActiveModels(models)
      })
      .finally(() => setLoading(false))
  }, [open])

  const formatBuildTime = (iso: string) => formatDateTime(iso, iso)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader className="items-center text-center">
          <DialogTitle className="text-2xl font-bold">{APP_NAME}</DialogTitle>
          <DialogDescription>{t("aboutTagline")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          {loading ? (
            <div className="space-y-3">
              <div className="flex justify-between">
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-4 w-24" />
              </div>
              <div className="flex justify-between">
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-4 w-32" />
              </div>
            </div>
          ) : versionInfo ? (
            <>
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t("aboutVersion")}</span>
                <span className="font-medium">{versionInfo.version}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t("aboutBuildTime")}</span>
                <span className="font-medium">{formatBuildTime(versionInfo.build_time)}</span>
              </div>
            </>
          ) : null}
        </div>

        {/* Active Models Section */}
        {loading ? (
          <div className="space-y-3 border-t pt-4">
            <Skeleton className="h-4 w-24" />
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="space-y-1">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-48" />
                </div>
              ))}
            </div>
          </div>
        ) : activeModels ? (
          <div className="space-y-3 border-t pt-4">
            <h4 className="text-sm font-medium">{t("aboutActiveModels")}</h4>
            <div className="space-y-3">
              {activeModels.models.map((model) => (
                <div key={model.role}>
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm text-muted-foreground">
                      {t(ROLE_I18N_MAP[model.role] ?? model.role)}
                    </span>
                    <span className="truncate font-mono text-xs">{model.model_name}</span>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {formatTokenCount(model.context_size)} {t("aboutContext").toLowerCase()}
                    {" · "}
                    {formatTokenCount(model.max_output_tokens)} {t("aboutMaxOutput").toLowerCase()}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="space-y-1 pt-2 text-center text-xs text-muted-foreground">
          <p>{t("aboutCraftedBy")}</p>
          <p>{t("aboutCopyright", { year: new Date().getFullYear() })}</p>
        </div>

        <DialogFooter className="sm:justify-center">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
