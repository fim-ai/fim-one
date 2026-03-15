"use client"

import { AlertTriangle } from "lucide-react"
import { useTranslations } from "next-intl"
import type { InsufficientEvidence } from "@/lib/evidence-utils"

interface InsufficientEvidenceBlockProps {
  data: InsufficientEvidence
}

export function InsufficientEvidenceBlock({ data }: InsufficientEvidenceBlockProps) {
  const t = useTranslations("playground")

  return (
    <div className="rounded-md border border-yellow-200 dark:border-yellow-800/50 bg-yellow-50/50 dark:bg-yellow-900/10 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-yellow-600 dark:text-yellow-500 shrink-0" />
        <span className="text-sm font-medium text-yellow-800 dark:text-yellow-300">
          {t("evidenceInsufficient")}
        </span>
      </div>
      <p className="text-xs text-yellow-700 dark:text-yellow-400">
        {t("evidenceInsufficientDetail", {
          confidence: data.confidence,
          threshold: data.threshold,
        })}
      </p>
      <p className="text-[11px] text-yellow-600/80 dark:text-yellow-500/70">
        {t("evidenceInsufficientHint")}
      </p>
    </div>
  )
}
