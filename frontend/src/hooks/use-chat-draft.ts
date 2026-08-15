"use client"

import { useCallback, useEffect, useRef } from "react"
import type { FileUploadResponse } from "@/types/file"

/**
 * Composer draft persistence.
 *
 * Unsent input survives page refreshes, conversation switches and expired
 * sessions: each conversation keeps its own draft, and the "new chat" composer
 * keeps one under a reserved key. A draft holds the typed text, pasted clips
 * and already-uploaded attachments (by `file_id` — the bytes live on the
 * server). It lives until the message is sent (the composer clears itself,
 * which erases the entry) or until it ages out.
 *
 * Drafts are namespaced per user so a shared browser never shows one account's
 * text to another. They are deliberately NOT cleared on logout — an expired
 * token is the exact case this feature exists to survive.
 */

/** Reserved draft key for the composer with no active conversation. */
export const NEW_CHAT_DRAFT_KEY = "__new__"

const STORAGE_PREFIX = "fim-one:chat-drafts:"
const MAX_DRAFTS = 20
/** Per-draft ceiling. Clips are dropped whole rather than cut — a truncated
 *  clip would silently change the message the user later sends. */
const MAX_DRAFT_BYTES = 128 * 1024
const DRAFT_TTL_MS = 30 * 24 * 60 * 60 * 1000 // 30 days
const WRITE_DELAY_MS = 250

export interface DraftClip {
  id: string
  content: string
  preview: string
  charCount: number
}

export interface DraftAttachment {
  id: string
  uploadResult: FileUploadResponse
}

export interface ChatDraft {
  text: string
  clips: DraftClip[]
  attachments: DraftAttachment[]
}

interface DraftEntry extends ChatDraft {
  ts: number
}

type DraftMap = Record<string, DraftEntry>

export const EMPTY_DRAFT: ChatDraft = { text: "", clips: [], attachments: [] }

export function isEmptyDraft(draft: ChatDraft): boolean {
  return !draft.text.trim() && draft.clips.length === 0 && draft.attachments.length === 0
}

function storageKey(userId: string): string {
  return `${STORAGE_PREFIX}${userId}`
}

function normalizeEntry(value: unknown): DraftEntry | null {
  if (!value || typeof value !== "object") return null
  const entry = value as Partial<DraftEntry>
  if (typeof entry.text !== "string" || typeof entry.ts !== "number") return null
  const clips = Array.isArray(entry.clips)
    ? entry.clips.filter(
        (c): c is DraftClip =>
          !!c && typeof c.id === "string" && typeof c.content === "string",
      )
    : []
  const attachments = Array.isArray(entry.attachments)
    ? entry.attachments.filter(
        (a): a is DraftAttachment =>
          !!a && typeof a.id === "string" && !!a.uploadResult?.file_id,
      )
    : []
  return { text: entry.text, clips, attachments, ts: entry.ts }
}

function readAll(userId: string): DraftMap {
  if (typeof window === "undefined") return {}
  try {
    const raw = window.localStorage.getItem(storageKey(userId))
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object") return {}
    const now = Date.now()
    const fresh: DraftMap = {}
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      const entry = normalizeEntry(value)
      if (!entry) continue
      if (now - entry.ts > DRAFT_TTL_MS) continue
      fresh[key] = entry
    }
    return fresh
  } catch {
    return {}
  }
}

/** Returns false when the write was rejected (quota, private mode). */
function writeAll(userId: string, map: DraftMap): boolean {
  if (typeof window === "undefined") return true
  try {
    const entries = Object.entries(map)
      .sort((a, b) => b[1].ts - a[1].ts)
      .slice(0, MAX_DRAFTS)
    if (entries.length === 0) {
      window.localStorage.removeItem(storageKey(userId))
    } else {
      window.localStorage.setItem(storageKey(userId), JSON.stringify(Object.fromEntries(entries)))
    }
    return true
  } catch {
    return false
  }
}

/** Drops clips when the draft is too large to keep whole. */
function withinLimit(entry: DraftEntry): DraftEntry {
  if (JSON.stringify(entry).length <= MAX_DRAFT_BYTES) return entry
  return { ...entry, clips: [] }
}

