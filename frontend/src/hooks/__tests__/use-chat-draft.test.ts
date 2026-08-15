import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"
import {
  useChatDraft,
  readDraft,
  saveDraft,
  dropDrafts,
  isEmptyDraft,
  NEW_CHAT_DRAFT_KEY,
  type ChatDraft,
  type DraftAttachment,
  type DraftClip,
} from "@/hooks/use-chat-draft"
import type { FileUploadResponse } from "@/types/file"

const USER = "user-1"
const STORAGE_KEY = `fim-one:chat-drafts:${USER}`

function draft(partial: Partial<ChatDraft> = {}): ChatDraft {
  return { text: "", clips: [], attachments: [], ...partial }
}

function clip(id: string, content: string): DraftClip {
  return { id, content, preview: content.slice(0, 10), charCount: content.length }
}

function attachment(id: string, fileId: string): DraftAttachment {
  const uploadResult: FileUploadResponse = {
    file_id: fileId,
    filename: `${fileId}.png`,
    file_url: `/api/files/${fileId}`,
    size: 1234,
    content_preview: null,
    content_length: null,
    mime_type: "image/png",
  }
  return { id, uploadResult }
}

beforeEach(() => {
  localStorage.clear()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe("draft store", () => {
  it("round-trips text, clips and attachments per key", () => {
    saveDraft(USER, "conv-a", draft({ text: "half-written A", clips: [clip("c1", "pasted log")] }))
    saveDraft(USER, "conv-b", draft({ attachments: [attachment("f1", "file-1")] }))

    const a = readDraft(USER, "conv-a")
    expect(a.text).toBe("half-written A")
    expect(a.clips).toHaveLength(1)
    expect(a.clips[0].content).toBe("pasted log")

    const b = readDraft(USER, "conv-b")
    expect(b.text).toBe("")
    expect(b.attachments[0].uploadResult.file_id).toBe("file-1")

    expect(isEmptyDraft(readDraft(USER, "conv-missing"))).toBe(true)
  })

  it("treats an attachment-only or clip-only draft as worth keeping", () => {
    expect(isEmptyDraft(draft({ text: "   " }))).toBe(true)
    expect(isEmptyDraft(draft({ clips: [clip("c1", "x")] }))).toBe(false)
    expect(isEmptyDraft(draft({ attachments: [attachment("f1", "file-1")] }))).toBe(false)
  })

  it("keeps drafts namespaced per user", () => {
    saveDraft(USER, "conv-a", draft({ text: "mine" }))
    expect(readDraft("user-2", "conv-a").text).toBe("")
  })

  it("removes the entry when the draft empties out", () => {
    saveDraft(USER, "conv-a", draft({ text: "typing" }))
    saveDraft(USER, "conv-a", draft({ text: "   " }))
    expect(readDraft(USER, "conv-a").text).toBe("")
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it("drops expired drafts on read", () => {
    const stale = Date.now() - 31 * 24 * 60 * 60 * 1000
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        old: { text: "stale", clips: [], attachments: [], ts: stale },
        fresh: { text: "fresh", clips: [], attachments: [], ts: Date.now() },
      }),
    )
    expect(readDraft(USER, "old").text).toBe("")
    expect(readDraft(USER, "fresh").text).toBe("fresh")
  })

  it("caps stored drafts, newest first", () => {
    for (let i = 0; i < 30; i++) {
      saveDraft(USER, `conv-${i}`, draft({ text: `draft ${i}` }))
      vi.advanceTimersByTime(1)
    }
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as Record<string, unknown>
    expect(Object.keys(stored)).toHaveLength(20)
    expect(readDraft(USER, "conv-29").text).toBe("draft 29")
    expect(readDraft(USER, "conv-0").text).toBe("")
  })

  it("drops oversized clips whole rather than storing a truncated one", () => {
    const huge = "x".repeat(200 * 1024)
    saveDraft(USER, "conv-a", draft({ text: "see the log", clips: [clip("c1", huge)] }))
    const stored = readDraft(USER, "conv-a")
    expect(stored.text).toBe("see the log")
    expect(stored.clips).toHaveLength(0)
  })

  it("survives corrupted storage", () => {
    localStorage.setItem(STORAGE_KEY, "{not json")
    expect(readDraft(USER, "conv-a").text).toBe("")
  })

  it("ignores malformed entries instead of restoring junk", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        "conv-a": { text: "ok", clips: [{ nope: 1 }], attachments: [{ id: "x" }], ts: Date.now() },
      }),
    )
    const stored = readDraft(USER, "conv-a")
    expect(stored.text).toBe("ok")
    expect(stored.clips).toHaveLength(0)
    expect(stored.attachments).toHaveLength(0)
  })

  it("drops drafts for deleted conversations", () => {
    saveDraft(USER, "conv-a", draft({ text: "a" }))
    saveDraft(USER, "conv-b", draft({ text: "b" }))
    dropDrafts(USER, ["conv-a"])
    expect(readDraft(USER, "conv-a").text).toBe("")
    expect(readDraft(USER, "conv-b").text).toBe("b")
  })
})

