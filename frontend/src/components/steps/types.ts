export interface ArtifactInfo {
  name: string
  url: string
  mime_type: string
  size: number
  /** Content hash — used to hide step artifacts that duplicate a deliverable. */
  sha256?: string
}

export interface IterationData {
  type?: string              // "iteration" | "thinking" | "answer"
  iteration?: number
  displayIteration?: number
  tool_name?: string
  tool_args?: Record<string, unknown>
  reasoning?: string
  observation?: string
  error?: string
  duration?: number          // seconds
  loading?: boolean          // true when tool is executing
  content_type?: string      // "text" | "html" | "markdown" | "json"
  artifacts?: ArtifactInfo[]
  /** Thinking text for interleaved __thinking__ entries. */
  thinkingText?: string
}
