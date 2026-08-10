"use client"

/**
 * QuestionCard — interactive multiple-choice question form rendered in
 * the chat transcript when an agent emits an `awaiting_user_question`
 * SSE event (the `ask_user_question` tool).
 *
 * Event contract (keep in sync with `confirmation_sse.py::_request_to_event_payload`):
 *
 *   {
 *     "type": "awaiting_user_question",
 *     "question_id": "<uuid>",
 *     "questions": [{question, header, options: [{label, description}], multi_select}],
 *     "timeout_at": "<ISO8601 UTC>",
 *     "agent_id": "<uuid|''>",
 *     "conversation_id": "<uuid|''>"
 *   }
 *
 * State machine: pending -> submitting -> {answered|skipped|expired|error},
 * mirroring ConfirmationCard. The backend row is the source of truth —
 * the card rehydrates from GET /api/confirmations/{id} on mount.
 */
import { useEffect, useMemo, useState } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  MessageCircleQuestion,
  CheckCircle2,
  Check,
  Clock,
  Loader2,
  PenLine,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { getErrorMessage } from "@/lib/error-utils"
import {
  answerUserQuestion,
  getConfirmationStatus,
  type UserQuestion,
} from "@/lib/api/confirmations"

type CardState =
  | "pending"
  | "submitting"
  | "answered"
  | "skipped"
  | "expired"
  | "error"

export interface QuestionCardProps {
  questionId: string
  questions: UserQuestion[]
  /** ISO8601 UTC timestamp of when the request expires. */
  timeoutAt: string
}

const OTHER_VALUE = "__other__"

function formatDurationShort(ms: number): string {
  const totalSec = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  if (m === 0) return `${s}s`
  return `${m}m ${s.toString().padStart(2, "0")}s`
}

function formatTimeHHMM(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    })
  } catch {
    return iso
  }
}

interface QuestionDraft {
  /** Selected labels; may include OTHER_VALUE sentinel. */
  selected: string[]
  otherText: string
}