interface ComposerProps {
  draftKey: string
  text: string
  clips?: DraftClip[]
  attachments?: DraftAttachment[]
  enabled?: boolean
}

const NO_CLIPS: DraftClip[] = []
const NO_ATTACHMENTS: DraftAttachment[] = []

/** Drives the hook the way the composer does: value in, restored draft out. */
function renderComposer(initial: ComposerProps) {
  let restored: ChatDraft | null = null
  const view = renderHook(
    (props: ComposerProps) =>
      useChatDraft({
        userId: USER,
        draftKey: props.draftKey,
        enabled: props.enabled ?? true,
        text: props.text,
        clips: props.clips ?? NO_CLIPS,
        attachments: props.attachments ?? NO_ATTACHMENTS,
        onRestore: (d) => {
          restored = d
        },
      }),
    { initialProps: initial },
  )
  return { view, getRestored: () => restored as ChatDraft | null }
}

describe("useChatDraft", () => {
  it("persists the composer value after the debounce window", () => {
    const { view } = renderComposer({ draftKey: NEW_CHAT_DRAFT_KEY, text: "" })
    view.rerender({ draftKey: NEW_CHAT_DRAFT_KEY, text: "unsent text" })
    expect(readDraft(USER, NEW_CHAT_DRAFT_KEY).text).toBe("")
    act(() => {
      vi.advanceTimersByTime(300)
    })
    expect(readDraft(USER, NEW_CHAT_DRAFT_KEY).text).toBe("unsent text")
  })

  it("persists clips and finished attachments alongside the text", () => {
    const { view } = renderComposer({ draftKey: "conv-a", text: "" })
    view.rerender({
      draftKey: "conv-a",
      text: "look at this",
      clips: [clip("c1", "a long pasted block")],
      attachments: [attachment("f1", "file-1")],
    })
    act(() => {
      vi.advanceTimersByTime(300)
    })
    const stored = readDraft(USER, "conv-a")
    expect(stored.clips[0].id).toBe("c1")
    expect(stored.attachments[0].uploadResult.file_id).toBe("file-1")
  })

  it("restores the stored draft on mount", () => {
    saveDraft(USER, "conv-a", draft({ text: "left half-written", clips: [clip("c1", "log")] }))
    const { getRestored } = renderComposer({ draftKey: "conv-a", text: "" })
    expect(getRestored()?.text).toBe("left half-written")
    expect(getRestored()?.clips).toHaveLength(1)
  })

  it("swaps drafts when the conversation changes, flushing the outgoing one", () => {
    saveDraft(USER, "conv-b", draft({ text: "draft B" }))
    const { view, getRestored } = renderComposer({ draftKey: "conv-a", text: "" })
    view.rerender({ draftKey: "conv-a", text: "draft A" })
    // Switch before the debounce fires — the pending write must not be lost.
    view.rerender({ draftKey: "conv-b", text: "draft A" })
    expect(readDraft(USER, "conv-a").text).toBe("draft A")
    expect(getRestored()?.text).toBe("draft B")
  })

  it("clears the stored draft once the composer empties (message sent)", () => {
    const { view } = renderComposer({
      draftKey: "conv-a",
      text: "",
    })
    view.rerender({
      draftKey: "conv-a",
      text: "about to send",
      attachments: [attachment("f1", "file-1")],
    })
    act(() => {
      vi.advanceTimersByTime(300)
    })
    view.rerender({ draftKey: "conv-a", text: "" })
    act(() => {
      vi.advanceTimersByTime(300)
    })
    expect(isEmptyDraft(readDraft(USER, "conv-a"))).toBe(true)
  })

  it("flushes a pending write on unmount", () => {
    const { view } = renderComposer({ draftKey: "conv-a", text: "" })
    view.rerender({ draftKey: "conv-a", text: "closing the tab" })
    view.unmount()
    expect(readDraft(USER, "conv-a").text).toBe("closing the tab")
  })

  it("does nothing when disabled", () => {
    saveDraft(USER, "conv-a", draft({ text: "stored" }))
    const { view, getRestored } = renderComposer({ draftKey: "conv-a", text: "", enabled: false })
    view.rerender({ draftKey: "conv-a", text: "typed", enabled: false })
    act(() => {
      vi.advanceTimersByTime(300)
    })
    expect(getRestored()).toBeNull()
    expect(readDraft(USER, "conv-a").text).toBe("stored")
  })
})
