import { describe, it, expect } from "vitest"
import { createSseParser, unwrapPayload } from "@/lib/sse-parser"

describe("createSseParser", () => {
  it("parses a whole frame delivered in one chunk", () => {
    const p = createSseParser()
    expect(p.push('event: step\ndata: {"a":1}\n\n')).toEqual([
      { event: "step", data: '{"a":1}' },
    ])
  })

  it("parses several frames in one chunk", () => {
    const p = createSseParser()
    const frames = p.push('event: a\ndata: 1\n\nevent: b\ndata: 2\n\n')
    expect(frames.map((f) => f.event)).toEqual(["a", "b"])
  })

  it("keeps the event name when the split lands between event and data", () => {
    // The regression this parser exists to prevent: state reset per chunk
    // relabelled such a frame as "message".
    const p = createSseParser()
    expect(p.push("event: step\n")).toEqual([])
    expect(p.push('data: {"a":1}\n\n')).toEqual([
      { event: "step", data: '{"a":1}' },
    ])
  })

  it("survives a split in the middle of a line", () => {
    const p = createSseParser()
    expect(p.push("event: st")).toEqual([])
    expect(p.push("ep\ndata: 42\n\n")).toEqual([{ event: "step", data: "42" }])
  })

  it("survives a split inside the data payload", () => {
    const p = createSseParser()
    p.push('event: step\ndata: {"long":"')
    expect(p.push('value"}\n\n')).toEqual([
      { event: "step", data: '{"long":"value"}' },
    ])
  })

  it("survives a byte-at-a-time delivery", () => {
    const p = createSseParser()
    const wire = 'event: step\ndata: {"a":1}\n\n'
    const frames = [...wire].flatMap((ch) => p.push(ch))
    expect(frames).toEqual([{ event: "step", data: '{"a":1}' }])
  })

  it("defaults to message when no event line is given", () => {
    const p = createSseParser()
    expect(p.push("data: 1\n\n")).toEqual([{ event: "message", data: "1" }])
  })

  it("resets the event name between frames", () => {
    const p = createSseParser()
    p.push("event: step\ndata: 1\n\n")
    expect(p.push("data: 2\n\n")).toEqual([{ event: "message", data: "2" }])
  })

  it("emits nothing for a frame with no data", () => {
    const p = createSseParser()
    expect(p.push("event: ping\n\n")).toEqual([])
  })

  it("flush completes a frame left without its blank line", () => {
    const p = createSseParser()
    expect(p.push("event: step\ndata: 1\n")).toEqual([])
    expect(p.flush()).toEqual([{ event: "step", data: "1" }])
  })

  it("flush is a no-op when nothing is buffered", () => {
    expect(createSseParser().flush()).toEqual([])
  })

  it("does not leak state between instances", () => {
    const a = createSseParser()
    a.push("event: step\n")
    expect(createSseParser().push("data: 1\n\n")).toEqual([
      { event: "message", data: "1" },
    ])
  })
})

describe("unwrapPayload", () => {
  it("unwraps the cursor envelope", () => {
    expect(unwrapPayload('{"cursor":7,"data":{"a":1}}')).toEqual({
      cursor: 7,
      data: { a: 1 },
    })
  })

  it("passes a bare payload through without a cursor", () => {
    expect(unwrapPayload('{"a":1}')).toEqual({ data: { a: 1 } })
  })

  it("treats a non-numeric cursor as no cursor", () => {
    expect(unwrapPayload('{"cursor":"7","data":1}')).toEqual({
      data: { cursor: "7", data: 1 },
    })
  })

  it("keeps cursor 0, which is falsy but a real position", () => {
    expect(unwrapPayload('{"cursor":0,"data":"x"}')).toEqual({
      cursor: 0,
      data: "x",
    })
  })

  it("keeps the whole object when the envelope has no data key", () => {
    expect(unwrapPayload('{"cursor":3}')).toEqual({
      cursor: 3,
      data: { cursor: 3 },
    })
  })

  it("throws on malformed JSON so callers can skip the frame", () => {
    expect(() => unwrapPayload("not json")).toThrow()
  })
})
