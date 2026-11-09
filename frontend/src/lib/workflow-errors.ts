import { toast } from "sonner"
import { ApiError } from "./api"
import { getErrorMessage, translateErrorCode, type TranslatorFn } from "./error-utils"

/** Refusal code for a blueprint that contains a switched-off Human Review step. */
export const HUMAN_INTERVENTION_DISABLED = "human_intervention_disabled"
/** Refusal code for a blueprint with invalid step settings. */
export const BLUEPRINT_SCHEMA_INVALID = "blueprint_schema_invalid"

/** How many invalid fields a toast lists before summarising the rest. */
const MAX_LISTED_FIELDS = 3

/** One invalid field, as `error_args.errors` of a 422 carries it. */
export interface BlueprintFieldError {
  /** Path in the blueprint, e.g. `nodes.3.data.timeout_hours`. */
  loc: string
  node_id: string | null
  message: string
}

export interface WorkflowErrorText {
  title: string
  /** Detail lines under the title: the steps and fields at fault. */
  lines: string[]
  /** The backend's error code, when the failure carried one. */
  code: string | null
}

/** The entries of `error_args.errors` that are well-formed. */
export function blueprintFieldErrors(args: Record<string, unknown>): BlueprintFieldError[] {
  const raw = args.errors
  if (!Array.isArray(raw)) return []
  return raw.flatMap((entry) => {
    if (typeof entry !== "object" || entry === null) return []
    const { loc, node_id, message } = entry as Record<string, unknown>
    if (typeof loc !== "string" || typeof message !== "string") return []
    return [{ loc, node_id: typeof node_id === "string" ? node_id : null, message }]
  })
}

/**
 * The field a `loc` points at inside its step: `nodes.3.data.timeout_hours`
 * is `timeout_hours`, `nodes.3.type` is `type`. A `loc` outside a step
 * (`edges.0.source`) is returned whole.
 */
export function fieldOfLoc(loc: string): string {
  const inNode = /^nodes\.\d+\.(.+)$/.exec(loc)
  if (!inNode) return loc
  return inNode[1].replace(/^data\./, "")
}

/**
 * One line per invalid field: the step's name, the field, what is wrong.
 * `nodeLabel` turns a step id into the name shown on the canvas; without it
 * (an import has no canvas) the id stands in. Lists at most
 * `MAX_LISTED_FIELDS` fields, then how many more there are.
 */
export function formatBlueprintFieldErrors(
  errors: BlueprintFieldError[],
  tWorkflows: TranslatorFn,
  nodeLabel?: (nodeId: string) => string | undefined,
): string[] {
  const lines = errors.slice(0, MAX_LISTED_FIELDS).map((e) => {
    if (!e.node_id) return tWorkflows("invalidFieldOutsideStep", { field: e.loc, message: e.message })
    return tWorkflows("invalidFieldInStep", {
      step: nodeLabel?.(e.node_id) || e.node_id,
      field: fieldOfLoc(e.loc),
      message: e.message,
    })
  })
  const rest = errors.length - MAX_LISTED_FIELDS
  if (rest > 0) lines.push(tWorkflows("invalidFieldsMore", { count: rest }))
  return lines
}

/**
 * What to tell the user about a failed workflow action (save, import,
 * duplicate, restore, run).
 *
 * A failure the backend gave a code is described by that code's message,
 * with the steps and fields at fault listed where the response names them.
 * A failure without a code (network, 5xx) is described by `fallback`;
 * `null` when there is no code and no fallback.
 */
export function describeWorkflowError(
  err: unknown,
  fallback: string | null,
  tError: TranslatorFn,
  tWorkflows: TranslatorFn,
  nodeLabel?: (nodeId: string) => string | undefined,
): WorkflowErrorText | null {
  if (!(err instanceof ApiError) || !err.errorCode) {
    return fallback ? { title: fallback, lines: [], code: null } : null
  }
  const code = err.errorCode
  if (code === HUMAN_INTERVENTION_DISABLED) {
    const nodes = err.errorArgs.nodes
    return {
      // The message takes no arguments; the steps go on a line of their own.
      title: translateErrorCode(code, tError) ?? err.message,
      lines:
        typeof nodes === "string" && nodes
          ? [tWorkflows("disabledStepsToRemove", { nodes })]
          : [],
      code,
    }
  }
  if (code === BLUEPRINT_SCHEMA_INVALID) {
    return {
      title: translateErrorCode(code, tError) ?? err.message,
      lines: formatBlueprintFieldErrors(blueprintFieldErrors(err.errorArgs), tWorkflows, nodeLabel),
      code,
    }
  }
  return { title: getErrorMessage(err, tError), lines: [], code }
}

/**
 * Toast for a failed workflow action; see {@link describeWorkflowError}.
 *
 * A coded failure's toast has the code as its id, so repeats (auto-save
 * retrying a refused save) replace it instead of stacking, and it stays up
 * longer when it lists steps to fix.
 *
 * Returns whether anything was shown.
 */
export function toastWorkflowError(
  err: unknown,
  fallback: string | null,
  tError: TranslatorFn,
  tWorkflows: TranslatorFn,
  nodeLabel?: (nodeId: string) => string | undefined,
): boolean {
  const text = describeWorkflowError(err, fallback, tError, tWorkflows, nodeLabel)
  if (!text) return false
  if (!text.code) {
    toast.error(text.title)
    return true
  }
  toast.error(text.title, {
    id: text.code,
    description: text.lines.length > 0 ? text.lines.join("\n") : undefined,
    descriptionClassName: "whitespace-pre-line",
    duration: text.lines.length > 0 ? 10000 : undefined,
  })
  return true
}
