"use client"

import { useTranslations } from "next-intl"
import { AlertTriangle } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const KNOWN_CODES = new Set([
  "token_quota_exceeded",
  "subscription_required",
  "quota_unavailable",
])

/** The message key for a document read without the vision model under *code*. */
export function visionNoticeKey(code: string): string {
  return `visionNotice.${KNOWN_CODES.has(code) ? code : "other"}`
}

/**
 * A warning icon on a document that was read as text only because the quota
 * check refused the vision model; the reason shows on hover.
 */
export function VisionNotice({ code }: { code: string }) {
  const t = useTranslations("kb")
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          role="img"
          aria-label={t("visionNoticeLabel")}
          className="inline-flex items-center text-amber-600 dark:text-amber-400 cursor-help"
        >
          <AlertTriangle className="h-3.5 w-3.5" />
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{t(visionNoticeKey(code))}</TooltipContent>
    </Tooltip>
  )
}
