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
 * Multi-question requests page one question at a time behind a chip
 * navigation strip (matching Claude's question UI); a single question
 * renders directly and auto-submits on a single-select click.
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
  ChevronLeft,
  ChevronRight,
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
  /** Optional free-text addition qualifying the selection. */
  note: string
  noteOpen: boolean
}

const EMPTY_DRAFT: QuestionDraft = {
  selected: [],
  otherText: "",
  note: "",
  noteOpen: false,
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
  const [recordedNotes, setRecordedNotes] = useState<Record<
    string,
    string
  > | null>(null)
  const [now, setNow] = useState<number>(() => Date.now())
  const [drafts, setDrafts] = useState<Record<number, QuestionDraft>>({})
  const [currentIdx, setCurrentIdx] = useState(0)

  const paginated = questions.length > 1

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
          setRecordedNotes(row.notes ?? null)
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
    return drafts[idx] ?? EMPTY_DRAFT
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
    // Notes always come from the drafts, even on the auto-submit path.
    const notes: Record<string, string> = {}
    questions.forEach((q, idx) => {
      const note = draftFor(idx).note.trim()
      if (note) notes[q.question] = note
    })
    setState("submitting")
    try {
      const res = await answerUserQuestion(questionId, answers, { notes })
      setDecidedAt(res.decided_at)
      setRecordedAnswers(answers)
      setRecordedNotes(Object.keys(notes).length > 0 ? notes : null)
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
      const res = await answerUserQuestion(questionId, {}, { skip: true })
      setDecidedAt(res.decided_at)
      setState("skipped")
    } catch (err) {
      toast.error(getErrorMessage(err, tError) || t("userQuestion.errorToast"))
      setState("error")
    }
  }

  function goTo(idx: number) {
    setCurrentIdx(Math.max(0, Math.min(questions.length - 1, idx)))
  }

  function advanceFrom(idx: number) {
    if (paginated && idx < questions.length - 1) {
      goTo(idx + 1)
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

    if (q.multi_select || label === OTHER_VALUE || selected.length !== 1) {
      return
    }
    // Concrete single-select choice: one question auto-submits (mirrors
    // Claude Code); in a paginated card it advances to the next question.
    // An open or filled note suppresses auto-submit so it isn't lost.
    if (!paginated) {
      if (!draft.noteOpen && !draft.note.trim()) {
        void submit({ [q.question]: label })
      }
    } else {
      advanceFrom(idx)
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

  /** One interactive question page (options + Other input). */
  function renderInteractiveQuestion(idx: number) {
    const q = questions[idx]
    const draft = draftFor(idx)
    const otherSelected = draft.selected.includes(OTHER_VALUE)
    return (
      <div key={q.question} className="space-y-1.5">
        <div className="text-sm">{q.question}</div>
        {q.multi_select && (
          <div className="text-xs text-muted-foreground">
            {t("userQuestion.multiSelectHint")}
          </div>
        )}
        <div className="space-y-1">
          {q.options.map((opt) => {
            const isSelected = draft.selected.includes(opt.label)
            return (
              <button
                key={opt.label}
                type="button"
                disabled={state === "submitting"}
                onClick={() => toggleOption(idx, opt.label)}
                aria-pressed={isSelected}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
                  isSelected
                    ? "border-sky-400 bg-sky-100/70 dark:border-sky-500/60 dark:bg-sky-900/40"
                    : "border-border bg-background/60 hover:bg-muted/60",
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
            disabled={state === "submitting"}
            onClick={() => toggleOption(idx, OTHER_VALUE)}
            aria-pressed={otherSelected}
            className={cn(
              "flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors",
              "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
              otherSelected
                ? "border-sky-400 bg-sky-100/70 dark:border-sky-500/60 dark:bg-sky-900/40"
                : "border-border bg-background/60 hover:bg-muted/60",
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
            <span className="font-medium">{t("userQuestion.otherOption")}</span>
          </button>
          {otherSelected && (
            <Input
              autoFocus
              value={draft.otherText}
              onChange={(e) => setOtherText(idx, e.target.value)}
              placeholder={t("userQuestion.otherPlaceholder")}
              disabled={state === "submitting"}
              className="mt-1"
              onKeyDown={(e) => {
                if (e.key !== "Enter") return
                if (allAnswered) {
                  void submit()
                } else if (answerFor(idx) !== null) {
                  advanceFrom(idx)
                }
              }}
            />
          )}
        </div>

        {/* Optional note qualifying the selection ("option 1, but …"). */}
        {draft.noteOpen ? (
          <Input
            autoFocus
            value={draft.note}
            onChange={(e) =>
              setDrafts((prev) => ({
                ...prev,
                [idx]: { ...draftFor(idx), note: e.target.value },
              }))
            }
            placeholder={t("userQuestion.notePlaceholder")}
            disabled={state === "submitting"}
            className="mt-1"
            onKeyDown={(e) => {
              if (e.key === "Enter" && allAnswered) void submit()
            }}
          />
        ) : (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={state === "submitting"}
            onClick={() =>
              setDrafts((prev) => ({
                ...prev,
                [idx]: { ...draftFor(idx), noteOpen: true },
              }))
            }
            className="h-6 px-2 text-xs text-muted-foreground"
          >
            <PenLine className="mr-1 h-3 w-3" />
            {t("userQuestion.noteButton")}
          </Button>
        )}
      </div>
    )
  }

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
        <div className="ml-auto flex items-center gap-2">
          {actionable && paginated && (
            <span className="text-xs text-muted-foreground tabular-nums">
              {t("userQuestion.questionProgress", {
                current: currentIdx + 1,
                total: questions.length,
              })}
            </span>
          )}
          {statusBadge}
        </div>
      </div>

      {/* Chip navigation — paginated cards only, while actionable. */}
      {actionable && paginated && (
        <div className="flex flex-wrap items-center gap-1.5" role="tablist">
          {questions.map((q, idx) => {
            const answered = answerFor(idx) !== null
            const isCurrent = idx === currentIdx
            return (
              <button
                key={q.question}
                type="button"
                role="tab"
                aria-selected={isCurrent}
                disabled={state === "submitting"}
                onClick={() => goTo(idx)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
                  isCurrent
                    ? "border-sky-500 bg-sky-500 text-white dark:border-sky-500/80"
                    : "border-border bg-background/60 text-muted-foreground hover:bg-muted/60",
                )}
              >
                {answered && <Check className="h-3 w-3" aria-hidden />}
                {q.header || `Q${idx + 1}`}
              </button>
            )
          })}
        </div>
      )}

      {/* Body */}
      {state === "answered" ? (
        // Resolved: flat summary of every question and its recorded answer.
        <div className="space-y-2">
          {questions.map((q) => {
            const recorded = recordedAnswers?.[q.question]
            const note = recordedNotes?.[q.question]
            return (
              <div key={q.question} className="space-y-0.5">
                <div className="text-sm">{q.question}</div>
                <div className="text-sm font-medium text-emerald-700 dark:text-emerald-300">
                  {Array.isArray(recorded)
                    ? recorded.join(", ")
                    : (recorded ?? "—")}
                </div>
                {note && (
                  <div className="text-xs italic text-muted-foreground">
                    {note}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ) : mutedText ? (
        // Skipped / expired: question texts only, muted.
        <div className="space-y-1">
          {questions.map((q) => (
            <div key={q.question} className="text-sm text-muted-foreground">
              {q.question}
            </div>
          ))}
        </div>
      ) : paginated ? (
        renderInteractiveQuestion(currentIdx)
      ) : (
        renderInteractiveQuestion(0)
      )}

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
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {paginated && (
            <>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => goTo(currentIdx - 1)}
                disabled={state === "submitting" || currentIdx === 0}
                aria-label={t("userQuestion.prevButton")}
              >
                <ChevronLeft className="mr-1 h-3.5 w-3.5" />
                {t("userQuestion.prevButton")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => goTo(currentIdx + 1)}
                disabled={
                  state === "submitting" ||
                  currentIdx === questions.length - 1
                }
                aria-label={t("userQuestion.nextButton")}
              >
                {t("userQuestion.nextButton")}
                <ChevronRight className="ml-1 h-3.5 w-3.5" />
              </Button>
            </>
          )}
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
          {!allAnswered && paginated && (
            <span className="text-xs text-muted-foreground">
              {t("userQuestion.unansweredWarning")}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
