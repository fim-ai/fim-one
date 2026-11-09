import { toast } from "sonner"
import { ApiError } from "./api"

/** Refusal code for a blueprint that contains a switched-off Human Review step. */
export const HUMAN_INTERVENTION_DISABLED = "human_intervention_disabled"

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type TranslatorFn = (key: string, args?: Record<string, any>) => string

/**
 * Toast for a failed workflow write (save, import, duplicate, restore).
 *
 * A refusal with a message of its own is shown as such, naming the steps at
 * fault; anything else shows `fallback`. The refusal toast has a fixed id,
 * so auto-save retries replace it instead of stacking.
 *
 * Returns whether the error was such a refusal.
 */
export function toastWorkflowWriteError(
  err: unknown,
  fallback: string | null,
  tError: TranslatorFn,
  tWorkflows: TranslatorFn,
): boolean {
  if (err instanceof ApiError && err.errorCode === HUMAN_INTERVENTION_DISABLED) {
    const nodes = err.errorArgs.nodes
    toast.error(tError(HUMAN_INTERVENTION_DISABLED), {
      id: HUMAN_INTERVENTION_DISABLED,
      description:
        typeof nodes === "string" && nodes
          ? tWorkflows("disabledStepsToRemove", { nodes })
          : undefined,
    })
    return true
  }
  if (fallback) toast.error(fallback)
  return false
}
