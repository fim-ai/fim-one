/**
 * Deciding a request a workflow run waits on (a Human Review step, or a
 * connector action that requires confirmation), without React.
 */
import { ApiError } from "@/lib/api"
import {
  getConfirmationStatus,
  respondToConfirmation,
  type ConfirmationDecision,
} from "@/lib/api/confirmations"

/** Refusal code for a request someone decided first, or that ran out. */
export const CONFIRMATION_ALREADY_DECIDED = "confirmation_already_decided"

/** The longest note the server takes with a decision. */
export const DECISION_NOTE_MAX_LENGTH = 2000

/** How a request ended when it was not this user's click that ended it. */
export type SettledStatus = "approved" | "rejected" | "expired" | "unknown"

export type DecisionOutcome =
  /** The decision was recorded. */
  | { outcome: "decided"; status: "approved" | "rejected" }
  /** Another surface decided first, or the request ran out. */
  | { outcome: "already"; status: SettledStatus }

const SETTLED: readonly SettledStatus[] = ["approved", "rejected", "expired"]

/** A status the server reported, as one of the ways a request ends. */
export function settledStatusOf(raw: unknown): SettledStatus {
  return SETTLED.includes(raw as SettledStatus) ? (raw as SettledStatus) : "unknown"
}

/**
 * Send a decision. A request that was already decided is an outcome, not an
 * error: the server says how it ended (`error_args.status`). Anything else
 * (not allowed, gone, network) is thrown for the caller to report.
 */
export async function decideApproval(
  id: string,
  decision: ConfirmationDecision,
  note?: string,
): Promise<DecisionOutcome> {
  try {
    const trimmed = note?.trim().slice(0, DECISION_NOTE_MAX_LENGTH)
    await respondToConfirmation(id, decision, trimmed || undefined)
    return { outcome: "decided", status: decision === "reject" ? "rejected" : "approved" }
  } catch (err) {
    if (err instanceof ApiError && err.errorCode === CONFIRMATION_ALREADY_DECIDED) {
      return { outcome: "already", status: settledStatusOf(err.errorArgs.status) }
    }
    throw err
  }
}

/**
 * How a request that left the inbox ended, read from the request itself.
 * "unknown" when it cannot be read (gone, or no longer this user's to see).
 */
export async function settledStatusOfRequest(id: string): Promise<SettledStatus> {
  try {
    const row = await getConfirmationStatus(id)
    return settledStatusOf(row.status)
  } catch {
    return "unknown"
  }
}
