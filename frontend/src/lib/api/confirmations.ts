/**
 * Confirmations API client.
 *
 * Wraps the backend endpoint `POST /api/confirmations/{id}/respond`
 * used by the inline chat approval card. The backend contract is
 * frozen (see Phase 1 Task #3):
 *
 *   Request:  { decision: "approve" | "reject", reason?: string }
 *   Response: { status: "approved" | "rejected", decided_at: ISO8601 }
 */
import { apiFetch } from "@/lib/api"

export type ConfirmationDecision = "approve" | "reject"

export interface ConfirmationResponseBody {
  status: "approved" | "rejected"
  decided_at: string
}

export async function respondToConfirmation(
  id: string,
  decision: ConfirmationDecision,
  reason?: string,
): Promise<ConfirmationResponseBody> {
  const body: Record<string, unknown> = { decision }
  if (reason !== undefined && reason !== "") {
    body.reason = reason
  }
  return apiFetch<ConfirmationResponseBody>(
    `/api/confirmations/${id}/respond`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  )
}

export type ConfirmationStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "expired"
  | "answered"
  | "dismissed"

export interface ConfirmationStatusBody {
  confirmation_id: string
  status: ConfirmationStatus
  mode: "inline" | "channel"
  kind?: "confirmation" | "user_question"
  tool_name: string
  arguments: Record<string, unknown>
  created_at: string
  decided_at: string | null
  approver_user_id: string | null
  /** kind="user_question" only. */
  questions?: UserQuestion[] | null
  answers?: Record<string, string | string[]> | null
}

export async function getConfirmationStatus(
  id: string,
): Promise<ConfirmationStatusBody> {
  return apiFetch<ConfirmationStatusBody>(`/api/confirmations/${id}`, {
    method: "GET",
  })
}

/* ------------------------------------------------------------------ */
/*  ask_user_question — structured question requests                   */
/* ------------------------------------------------------------------ */

export interface UserQuestionOption {
  label: string
  description: string
}

export interface UserQuestion {
  question: string
  header: string
  options: UserQuestionOption[]
  multi_select: boolean
}

export interface QuestionAnswerResponseBody {
  status: "answered" | "dismissed"
  confirmation_id: string
  decided_at: string
}

/**
 * Answer a pending `kind=user_question` request. `answers` maps each
 * question's text to the selected label, a list of labels (multi-select),
 * or the user's free "Other" text. Pass `skip=true` to dismiss instead.
 */
export async function answerUserQuestion(
  id: string,
  answers: Record<string, string | string[]>,
  skip = false,
): Promise<QuestionAnswerResponseBody> {
  return apiFetch<QuestionAnswerResponseBody>(
    `/api/confirmations/${id}/answer`,
    {
      method: "POST",
      body: JSON.stringify(skip ? { answers: {}, skip: true } : { answers }),
    },
  )
}
