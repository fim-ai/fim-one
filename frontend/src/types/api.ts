// ReAct events
export interface ReactStepEvent {
  type: "thinking" | "tool_call" | "tool_start" | "final_answer"
  iteration: number
  tool_name?: string
  tool_args?: Record<string, unknown>
  reasoning?: string
  observation?: string
  error?: string
  /** Server-side iteration elapsed (LLM thinking + tool execution). */
  iter_elapsed?: number
}

export interface ReactDoneEvent {
  answer: string
  iterations: number
  elapsed: number
  /** Server-side elapsed for the final iteration (LLM thinking → answer). */
  iter_elapsed?: number
}

// DAG events
export interface DagPhaseEvent {
  name: "planning" | "executing" | "analyzing"
  status: "start" | "done"
  steps?: Array<{
    id: string
    task: string
    deps: string[]
    tool_hint?: string
  }>
  results?: Array<{
    id: string
    task: string
    status: string
    result?: string
    started_at?: number
    completed_at?: number
    duration?: number
  }>
  achieved?: boolean
  confidence?: number
  reasoning?: string
}

export interface DagStepProgressEvent {
  step_id: string
  event: "started" | "iteration" | "completed"
  task?: string
  status?: string
  result?: string
  duration?: number
  started_at?: number
  // iteration fields
  type?: string
  iteration?: number
  tool_name?: string
  tool_args?: Record<string, unknown>
  reasoning?: string
  observation?: string
  error?: string
}

export interface DagDoneEvent {
  answer: string
  achieved: boolean
  confidence: number
  elapsed: number
}
