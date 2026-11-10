"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { Check, Loader2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { DECISION_NOTE_MAX_LENGTH } from "@/lib/workflow-approvals"
import type { ConfirmationDecision } from "@/lib/api/confirmations"

interface DecisionControlsProps {
  /** A decision is being sent: both buttons are off until it is answered. */
  busy: boolean
  onDecide: (decision: ConfirmationDecision, note: string) => void
  /** Smaller controls, for the run panel. */
  compact?: boolean
}

/** Approve / Reject with an optional note. */
export function DecisionControls({ busy, onDecide, compact = false }: DecisionControlsProps) {
  const t = useTranslations("workflows")
  const [note, setNote] = useState("")

  return (
    <div className="space-y-2">
      <Textarea
        aria-label={t("approvalNoteLabel")}
        placeholder={t("approvalNotePlaceholder")}
        className={compact ? "min-h-[52px] text-xs" : "min-h-[60px] text-sm"}
        rows={2}
        maxLength={DECISION_NOTE_MAX_LENGTH}
        value={note}
        disabled={busy}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          className={compact ? "h-7 gap-1 text-xs" : "gap-1.5"}
          disabled={busy}
          onClick={() => onDecide("approve", note)}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
          {t("approvalApprove")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className={compact ? "h-7 gap-1 text-xs" : "gap-1.5"}
          disabled={busy}
          onClick={() => onDecide("reject", note)}
        >
          <X className="h-3.5 w-3.5" />
          {t("approvalReject")}
        </Button>
      </div>
    </div>
  )
}