export function readDraft(userId: string, key: string): ChatDraft {
  const entry = readAll(userId)[key]
  if (!entry) return EMPTY_DRAFT
  return { text: entry.text, clips: entry.clips, attachments: entry.attachments }
}

/** Stores the draft under `key`; an empty draft removes the entry. */
export function saveDraft(userId: string, key: string, draft: ChatDraft): void {
  const map = readAll(userId)
  if (isEmptyDraft(draft)) {
    if (!(key in map)) return
    delete map[key]
    writeAll(userId, map)
    return
  }

  const entry: DraftEntry = { ...draft, ts: Date.now() }
  map[key] = withinLimit(entry)
  if (writeAll(userId, map)) return

  // Out of quota. Shed this draft's clips first, then everything but its text —
  // losing what the user typed is the one outcome worth avoiding.
  map[key] = { ...entry, clips: [] }
  if (writeAll(userId, map)) return
  writeAll(userId, { [key]: { text: draft.text, clips: [], attachments: [], ts: entry.ts } })
}

/** Drops drafts for conversations that no longer exist. */
export function dropDrafts(userId: string, keys: string[]): void {
  if (keys.length === 0) return
  const map = readAll(userId)
  let changed = false
  for (const key of keys) {
    if (key in map) {
      delete map[key]
      changed = true
    }
  }
  if (changed) writeAll(userId, map)
}

interface UseChatDraftOptions {
  /** Owner of the drafts; empty string disables persistence (not signed in yet). */
  userId: string
  /** Conversation id, or `NEW_CHAT_DRAFT_KEY` for the fresh-chat composer. */
  draftKey: string
  /** Set false to opt out (embedded composers, unresolved conversation). */
  enabled: boolean
  /** Current composer text. */
  text: string
  /** Pasted clips currently attached to the composer. */
  clips: DraftClip[]
  /** Attachments that finished uploading; in-flight ones are not persisted. */
  attachments: DraftAttachment[]
  /** Called with the stored draft when the target changes. Must be stable. */
  onRestore: (draft: ChatDraft) => void
}

/**
 * Mirrors the composer into localStorage and restores it whenever the draft
 * target changes. `clips` and `attachments` must be referentially stable
 * between unrelated renders (state or `useMemo`), or the debounced write
 * would be rescheduled on every render of a streaming conversation.
 */
export function useChatDraft({
  userId,
  draftKey,
  enabled,
  text,
  clips,
  attachments,
  onRestore,
}: UseChatDraftOptions): void {
  const active = enabled && Boolean(userId)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingRef = useRef<{ userId: string; key: string; draft: ChatDraft } | null>(null)
  const loadedKeyRef = useRef<string | null>(null)

  const flush = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    const pending = pendingRef.current
    if (!pending) return
    pendingRef.current = null
    saveDraft(pending.userId, pending.key, pending.draft)
  }, [])

  // Restore on mount and on every target change (conversation switch, new chat).
  useEffect(() => {
    if (!active) return
    if (loadedKeyRef.current === draftKey) return
    // Persist the outgoing conversation's draft before swapping targets.
    flush()
    loadedKeyRef.current = draftKey
    onRestore(readDraft(userId, draftKey))
  }, [active, userId, draftKey, flush, onRestore])

  // Mirror edits, debounced so typing doesn't hit localStorage per keystroke.
  useEffect(() => {
    if (!active) return
    if (loadedKeyRef.current !== draftKey) return
    pendingRef.current = { userId, key: draftKey, draft: { text, clips, attachments } }
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      flush()
    }, WRITE_DELAY_MS)
  }, [active, userId, draftKey, text, clips, attachments, flush])

  // A refresh or tab close can land inside the debounce window — flush first.
  useEffect(() => {
    if (!active) return
    const handler = () => flush()
    window.addEventListener("pagehide", handler)
    document.addEventListener("visibilitychange", handler)
    return () => {
      window.removeEventListener("pagehide", handler)
      document.removeEventListener("visibilitychange", handler)
      flush()
    }
  }, [active, flush])
}