export function QuestionCard({
  questionId,
  questions,
  timeoutAt,
}: QuestionCardProps) {
  const t = useTranslations("playground")
  const tError = useTranslations("errors")

  const expiryMs = useMemo(() => {
    const parsed = new Date(timeoutAt).getTime()
    return Number.isFinite(parsed) ? parsed : Date.now()
  }, [timeoutAt])

  const initiallyExpired = Date.now() >= expiryMs
  const [state, setState] = useState<CardState>(
    initiallyExpired ? "expired" : "pending",
  )
  const [decidedAt, setDecidedAt] = useState<string | null>(null)
  const [recordedAnswers, setRecordedAnswers] = useState<Record<
    string,
    string | string[]
  > | null>(null)
  const [now, setNow] = useState<number>(() => Date.now())
  const [drafts, setDrafts] = useState<Record<number, QuestionDraft>>({})

  // Rehydrate from the backend on mount — same rationale as
  // ConfirmationCard: the card remounts when the chat page flips from
  // live-streaming to done-collapsed layout, losing local state.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const row = await getConfirmationStatus(questionId)
        if (cancelled) return
        if (row.status === "answered") {
          setDecidedAt(row.decided_at)
          setRecordedAnswers(row.answers ?? null)
          setState("answered")
        } else if (row.status === "dismissed" || row.status === "rejected") {
          setDecidedAt(row.decided_at)
          setState("skipped")
        } else if (row.status === "expired") {
          setState("expired")
        }
      } catch {
        // Best-effort rehydration; the local state machine still works.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [questionId])

  // Countdown tick — once per second while actionable.
  useEffect(() => {
    if (state !== "pending" && state !== "error" && state !== "submitting") {
      return
    }
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [state])

  // Client-side expiry transition.
  useEffect(() => {
    if ((state === "pending" || state === "error") && now >= expiryMs) {
      setState("expired")
    }
  }, [now, expiryMs, state])

  const remainingLabel = formatDurationShort(Math.max(0, expiryMs - now))

  function draftFor(idx: number): QuestionDraft {
    return drafts[idx] ?? { selected: [], otherText: "" }
  }

  function answerFor(idx: number): string | string[] | null {
    const q = questions[idx]
    const draft = draftFor(idx)
    const resolved = draft.selected
      .map((v) => (v === OTHER_VALUE ? draft.otherText.trim() : v))
      .filter((v) => v.length > 0)
    if (resolved.length === 0) return null
    return q.multi_select ? resolved : resolved[0]
  }

  const allAnswered = questions.every((_, idx) => answerFor(idx) !== null)

  async function submit(answersOverride?: Record<string, string | string[]>) {
    if (state === "submitting" || state === "expired") return
    const answers: Record<string, string | string[]> = answersOverride ?? {}
    if (!answersOverride) {
      questions.forEach((q, idx) => {
        const a = answerFor(idx)
        if (a !== null) answers[q.question] = a
      })
    }
    if (Object.keys(answers).length === 0) return
    setState("submitting")
    try {
      const res = await answerUserQuestion(questionId, answers)
      setDecidedAt(res.decided_at)
      setRecordedAnswers(answers)
      setState("answered")
    } catch (err) {
      toast.error(getErrorMessage(err, tError) || t("userQuestion.errorToast"))
      setState("error")
    }
  }

  async function skip() {
    if (state === "submitting" || state === "expired") return
    setState("submitting")
    try {
      const res = await answerUserQuestion(questionId, {}, true)
      setDecidedAt(res.decided_at)
      setState("skipped")
    } catch (err) {
      toast.error(getErrorMessage(err, tError) || t("userQuestion.errorToast"))
      setState("error")
    }
  }

  function toggleOption(idx: number, label: string) {
    if (state !== "pending" && state !== "error") return
    const q = questions[idx]
    const draft = draftFor(idx)
    let selected: string[]
    if (q.multi_select) {
      selected = draft.selected.includes(label)
        ? draft.selected.filter((v) => v !== label)
        : [...draft.selected, label]
    } else {
      selected = draft.selected.includes(label) ? [] : [label]
    }
    setDrafts((prev) => ({ ...prev, [idx]: { ...draft, selected } }))

    // Single question, single select, concrete option — submit right away
    // (mirrors Claude Code's auto-submit; "Other" still needs typing).
    if (
      questions.length === 1 &&
      !q.multi_select &&
      label !== OTHER_VALUE &&
      selected.length === 1
    ) {
      void submit({ [q.question]: label })
    }
  }

  function setOtherText(idx: number, text: string) {
    const draft = draftFor(idx)
    setDrafts((prev) => ({ ...prev, [idx]: { ...draft, otherText: text } }))
  }

  const actionable =
    state === "pending" || state === "submitting" || state === "error"

  const cardPalette: Record<CardState, string> = {
    pending:
      "border-sky-300/70 bg-sky-50/60 dark:border-sky-500/40 dark:bg-sky-950/30",
    submitting:
      "border-sky-300/70 bg-sky-50/60 dark:border-sky-500/40 dark:bg-sky-950/30",
    error:
      "border-sky-300/70 bg-sky-50/60 dark:border-sky-500/40 dark:bg-sky-950/30",
    answered:
      "border-emerald-300/70 bg-emerald-50/60 dark:border-emerald-500/40 dark:bg-emerald-950/30",
    skipped: "border-border bg-muted",
    expired: "border-border bg-muted",
  }

  const headerIcon = (() => {
    switch (state) {
      case "answered":
        return (
          <CheckCircle2
            className="h-4 w-4 text-emerald-600 dark:text-emerald-400"
            aria-hidden
          />
        )
      case "skipped":
      case "expired":
        return <Clock className="h-4 w-4 text-muted-foreground" aria-hidden />
      default:
        return (
          <MessageCircleQuestion
            className="h-4 w-4 text-sky-600 dark:text-sky-400"
            aria-hidden
          />
        )
    }
  })()

  const statusBadge = (() => {
    switch (state) {
      case "answered":
        return (
          <Badge
            variant="outline"
            className="border-emerald-400/60 bg-emerald-100/60 text-emerald-700 dark:border-emerald-500/40 dark:bg-emerald-950/40 dark:text-emerald-300"
          >
            {t("userQuestion.answered")}
          </Badge>
        )
      case "skipped":
        return (
          <Badge variant="outline" className="text-muted-foreground">
            {t("userQuestion.skipped")}
          </Badge>
        )
      case "expired":
        return (
          <Badge variant="outline" className="text-muted-foreground">
            {t("userQuestion.expired")}
          </Badge>
        )
      default:
        return null
    }
  })()

  const mutedText = state === "expired" || state === "skipped"

  return (
    <div
      className={cn(
        "rounded-lg border px-4 py-3 space-y-3 min-w-0 w-full",
        cardPalette[state],
      )}
      role="group"
      aria-label={t("userQuestion.title")}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        {headerIcon}
        <span
          className={cn(
            "text-sm font-medium",
            mutedText && "text-muted-foreground",
          )}
        >
          {t("userQuestion.title")}
        </span>
        <div className="ml-auto flex items-center gap-2">{statusBadge}</div>
      </div>

      {/* Questions */}
      <div className="space-y-4">
        {questions.map((q, idx) => {
          const draft = draftFor(idx)
          const otherSelected = draft.selected.includes(OTHER_VALUE)
          const recorded = recordedAnswers?.[q.question]
          return (
            <div key={q.question} className="space-y-1.5">
              <div className="flex items-baseline gap-2">
                {questions.length > 1 && (
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground shrink-0">
                    {q.header || `Q${idx + 1}`}
                  </span>
                )}
                <span
                  className={cn(
                    "text-sm",
                    mutedText && "text-muted-foreground",
                  )}
                >
                  {q.question}
                </span>
              </div>
              {q.multi_select && actionable && (
                <div className="text-xs text-muted-foreground">
                  {t("userQuestion.multiSelectHint")}
                </div>
              )}

              {/* Resolved view: show the recorded answer only. */}
              {state === "answered" && (
                <div className="text-sm font-medium text-emerald-700 dark:text-emerald-300">
                  {Array.isArray(recorded)
                    ? recorded.join(", ")
                    : (recorded ?? "—")}
                </div>
              )}

              {/* Interactive option list */}
              {state !== "answered" && (
                <div className="space-y-1">
                  {q.options.map((opt) => {
                    const isSelected = draft.selected.includes(opt.label)
                    return (
                      <button
                        key={opt.label}
                        type="button"
                        disabled={!actionable || state === "submitting"}
                        onClick={() => toggleOption(idx, opt.label)}
                        aria-pressed={isSelected}
                        className={cn(
                          "flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors",
                          "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
                          isSelected
                            ? "border-sky-400 bg-sky-100/70 dark:border-sky-500/60 dark:bg-sky-900/40"
                            : "border-border bg-background/60 hover:bg-muted/60",
                          !actionable && "opacity-60 cursor-default",
                        )}
                      >
                        <span
                          className={cn(
                            "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center border",
                            q.multi_select ? "rounded-sm" : "rounded-full",
                            isSelected
                              ? "border-sky-500 bg-sky-500 text-white"
                              : "border-muted-foreground/40",
                          )}
                          aria-hidden
                        >
                          {isSelected && <Check className="h-3 w-3" />}
                        </span>
                        <span className="min-w-0">
                          <span className="font-medium">{opt.label}</span>
                          {opt.description && (
                            <span className="block text-xs text-muted-foreground">
                              {opt.description}
                            </span>
                          )}
                        </span>
                      </button>
                    )
                  })}

                  {/* "Other" free-text choice — always offered. */}
                  <button
                    type="button"
                    disabled={!actionable || state === "submitting"}
                    onClick={() => toggleOption(idx, OTHER_VALUE)}
                    aria-pressed={otherSelected}
                    className={cn(
                      "flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors",
                      "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
                      otherSelected
                        ? "border-sky-400 bg-sky-100/70 dark:border-sky-500/60 dark:bg-sky-900/40"
                        : "border-border bg-background/60 hover:bg-muted/60",
                      !actionable && "opacity-60 cursor-default",
                    )}
                  >
                    <span
                      className={cn(
                        "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center border",
                        q.multi_select ? "rounded-sm" : "rounded-full",
                        otherSelected
                          ? "border-sky-500 bg-sky-500 text-white"
                          : "border-muted-foreground/40",
                      )}
                      aria-hidden
                    >
                      {otherSelected ? (
                        <Check className="h-3 w-3" />
                      ) : (
                        <PenLine className="h-2.5 w-2.5 text-muted-foreground" />
                      )}
                    </span>
                    <span className="font-medium">
                      {t("userQuestion.otherOption")}
                    </span>
                  </button>
                  {otherSelected && actionable && (
                    <Input
                      autoFocus
                      value={draft.otherText}
                      onChange={(e) => setOtherText(idx, e.target.value)}
                      placeholder={t("userQuestion.otherPlaceholder")}
                      disabled={state === "submitting"}
                      className="mt-1"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && allAnswered) void submit()
                      }}
                    />
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Countdown / resolved info */}
      {actionable && (
        <div
          className="text-xs text-muted-foreground tabular-nums"
          aria-live="polite"
        >
          {t("userQuestion.expiresIn", { time: remainingLabel })}
        </div>
      )}
      {state === "answered" && decidedAt && (
        <div className="text-xs text-muted-foreground">
          {t("userQuestion.answeredAt", { time: formatTimeHHMM(decidedAt) })}
        </div>
      )}
      {state === "skipped" && decidedAt && (
        <div className="text-xs text-muted-foreground">
          {t("userQuestion.skippedAt", { time: formatTimeHHMM(decidedAt) })}
        </div>
      )}
      {state === "expired" && (
        <div className="text-xs text-muted-foreground">
          {t("userQuestion.expiredHint")}
        </div>
      )}

      {/* Actions */}
      {actionable && (
        <div className="flex items-center gap-2 pt-1">
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={() => void submit()}
            disabled={state === "submitting" || !allAnswered}
            aria-label={t("userQuestion.submitButton")}
          >
            {state === "submitting" ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : null}
            {t("userQuestion.submitButton")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void skip()}
            disabled={state === "submitting"}
            aria-label={t("userQuestion.skipButton")}
            className="text-muted-foreground"
          >
            {t("userQuestion.skipButton")}
          </Button>
          {!allAnswered && questions.length > 1 && (
            <span className="text-xs text-muted-foreground">
              {t("userQuestion.unansweredWarning")}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
