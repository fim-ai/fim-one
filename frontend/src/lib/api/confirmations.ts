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
