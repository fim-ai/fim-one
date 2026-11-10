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

/**
 * Broadcast on `window` after a decision was sent, so that every count
 * badge on the page reads the count again at once.
 */
export const APPROVALS_CHANGED_EVENT = "workflow-approvals-changed"

/** Tell the count badges that the inbox changed. */
export function announceApprovalsChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(APPROVALS_CHANGED_EVENT))
}

/**
 * The requests that were on screen and are gone from a fresh read of the
 * inbox: decided on another surface, or run out. `handled` are the ones
 * this page dealt with itself (decided here, or already shown as over).
 */
export function vanishedRequests<T extends { id: string }>(
  shown: readonly T[],
  fresh: readonly { id: string }[],
  handled: ReadonlySet<string>,
): T[] {
  const open = new Set(fresh.map((item) => item.id))
  return shown.filter((item) => !open.has(item.id) && !handled.has(item.id))
}

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
