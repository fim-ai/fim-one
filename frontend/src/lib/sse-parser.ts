/**
 * Incremental Server-Sent Events frame parser.
 *
 * A network read can split an SSE frame at any byte — mid-line, or between
 * a frame's `event:` and `data:` lines. Both the partial line and the
 * partly-built frame therefore have to survive across chunks. Keeping that
 * state per-consumer is what let the live and resume paths drift apart:
 * one hoisted the frame state correctly and the other re-initialised it on
 * every chunk, silently relabelling split frames as `"message"`.
 *
 * This is the single implementation both consume.
 */

/** A decoded frame: its event name and the raw (unparsed) data line. */
export interface SseFrame {
  event: string
  data: string
}

/** Result of unwrapping a payload that may carry a resume cursor. */
export interface UnwrappedPayload {
  cursor?: number
  data: unknown
}

export interface SseParser {
  /** Feed a chunk; returns every frame completed by it. */
  push: (chunk: string) => SseFrame[]
  /**
   * Force-terminate a trailing frame that never received its blank line.
   * Returns it when one was buffered, otherwise an empty array.
   */
  flush: () => SseFrame[]
}

/**
 * Create a stateful parser. One instance per stream — never share it, and
 * never recreate it per chunk.
 */
export function createSseParser(): SseParser {
  let partialLine = ""
  let event = "message"
  let data = ""

  const consume = (lines: string[]): SseFrame[] => {
    const frames: SseFrame[] = []
    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim()
      } else if (line.startsWith("data:")) {
        data = line.slice(5).trim()
      } else if (line === "") {
        if (data) {
          frames.push({ event, data })
        }
        event = "message"
        data = ""
      }
    }
    return frames
  }

  return {
    push(chunk: string): SseFrame[] {
      const lines = (partialLine + chunk).split("\n")
      // The final element is either "" (chunk ended on a newline) or a
      // partial line; either way it is not yet complete.
      partialLine = lines.pop() ?? ""
      return consume(lines)
    },
    flush(): SseFrame[] {
      const lines = partialLine ? [partialLine, ""] : [""]
      partialLine = ""
      return consume(lines)
    },
  }
}

/**
 * Unwrap a frame payload that may be wrapped as ``{cursor, data}``.
 *
 * Both the live stream and ``/chat/resume`` send this envelope so a client
 * tracks its position identically on either path. A bare payload (no
 * numeric ``cursor``) is returned untouched.
 *
 * @throws SyntaxError when *raw* is not valid JSON.
 */
export function unwrapPayload(raw: string): UnwrappedPayload {
  const parsed: unknown = JSON.parse(raw)
  if (parsed && typeof parsed === "object") {
    const maybe = parsed as { cursor?: unknown; data?: unknown }
    if (typeof maybe.cursor === "number") {
      return {
        cursor: maybe.cursor,
        data: "data" in maybe ? maybe.data : parsed,
      }
    }
  }
  return { data: parsed }
}
