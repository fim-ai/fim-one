"use client"

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
} from "react"
import { conversationApi, ApiError } from "@/lib/api"
import { useAuth } from "./auth-context"
import type {
  ConversationResponse,
  ConversationDetail,
} from "@/types/conversation"

interface ConversationContextValue {
  conversations: ConversationResponse[]
  activeConversation: ConversationDetail | null
  activeId: string | null
  isLoadingList: boolean
  isLoadingDetail: boolean
  /** Map of conversation IDs to partially-typed titles (typewriter animation). */
  typingTitles: Record<string, string>
  loadConversations: () => Promise<void>
  selectConversation: (id: string) => Promise<void>
  createConversation: (
    mode: "react" | "dag",
    title?: string,
    agentId?: string,
  ) => Promise<ConversationResponse>
  deleteConversation: (id: string) => Promise<void>
  updateTitle: (id: string, title: string) => Promise<void>
  /** Animate a title into the sidebar with a typewriter effect. */
  animateTitle: (id: string, fullTitle: string) => void
  toggleStar: (id: string) => Promise<void>
  batchDeleteConversations: (ids: string[]) => Promise<void>
  clearActive: () => void
}

const ConversationContext = createContext<ConversationContextValue | null>(null)

export function ConversationProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const { user } = useAuth()
  const [conversations, setConversations] = useState<ConversationResponse[]>([])
  const [activeConversation, setActiveConversation] =
    useState<ConversationDetail | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [isLoadingList, setIsLoadingList] = useState(false)
  const [isLoadingDetail, setIsLoadingDetail] = useState(false)
  const [typingTitles, setTypingTitles] = useState<Record<string, string>>({})
  const animationTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  const loadConversations = useCallback(async () => {
    setIsLoadingList(true)
    try {
      const res = await conversationApi.list(1, 20)
      setConversations(res.items)
    } catch (err) {
      console.error("Failed to load conversations:", err)
    } finally {
      setIsLoadingList(false)
    }
  }, [])

  // Load when user changes
  useEffect(() => {
    if (user) {
      loadConversations()
    } else {
      setConversations([])
      setActiveConversation(null)
      setActiveId(null)
    }
  }, [user, loadConversations])

  const selectConversation = useCallback(async (id: string) => {
    setActiveId(id)
    setIsLoadingDetail(true)
    try {
      const detail = await conversationApi.get(id)
      setActiveConversation(detail)
    } catch (err) {
      setActiveId(null)
      setActiveConversation(null)
      if (err instanceof ApiError && err.status === 404) {
        // Conversation was deleted — prune stale entry from sidebar list
        setConversations((prev) => prev.filter((c) => c.id !== id))
      } else {
        console.error("Failed to load conversation:", err)
      }
    } finally {
      setIsLoadingDetail(false)
    }
  }, [])

  const createConversation = useCallback(
    async (mode: "react" | "dag", title?: string, agentId?: string) => {
      const conv = await conversationApi.create({ mode, title, agent_id: agentId })
      setConversations((prev) => [conv, ...prev])
      setActiveId(conv.id)
      setActiveConversation({ ...conv, messages: [] })
      return conv
    },
    [],
  )

  const deleteConversation = useCallback(
    async (id: string) => {
      await conversationApi.delete(id)
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (activeId === id) {
        setActiveConversation(null)
        setActiveId(null)
      }
    },
    [activeId],
  )

  const updateTitle = useCallback(
    async (id: string, title: string) => {
      await conversationApi.update(id, { title })
      setConversations((prev) =>
        prev.map((c) => (c.id === id ? { ...c, title } : c)),
      )
      if (activeConversation?.id === id) {
        setActiveConversation((prev) => (prev ? { ...prev, title } : prev))
      }
    },
    [activeConversation?.id],
  )

  const animateTitle = useCallback((id: string, fullTitle: string) => {
    // Clear any existing animation for this ID
    if (animationTimers.current[id]) {
      clearInterval(animationTimers.current[id])
    }
    let index = 1
    setTypingTitles((prev) => ({ ...prev, [id]: fullTitle.charAt(0) }))
    const timer = setInterval(() => {
      index++
      if (index > fullTitle.length) {
        clearInterval(timer)
        delete animationTimers.current[id]
        setTypingTitles((prev) => {
          const next = { ...prev }
          delete next[id]
          return next
        })
        // Ensure the conversations list has the full title
        setConversations((prev) =>
          prev.map((c) => (c.id === id ? { ...c, title: fullTitle } : c)),
        )
        return
      }
      setTypingTitles((prev) => ({ ...prev, [id]: fullTitle.slice(0, index) }))
    }, 32)
    animationTimers.current[id] = timer
  }, [])

  // Cleanup animation timers on unmount
  useEffect(() => {
    const timers = animationTimers.current
    return () => {
      Object.values(timers).forEach(clearInterval)
    }
  }, [])

  const toggleStar = useCallback(async (id: string) => {
    const conv = conversations.find((c) => c.id === id)
    if (!conv) return
    const updated = await conversationApi.update(id, { starred: !conv.starred })
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, starred: updated.starred } : c)),
    )
  }, [conversations])

  const batchDeleteConversations = useCallback(
    async (ids: string[]) => {
      await conversationApi.batchDelete(ids)
      setConversations((prev) => prev.filter((c) => !ids.includes(c.id)))
      if (activeId && ids.includes(activeId)) {
        setActiveConversation(null)
        setActiveId(null)
      }
    },
    [activeId],
  )

  const clearActive = useCallback(() => {
    setActiveConversation(null)
    setActiveId(null)
  }, [])

  return (
    <ConversationContext.Provider
      value={{
        conversations,
        activeConversation,
        activeId,
        isLoadingList,
        isLoadingDetail,
        typingTitles,
        loadConversations,
        selectConversation,
        createConversation,
        deleteConversation,
        updateTitle,
        animateTitle,
        toggleStar,
        batchDeleteConversations,
        clearActive,
      }}
    >
      {children}
    </ConversationContext.Provider>
  )
}

export function useConversation() {
  const ctx = useContext(ConversationContext)
  if (!ctx)
    throw new Error("useConversation must be used within ConversationProvider")
  return ctx
}
