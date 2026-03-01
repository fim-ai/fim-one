export interface IterationData {
  type?: string              // "tool_call" | "tool_start" | "thinking"
  iteration?: number
  displayIteration?: number
  tool_name?: string
  tool_args?: Record<string, unknown>
  reasoning?: string
  observation?: string
  error?: string
  duration?: number          // seconds
  loading?: boolean          // DAG tool_start equivalent
}
