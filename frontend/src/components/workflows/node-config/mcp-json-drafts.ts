/**
 * What was typed into an MCP step's "Arguments as JSON" editor and is not a
 * JSON object yet.
 *
 * Such text cannot be stored in the step (its `parameters` stay the last
 * valid object), and the form is mounted anew each time a step is selected.
 * Without this, selecting another step dropped the text without a word.
 * It is kept here per step for as long as the page is open, and shown
 * again, with its error, when the step is selected again.
 */

export interface JsonDraft {
  /** The text typed. */
  text: string
  /** The step's `parameters` (as JSON) the text was typed against. */
  over: string
}

const kept = new Map<string, JsonDraft>()

export function keptJsonDraft(nodeId: string): JsonDraft | null {
  return kept.get(nodeId) ?? null
}

/** Keep `draft` for the step, or forget what was kept with `null`. */
export function keepJsonDraft(nodeId: string, draft: JsonDraft | null): void {
  if (draft === null) kept.delete(nodeId)
  else kept.set(nodeId, draft)
}

export function forgetJsonDrafts(): void {
  kept.clear()
}
