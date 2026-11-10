"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"
import { UserCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useModuleFlags } from "@/lib/module-flags"
import { useApprovalCount } from "./use-approval-count"

/**
 * Link to the approval inbox with the number of requests that wait for the
 * signed-in user. Not shown when Human Review steps are switched off on
 * this instance and nothing waits; connector actions that require
 * confirmation can wait even then, so a count above 0 always shows it.
 */
export function ApprovalsLink({ iconOnly = false }: { iconOnly?: boolean }) {
  const t = useTranslations("workflows")
  const { humanIntervention } = useModuleFlags()
  const { count } = useApprovalCount()

  if (!humanIntervention && count === 0) return null

  return (
    <Button
      asChild
      variant={iconOnly ? "ghost" : "outline"}
      size="sm"
      className="gap-1.5"
      data-testid="approvals-link"
    >
      <Link href="/workflows/approvals" aria-label={iconOnly ? t("approvalsButton") : undefined}>
        <UserCheck className="h-3.5 w-3.5" />
        {!iconOnly && t("approvalsButton")}
        {count > 0 && (
          <span
            data-testid="approvals-count"
            aria-label={t("approvalsPendingCount", { count })}
            className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium leading-none text-primary-foreground tabular-nums"
          >
            {count > 99 ? "99+" : count}
          </span>
        )}
      </Link>
    </Button>
  )
}
