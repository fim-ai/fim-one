/**
 * What the run panel's input form sends as a run's inputs.
 */
import { ApiError } from "@/lib/api"
import type { StartNodeData } from "@/types/workflow"

type StartVariables = NonNullable<StartNodeData["variables"]>

/** Refusal codes of a run whose inputs do not fit the Start step. */
export const INPUT_REFUSALS: readonly string[] = ["workflow_input_required", "workflow_input_invalid"]

export interface RunInputs {
  /** The inputs to send. */
  inputs: Record<string, unknown>
  /** What each field held, to fill the form again for "Run Again". */
  effectiveStrings: Record<string, string>
}

/**
 * The inputs for a run, from what was typed into the form.
 *
 * With the Start step's "check inputs" on, the server checks and converts:
 * what was typed is sent as text and an empty field is left out (the server
 * then uses the default, or refuses a required input). With it off the form
 * converts as it always has: a number field becomes a number (0 when
 * empty), a boolean field is true only for "true", everything else is text,
 * and an empty field takes the variable's default.
 */
export function runInputsOf(
  variables: StartVariables,
  typed: Record<string, string>,
  serverChecks: boolean,
): RunInputs {
  const inputs: Record<string, unknown> = {}
  const effectiveStrings: Record<string, string> = {}
  for (const v of variables) {
    if (serverChecks) {
      const text = typed[v.name] ?? ""
      effectiveStrings[v.name] = text
      if (text.trim() !== "") inputs[v.name] = text
      continue
    }
    const raw = (typed[v.name] ?? v.default_value ?? "") as string
    effectiveStrings[v.name] = raw
    if (v.type === "number") {
      inputs[v.name] = raw ? Number(raw) : 0
    } else if (v.type === "boolean") {
      inputs[v.name] = raw === "true"
    } else {
      inputs[v.name] = raw
    }
  }
  return { inputs, effectiveStrings }
}

/**
 * The input a refused run names (`error_args.input`), or null when the
 * failure is not about an input.
 */
export function refusedInputOf(err: unknown): string | null {
  if (!(err instanceof ApiError) || !err.errorCode || !INPUT_REFUSALS.includes(err.errorCode)) {
    return null
  }
  const input = err.errorArgs.input
  return typeof input === "string" && input ? input : null
}

/** The 1-based row of a batch a refusal is about (`error_args.index`), or null. */
export function refusedRowOf(err: unknown): number | null {
  if (!(err instanceof ApiError) || !err.errorCode || !INPUT_REFUSALS.includes(err.errorCode)) {
    return null
  }
  const index = err.errorArgs.index
  return typeof index === "number" && Number.isInteger(index) && index >= 0 ? index + 1 : null
}
