"use client"

import { useState, useCallback, useRef, useEffect, useMemo, Fragment } from "react"
import { useRouter, useSearchParams, usePathname } from "next/navigation"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Send, Loader2, PanelRightOpen, PanelRightClose, ArrowDown, Square, Zap, GitBranch, User, Paperclip, X, Plus, ChevronsUpDown, Check, Undo2 } from "lucide-react"
import { toast } from "sonner"
import { useSSE } from "@/hooks/use-sse"
import { useDagSteps } from "@/hooks/use-dag-steps"
import { useReactSteps } from "@/hooks/use-react-steps"
import { useMediaQuery } from "@/hooks/use-media-query"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { useAuth } from "@/contexts/auth-context"
import { useConversation } from "@/contexts/conversation-context"
import { agentApi, fileApi, chatApi } from "@/lib/api"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import { cn, formatFileSize, isImageFile } from "@/lib/utils"
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { ReactOutput } from "@/components/playground/react-output"
import { DagOutput } from "@/components/playground/dag-output"
import { Examples } from "@/components/playground/examples"
import { RightSidebar } from "@/components/playground/right-sidebar"
import { DagFlowGraph } from "@/components/dag/dag-flow-graph"
import { HistoryMessages } from "@/components/playground/history-messages"
import { reconstructSSEMessages } from "@/lib/sse-utils"
import type { SSEMessage } from "@/hooks/use-sse"
import type { MessageResponse } from "@/types/conversation"
import type { AgentResponse } from "@/types/agent"
import type { FileUploadResponse } from "@/types/file"
import type { AgentMode, Language } from "@/components/playground/examples"

interface PlaygroundPageProps {
  /** When true, this is a fresh "new chat" page — no conversation should be loaded from URL */
  isNewChat?: boolean
}

export function PlaygroundPage({ isNewChat }: PlaygroundPageProps) {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const {
    activeConversation,
    activeId,
    createConversation,
    selectConversation,
    loadConversations,
    clearActive,
  } = useConversation()

  const [mode, setMode] = useState<AgentMode>("react")
  const [selectedAgent, setSelectedAgent] = useState<AgentResponse | null>(null)
  const [query, setQuery] = useState("")
  const [language, setLanguage] = useState<Language>("en")
  const [sourceMode, setSourceMode] = useState<AgentMode | null>(null)
  const [pendingQuery, setPendingQuery] = useState<string | null>(null)
  const [pendingMode, setPendingMode] = useState<AgentMode | null>(null)
  const { messages, isRunning, start, reset, abort } = useSSE()
  const [injectedMessages, setInjectedMessages] = useState<{id?: string; content: string; ts: number}[]>([])
  const failedInjectRef = useRef<string | null>(null)

  // Read agent param from URL for quick chat link
  const agentParam = isNewChat ? searchParams.get("agent") : null

  // Ref to track conversation IDs we created ourselves (via send),
  // so the "switch conversation" effect doesn't reset SSE for them.
  const selfCreatedIdRef = useRef<string | null>(null)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  // For /new route: ensure active conversation is cleared on mount
  const clearedForNewRef = useRef(false)
  useEffect(() => {
    if (isNewChat && !clearedForNewRef.current) {
      clearedForNewRef.current = true
      clearActive()
    }
  }, [isNewChat, clearActive])

  // URL -> state: on mount, if ?c=<id> is in URL, select that conversation
  // Only applies to the root route (not /new)
  const initializedRef = useRef(false)
  useEffect(() => {
    if (initializedRef.current || authLoading || !user) return
    initializedRef.current = true
    if (isNewChat) return // /new route: don't load from URL
    const cParam = searchParams.get("c")
    if (cParam && cParam !== activeId) {
      selectConversation(cParam)
    }
  }, [authLoading, user]) // eslint-disable-line react-hooks/exhaustive-deps

  // State -> URL: sync activeId to URL search param (use history API to avoid RSC flight request)
  // Skip the first run -- on mount activeId is null but URL may have ?c=xxx from direct navigation
  const urlSyncSkipRef = useRef(true)
  useEffect(() => {
    if (!initializedRef.current) return
    if (urlSyncSkipRef.current) {
      urlSyncSkipRef.current = false
      return
    }
    if (activeId) {
      // When conversation is created (e.g. from /new), always navigate to /?c=<id>
      const targetUrl = `/?c=${activeId}`
      const currentUrl = window.location.pathname + window.location.search
      if (targetUrl !== currentUrl) {
        window.history.replaceState(null, "", targetUrl)
      }
    } else if (pathname === "/") {
      // Only clear URL params when on root route and activeId becomes null
      const currentUrl = window.location.pathname + window.location.search
      if (currentUrl !== "/new" && currentUrl !== "/") {
        window.history.replaceState(null, "", "/new")
      }
    }
  }, [activeId, pathname])

  // When user clicks a DIFFERENT conversation in sidebar, sync mode and reset SSE.
  // Skip if we just created this conversation ourselves (selfCreatedIdRef).
  const prevActiveIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (activeConversation && activeConversation.id !== prevActiveIdRef.current) {
      // Skip reset if this is a conversation we just created via send
      if (activeConversation.id === selfCreatedIdRef.current) {
        selfCreatedIdRef.current = null
        prevActiveIdRef.current = activeConversation.id
        return
      }
      setMode(activeConversation.mode as AgentMode)
      reset()
      setQuery("")
      setSourceMode(null)
      setPendingQuery(null)
    }
    if (activeConversation) {
      prevActiveIdRef.current = activeConversation.id
    }
  }, [activeConversation?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // When active conversation is cleared (new chat), reset everything
  useEffect(() => {
    if (!activeId && prevActiveIdRef.current !== null) {
      reset()
      setQuery("")
      setSourceMode(null)
      setPendingQuery(null)
      prevActiveIdRef.current = null
    }
  }, [activeId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Refresh conversation list when SSE completes (keep live state for sidebar)
  const sseJustFinishedRef = useRef(false)
  useEffect(() => {
    if (isRunning) {
      sseJustFinishedRef.current = true
    } else if (sseJustFinishedRef.current) {
      sseJustFinishedRef.current = false
      setInjectedMessages([])
      loadConversations()
      // Restore failed inject content to input box for user to re-send
      const queued = failedInjectRef.current
      if (queued) {
        failedInjectRef.current = null
        setQuery(queued)
        toast.warning("Message couldn't be injected — press Enter to send as a new turn.")
      }
    }
  }, [isRunning]) // eslint-disable-line react-hooks/exhaustive-deps

  // (Agent selection no longer overrides mode -- user controls mode independently)

  const runWithQuery = useCallback(
    async (q: string, imageIds?: string[]) => {
      const trimmed = q.trim()
      if (!trimmed) return

      // INJECT MODE: during active execution, inject message
      if (isRunning && activeId) {
        setQuery("")
        const ts = Date.now()
        setInjectedMessages(prev => [...prev, { content: trimmed, ts }])
        try {
          const res = await chatApi.inject(activeId, trimmed)
          // Store the backend-assigned id for recall support
          setInjectedMessages(prev => prev.map(m => m.ts === ts ? { ...m, id: res.id } : m))
        } catch {
          // 409 = execution already finished — restore to input for user to re-send
          setInjectedMessages(prev => prev.filter(m => m.ts !== ts))
          failedInjectRef.current = trimmed
        }
        return
      }

      if (isRunning) return

      // Clear input and show user message immediately
      setQuery("")
      setPendingQuery(trimmed)

      let convId = activeId

      // Auto-create conversation if none selected
      if (!convId) {
        try {
          const conv = await createConversation(
            mode,
            trimmed.slice(0, 60),
            selectedAgent?.id,
          )
          convId = conv.id
          // Mark as self-created so the activeConversation effect doesn't reset SSE
          selfCreatedIdRef.current = convId
        } catch (err) {
          console.error("Failed to create conversation:", err)
          return
        }
      } else {
        // Existing conversation -- refresh to get all previous messages (with sse_events)
        // so they render as history while the new turn streams live.
        await selectConversation(convId)
      }

      const endpoint = mode === "react" ? "react" : "dag"
      let url = `${getApiBaseUrl()}/api/${endpoint}?q=${encodeURIComponent(trimmed)}&conversation_id=${convId}`
      const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY)
      if (accessToken) {
        url += `&token=${encodeURIComponent(accessToken)}`
      }
      if (selectedAgent?.id) {
        url += `&agent_id=${encodeURIComponent(selectedAgent.id)}`
      }
      if (imageIds?.length) {
        url += `&image_ids=${encodeURIComponent(imageIds.join(","))}`
      }
      setSourceMode(mode)
      start(url)
    },
    [isRunning, mode, start, activeId, createConversation, selectConversation, selectedAgent, setInjectedMessages],
  )

  const handleExampleSelect = useCallback(
    (example: string) => {
      setQuery(example)
      runWithQuery(example)
    },
    [runWithQuery],
  )

  const handleRecallInject = useCallback(
    (msg: {id?: string; content: string; ts: number}) => {
      // Remove from optimistic state
      setInjectedMessages(prev => prev.filter(m => m.ts !== msg.ts))
      // Recall from backend queue
      if (msg.id && activeId) {
        chatApi.recallInject(activeId, msg.id).catch(() => {})
      }
      // If input is empty, fill with recalled content for easy re-edit
      setQuery(prev => prev.trim() ? prev : msg.content)
    },
    [activeId],
  )

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col">
      <PlaygroundContent
        mode={mode}
        sourceMode={sourceMode}
        query={query}
        pendingQuery={pendingQuery}
        language={language}
        messages={messages}
        isRunning={isRunning}
        activeConversation={activeConversation}
        selectedAgent={selectedAgent}
        injectedMessages={injectedMessages}
        onRecallInject={handleRecallInject}
        onAgentChange={setSelectedAgent}
        onQueryChange={setQuery}
        onLanguageChange={setLanguage}
        onModeChange={(m) => {
          if (isRunning) return
          if (activeId) {
            setPendingMode(m)
          } else {
            setMode(m)
          }
        }}
        onRunWithQuery={runWithQuery}
        onAbort={abort}
        onExampleSelect={handleExampleSelect}
        isNewChat={isNewChat}
        initialAgentId={agentParam}
      />

      {/* Mode switch confirmation dialog */}
      <Dialog open={pendingMode !== null} onOpenChange={(open) => { if (!open) setPendingMode(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Switch to {pendingMode?.toUpperCase()} mode?</DialogTitle>
            <DialogDescription>
              This will start a new conversation. Your current conversation is saved and accessible from the sidebar.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingMode(null)}>
              Cancel
            </Button>
            <Button className="px-6" onClick={() => {
              if (pendingMode) {
                reset()
                setPendingQuery(null)
                setSourceMode(null)
                clearActive()
                setMode(pendingMode)
                // Navigate to /new when switching mode
                router.push("/new")
              }
              setPendingMode(null)
            }}>
              Switch
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  )
}

/** Fetches an image via authenticated request and displays a clickable thumbnail with lightbox. */
function ImageThumbnail({ fileId, filename }: { fileId: string; filename: string }) {
  const [expanded, setExpanded] = useState(false)
  const [blobUrl, setBlobUrl] = useState<string | null>(null)

  useEffect(() => {
    let revoked = false
    const token = localStorage.getItem(ACCESS_TOKEN_KEY)
    fetch(`${getApiBaseUrl()}/api/files/${fileId}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => res.blob())
      .then((blob) => {
        if (!revoked) setBlobUrl(URL.createObjectURL(blob))
      })
      .catch(() => {})

    return () => {
      revoked = true
      // blobUrl cleanup happens via the separate ref below
    }
  }, [fileId])

  // Clean up blob URL on unmount
  useEffect(() => {
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [blobUrl])

  if (!blobUrl) return <div className="h-16 w-16 rounded-md border border-border/40 bg-muted/30 animate-pulse" />

  return (
    <>
      <button
        onClick={() => setExpanded(true)}
        className="group relative overflow-hidden rounded-md border border-border/40"
      >
        <img src={blobUrl} alt={filename} className="h-16 w-16 object-cover transition-transform group-hover:scale-105" loading="lazy" />
      </button>
      {expanded && (
        <Dialog open={expanded} onOpenChange={setExpanded}>
          <DialogContent className="max-w-3xl">
            <DialogHeader>
              <DialogTitle>{filename}</DialogTitle>
            </DialogHeader>
            <img src={blobUrl} alt={filename} className="w-full rounded" />
          </DialogContent>
        </Dialog>
      )}
    </>
  )
}

/** Renders a single history turn (user message + execution steps) using the same hooks as live mode. */
function HistoryTurn({ userContent, userMetadata, sseMessages, mode, hideDagGraph }: {
  userContent: string | null
  userMetadata?: Record<string, unknown> | null
  sseMessages: SSEMessage[]
  mode: "react" | "dag"
  hideDagGraph: boolean
}) {
  const reactItems = useReactSteps(sseMessages, false)
  const dagData = useDagSteps(sseMessages, false)

  return (
    <>
      {userContent && (
        <div className="flex items-center gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
            <User className="h-3.5 w-3.5 text-primary" />
          </div>
          <div className="flex-1">
            <p className="text-sm text-foreground">{userContent}</p>
            {Array.isArray(userMetadata?.images) && userMetadata.images.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {(userMetadata.images as Array<{ file_id: string; filename: string }>).map((img) => (
                  <ImageThumbnail key={img.file_id} fileId={img.file_id} filename={img.filename} />
                ))}
              </div>
            ) : null}
          </div>
        </div>
      )}
      {mode === "react" ? (
        <ReactOutput items={reactItems} />
      ) : (
        <DagOutput
          planSteps={dagData.planSteps}
          stepStates={dagData.stepStates}
          analysisPhase={dagData.analysisPhase}
          doneEvent={dagData.doneEvent}
          currentPhase={dagData.currentPhase}
          currentRound={dagData.currentRound}
          injectEvents={dagData.injectEvents}
          hideDagGraph={hideDagGraph}
          hideStepCards
        />
      )}
    </>
  )
}

/** Subtle divider shown when the backend compacted (summarized) older conversation context. */
function CompactDivider({ originalCount, keptCount }: { originalCount: number; keptCount: number }) {
  return (
    <div className="flex items-center gap-3 py-2">
      <div className="flex-1 border-t border-dashed border-border/50" />
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground/70 select-none">
        <span>&#9986;</span>
        <span>Earlier context ({originalCount - keptCount} messages) was summarized by AI</span>
      </span>
      <div className="flex-1 border-t border-dashed border-border/50" />
    </div>
  )
}

interface PlaygroundContentProps {
  mode: AgentMode
  sourceMode: AgentMode | null
  query: string
  pendingQuery: string | null
  language: Language
  messages: ReturnType<typeof useSSE>["messages"]
  isRunning: boolean
  activeConversation: ReturnType<typeof useConversation>["activeConversation"]
  selectedAgent: AgentResponse | null
  injectedMessages: {id?: string; content: string; ts: number}[]
  onRecallInject: (msg: {id?: string; content: string; ts: number}) => void
  onAgentChange: (agent: AgentResponse | null) => void
  onQueryChange: (q: string) => void
  onLanguageChange: (lang: Language) => void
  onModeChange: (mode: AgentMode) => void
  onRunWithQuery: (q: string, imageIds?: string[]) => void
  onAbort: () => void
  onExampleSelect: (example: string) => void
  isNewChat?: boolean
  initialAgentId?: string | null
}

function PlaygroundContent({
  mode,
  sourceMode,
  query,
  pendingQuery,
  language,
  messages,
  isRunning,
  activeConversation,
  selectedAgent,
  injectedMessages,
  onRecallInject,
  onAgentChange,
  onQueryChange,
  onLanguageChange,
  onModeChange,
  onRunWithQuery,
  onAbort,
  onExampleSelect,
  isNewChat,
  initialAgentId,
}: PlaygroundContentProps) {
  const modeMatches = sourceMode === mode
  const hasLiveMessages = modeMatches && messages.length > 0
  const hasHistory = !!(activeConversation?.messages && activeConversation.messages.length > 0)
  const hasMessages = hasLiveMessages || hasHistory || !!pendingQuery
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isNearBottomRef = useRef(true)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const composingRef = useRef(false)
  const [composing, setComposing] = useState(false)

  // Auto-focus textarea on new chat
  useEffect(() => {
    if (isNewChat) textareaRef.current?.focus()
  }, [isNewChat])

  // Sidebar state -- persisted to localStorage
  const [sidebarOpen, setSidebarOpen] = useLocalStorage("fim-sidebar-open", true)
  const [sidebarExpanded, setSidebarExpanded] = useLocalStorage("fim-sidebar-expanded", false)
  const [customRatio, setCustomRatio] = useLocalStorage<number | null>("fim-sidebar-custom-ratio", null)
  const isWideScreen = useMediaQuery("(min-width: 1024px)")

  // Drag resize state (transient, not persisted)
  const [dragRatio, setDragRatio] = useState<number | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [resizeKey, setResizeKey] = useState(0)
  const panelContainerRef = useRef<HTMLDivElement>(null)
  const dragRatioRef = useRef<number | null>(null)

  // Agent selector
  const [agents, setAgents] = useState<AgentResponse[]>([])
  const [agentsLoaded, setAgentsLoaded] = useState(false)
  const [agentSelectorOpen, setAgentSelectorOpen] = useState(false)

  // File upload
  const [attachedFiles, setAttachedFiles] = useState<(FileUploadResponse & { previewUrl?: string })[]>([])
  const [pendingImages, setPendingImages] = useState<Array<{ file_id: string; filename: string }>>([])
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Priority: active drag > custom drag (persisted) > expand preset > normal preset
  const NORMAL_RATIO = 0.3
  const EXPANDED_RATIO = 0.7
  const currentRatio = dragRatio ?? customRatio ?? (sidebarExpanded ? EXPANDED_RATIO : NORMAL_RATIO)

  // Parse data at this level via hooks
  const dagData = useDagSteps(messages, isRunning)
  const reactItems = useReactSteps(messages, isRunning)

  // Reconstruct all persisted execution steps from conversation messages.
  // Available during BOTH live mode (shows previous turns) and history mode (shows all turns).
  const allHistoryTurns = useMemo(() => {
    if (!activeConversation?.messages?.length) return null
    const turns: Array<{ user: MessageResponse | null; sseMessages: SSEMessage[] }> = []
    const msgs = activeConversation.messages
    for (let i = 0; i < msgs.length; i++) {
      const msg = msgs[i]
      if (msg.role === "assistant") {
        const reconstructed = reconstructSSEMessages(msg)
        if (reconstructed) {
          // Walk backwards to find the original "text" user message, skipping inject messages.
          let userMsg: MessageResponse | null = null
          for (let j = i - 1; j >= 0; j--) {
            if (msgs[j].role === "user" && msgs[j].message_type !== "inject") {
              userMsg = msgs[j]
              break
            }
          }
          turns.push({ user: userMsg, sseMessages: reconstructed })
        }
      }
    }
    return turns.length > 0 ? turns : null
  }, [activeConversation?.messages])

  // Detect compact event from SSE stream
  const compactEvent = useMemo(() => {
    if (!modeMatches) return null
    const evt = messages.find((m) => m.event === "compact")
    return evt?.data as { original_messages: number; kept_messages: number } | null
  }, [messages, modeMatches])

  const hasRichHistory = !hasLiveMessages && allHistoryTurns !== null

  // Sidebar only shown during live DAG streaming (React mode no longer uses sidebar)
  const showSidebar = hasLiveMessages && sidebarOpen && isWideScreen && mode === "dag"

  // Scroll the ScrollArea viewport to bottom (avoids scrollIntoView cascading to parent containers)
  const scrollViewportToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const root = scrollAreaRef.current
    if (!root) return
    const viewport = root.querySelector<HTMLElement>("[data-radix-scroll-area-viewport]")
    if (!viewport) return
    viewport.scrollTo({ top: viewport.scrollHeight, behavior })
  }, [])

  const handleSuggestionSelect = useCallback((q: string) => {
    onRunWithQuery(q)
    requestAnimationFrame(() => scrollViewportToBottom())
  }, [onRunWithQuery, scrollViewportToBottom])

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsDragging(true)

    const container = panelContainerRef.current
    if (!container) return

    const onMouseMove = (ev: MouseEvent) => {
      const rect = container.getBoundingClientRect()
      const ratio = 1 - (ev.clientX - rect.left) / rect.width
      const clamped = Math.max(0.15, Math.min(0.85, ratio))
      dragRatioRef.current = clamped
      setDragRatio(clamped)
    }

    const onMouseUp = () => {
      if (dragRatioRef.current !== null) {
        setCustomRatio(dragRatioRef.current)
      }
      dragRatioRef.current = null
      setDragRatio(null)
      setIsDragging(false)
      setResizeKey((k) => k + 1)
      document.removeEventListener("mousemove", onMouseMove)
      document.removeEventListener("mouseup", onMouseUp)
    }

    document.addEventListener("mousemove", onMouseMove)
    document.addEventListener("mouseup", onMouseUp)
  }, [setCustomRatio])

  // Track scroll position -- only auto-scroll when user is near bottom
  useEffect(() => {
    const root = scrollAreaRef.current
    if (!root) return
    const viewport = root.querySelector<HTMLElement>("[data-radix-scroll-area-viewport]")
    if (!viewport) return

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = viewport
      const nearBottom = scrollHeight - scrollTop - clientHeight < 80
      isNearBottomRef.current = nearBottom
      if (nearBottom) setShowScrollBtn(false)
    }

    viewport.addEventListener("scroll", handleScroll, { passive: true })
    return () => viewport.removeEventListener("scroll", handleScroll)
  }, [hasMessages])

  // Auto-scroll only when user is already near bottom.
  const msgCountRef = useRef(messages.length)
  useEffect(() => {
    if (messages.length <= msgCountRef.current) {
      msgCountRef.current = messages.length
      return
    }
    msgCountRef.current = messages.length
    if (isNearBottomRef.current) {
      scrollViewportToBottom()
    } else {
      setShowScrollBtn(true)
    }
  }, [messages, scrollViewportToBottom])

  // Reset scroll state on clear
  useEffect(() => {
    if (!hasMessages) {
      isNearBottomRef.current = true
      setShowScrollBtn(false)
    }
  }, [hasMessages])

  // Clear pending images when pending query is cleared
  useEffect(() => {
    if (!pendingQuery) setPendingImages([])
  }, [pendingQuery])

  const scrollToBottom = useCallback(() => {
    scrollViewportToBottom()
    setShowScrollBtn(false)
  }, [scrollViewportToBottom])

  const scrollInViewport = useCallback((selector: string) => {
    const root = scrollAreaRef.current
    if (!root) return
    const viewport = root.querySelector<HTMLElement>("[data-radix-scroll-area-viewport]")
    if (!viewport) return
    const el = viewport.querySelector<HTMLElement>(selector)
    if (!el) return
    const elRect = el.getBoundingClientRect()
    const viewportRect = viewport.getBoundingClientRect()
    const scrollOffset = elRect.top - viewportRect.top + viewport.scrollTop
    viewport.scrollTo({ top: Math.max(0, scrollOffset - 16), behavior: "smooth" })
  }, [])

  const scrollToStep = useCallback((stepId: string) => {
    scrollInViewport(`[data-step-id="${stepId}"]`)
  }, [scrollInViewport])

  // Fetch published agents on mount
  useEffect(() => {
    if (agentsLoaded) return
    agentApi.list(1, 50, "published").then((res) => {
      setAgents(res.items as AgentResponse[])
      setAgentsLoaded(true)
    }).catch(() => setAgentsLoaded(true))
  }, [agentsLoaded])

  // Auto-select agent from URL param
  useEffect(() => {
    if (!initialAgentId || !agentsLoaded || agents.length === 0) return
    if (selectedAgent?.id === initialAgentId) return // already selected
    const found = agents.find(a => a.id === initialAgentId)
    if (found) {
      onAgentChange(found)
    }
  }, [initialAgentId, agentsLoaded, agents, selectedAgent, onAgentChange])

  // Shared upload logic for both file input and paste
  const uploadFiles = useCallback(async (files: File[]) => {
    if (!files.length) return
    setIsUploading(true)
    try {
      for (const file of files) {
        const result = await fileApi.upload(file)
        let previewUrl: string | undefined
        if (file.type.startsWith("image/")) {
          previewUrl = URL.createObjectURL(file)
        }
        setAttachedFiles((prev) => [...prev, { ...result, previewUrl }])
      }
    } catch (err) {
      console.error("Upload failed:", err)
    } finally {
      setIsUploading(false)
    }
  }, [])

  // File input handler
  const handleFileUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files?.length) return
    await uploadFiles(Array.from(files))
    if (fileInputRef.current) fileInputRef.current.value = ""
  }, [uploadFiles])

  // Paste handler — extract images from clipboard
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items
    if (!items) return
    const imageFiles: File[] = []
    for (const item of Array.from(items)) {
      if (item.type.startsWith("image/")) {
        const file = item.getAsFile()
        if (file) imageFiles.push(file)
      }
    }
    if (imageFiles.length > 0) {
      e.preventDefault()
      uploadFiles(imageFiles)
    }
  }, [uploadFiles])

  const removeFile = useCallback((fileId: string) => {
    setAttachedFiles((prev) => {
      const file = prev.find((f) => f.file_id === fileId)
      if (file?.previewUrl) URL.revokeObjectURL(file.previewUrl)
      return prev.filter((f) => f.file_id !== fileId)
    })
  }, [])

  // Run with file content injection (text files) and image_ids passthrough
  const handleRunWithFiles = useCallback(() => {
    let finalQuery = query.trim()
    if (!finalQuery) return

    const textFiles = attachedFiles.filter((f) => !isImageFile(f))
    const imageFiles = attachedFiles.filter((f) => isImageFile(f))

    // Text files: inject content into query (existing behavior)
    if (textFiles.length > 0) {
      const fileContext = textFiles
        .map((f) => `File: ${f.filename}\n\`\`\`\n${f.content_preview || "[No preview available]"}\n\`\`\``)
        .join("\n\n")
      finalQuery = `Uploaded files:\n${fileContext}\n\n${finalQuery}`
    }

    // Image files: pass as image_ids parameter
    const imageIds = imageFiles.map((f) => f.file_id)

    // Save image info for pending display before clearing
    setPendingImages(imageFiles.map((f) => ({ file_id: f.file_id, filename: f.filename })))

    // Clean up preview URLs
    attachedFiles.forEach((f) => {
      if (f.previewUrl) URL.revokeObjectURL(f.previewUrl)
    })
    setAttachedFiles([])

    onRunWithQuery(finalQuery, imageIds.length > 0 ? imageIds : undefined)
    requestAnimationFrame(() => scrollViewportToBottom())
  }, [attachedFiles, query, onRunWithQuery, scrollViewportToBottom])

  const handleKeyDownWithFiles = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault()
        handleRunWithFiles()
      }
    },
    [handleRunWithFiles],
  )

  const statusText = (() => {
    if (!isRunning || !modeMatches) return null
    if (mode === "dag") {
      if (dagData.doneEvent) return null
      const roundSuffix = dagData.currentRound > 1 ? ` (Round ${dagData.currentRound})` : ""
      if (dagData.currentPhase === "replanning") return "Re-planning..."
      if (dagData.currentPhase === "planning") return `Planning${roundSuffix}...`
      if (dagData.currentPhase === "executing") return `Executing steps${roundSuffix}...`
      if (dagData.currentPhase === "analyzing") return `Analyzing${roundSuffix}...`
      return "Processing..."
    } else {
      if (reactItems.some(i => i.event === "done")) return null
      return "Processing..."
    }
  })()

  return (
    <div className="flex flex-1 flex-col overflow-hidden p-6 gap-4">
      {/* Output area / empty state */}
      {hasMessages ? (
        <div ref={panelContainerRef} className="flex flex-1 min-h-0">
          {/* Main content */}
          <div
            className={cn(
              "flex flex-col min-h-0 rounded-lg border border-border/50 bg-muted/10 overflow-hidden",
              !isDragging && "transition-all duration-300",
              !showSidebar && "flex-1 min-w-0"
            )}
            style={showSidebar ? { flex: `${1 - currentRatio} 1 0%`, minWidth: 0 } : undefined}
          >
            {/* Output header bar */}
            <div className="flex items-center shrink-0 px-4 py-3 border-b border-border/30 gap-1">
              <span className="text-sm font-medium">
                {hasLiveMessages || pendingQuery || hasRichHistory ? "Execution Log" : "History"}
              </span>
              {statusText && (
                <span className="flex items-center gap-1.5 ml-3 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  <span className="shiny-text">{statusText}</span>
                </span>
              )}
              <div className="flex-1" />
              {hasLiveMessages && isWideScreen && mode === "dag" && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setSidebarOpen(!sidebarOpen)}
                  className="h-7 w-7 text-muted-foreground"
                >
                  {sidebarOpen ? <PanelRightClose className="h-3.5 w-3.5" /> : <PanelRightOpen className="h-3.5 w-3.5" />}
                </Button>
              )}
            </div>

            <div className="relative flex-1 min-h-0">
              <ScrollArea ref={scrollAreaRef} className="h-full p-4">
                <div className="min-w-0 max-w-full space-y-4">
                  {/* Previous turns from DB (shown during both live and history mode) */}
                  {allHistoryTurns?.map((turn, idx) => {
                    const historyCompact = turn.sseMessages.find((m) => m.event === "compact")
                    const historyCompactData = historyCompact?.data as { original_messages: number; kept_messages: number } | undefined
                    return (
                      <Fragment key={idx}>
                        {historyCompactData && (
                          <CompactDivider
                            originalCount={historyCompactData.original_messages}
                            keptCount={historyCompactData.kept_messages}
                          />
                        )}
                        <HistoryTurn
                          userContent={turn.user?.content ?? null}
                          userMetadata={turn.user?.metadata}
                          sseMessages={turn.sseMessages}
                          mode={(activeConversation?.mode as AgentMode) ?? mode}
                          hideDagGraph={hasLiveMessages}
                        />
                      </Fragment>
                    )
                  })}
                  {/* Fallback: old messages without sse_events */}
                  {!allHistoryTurns && !hasLiveMessages && hasHistory && (
                    <HistoryMessages messages={activeConversation!.messages.filter(m => m.message_type !== "inject")} />
                  )}
                  {/* Compact divider -- shown when AI summarized older context */}
                  {compactEvent && (
                    <CompactDivider
                      originalCount={compactEvent.original_messages}
                      keptCount={compactEvent.kept_messages}
                    />
                  )}
                  {/* Current turn: user message + live output */}
                  {pendingQuery && (
                    <div className="flex items-center gap-3">
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
                        <User className="h-3.5 w-3.5 text-primary" />
                      </div>
                      <div className="flex-1">
                        <p className="text-sm text-foreground">{pendingQuery}</p>
                        {pendingImages.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {pendingImages.map((img) => (
                              <ImageThumbnail key={img.file_id} fileId={img.file_id} filename={img.filename} />
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                  {(hasLiveMessages || (isRunning && pendingQuery && mode === "react")) && (
                    mode === "react" ? (
                      <ReactOutput items={reactItems} isStreaming={isRunning && modeMatches} onSuggestionSelect={handleSuggestionSelect} />
                    ) : (
                      <DagOutput
                        planSteps={dagData.planSteps}
                        stepStates={dagData.stepStates}
                        analysisPhase={dagData.analysisPhase}
                        doneEvent={dagData.doneEvent}
                        currentPhase={dagData.currentPhase}
                        currentRound={dagData.currentRound}
                        injectEvents={dagData.injectEvents}
                        hideDagGraph
                        onSuggestionSelect={handleSuggestionSelect}
                      />
                    )
                  )}
                  {/* Optimistic inject messages not yet confirmed by SSE */}
                  {injectedMessages
                    .filter((msg) => {
                      // Keep optimistic messages that haven't been confirmed by SSE inject events.
                      // Prefer id-based matching; fall back to content matching.
                      return !messages.some(
                        (m) => {
                          if (m.event !== "inject") return false
                          const data = m.data as { content: string; id?: string }
                          if (msg.id && data.id) return data.id === msg.id
                          return data.content === msg.content
                        }
                      )
                    })
                    .map((msg) => (
                    <div key={msg.ts} className={`flex items-center gap-3 ${msg.id ? "inject-breathe" : "animate-pulse"}`}>
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
                        <User className="h-3.5 w-3.5 text-primary" />
                      </div>
                      <div className="flex-1">
                        <p className="text-sm text-foreground">{msg.content}</p>
                        <div className="flex items-center gap-2 mt-0.5">
                          {msg.id ? (
                            <button
                              onClick={() => onRecallInject(msg)}
                              className="inline-flex items-center gap-1 text-[10px] text-muted-foreground/50 hover:text-destructive transition-colors"
                            >
                              <Undo2 className="h-2.5 w-2.5" />
                              Recall
                            </button>
                          ) : (
                            <span className="flex items-center gap-1 text-[10px] text-muted-foreground/50">
                              <Loader2 className="h-2.5 w-2.5 animate-spin" />
                              Queued...
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </ScrollArea>
              {showScrollBtn && (
                <button
                  onClick={scrollToBottom}
                  className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full border border-border/60 bg-background/90 px-3 py-1.5 text-xs text-muted-foreground shadow-md backdrop-blur-sm transition-colors hover:text-foreground hover:border-border"
                >
                  <ArrowDown className="h-3 w-3" />
                  New updates
                </button>
              )}
            </div>
          </div>

          {/* Resize handle */}
          {showSidebar && (
            <div
              className="shrink-0 w-3 cursor-col-resize flex items-center justify-center group"
              onMouseDown={handleDragStart}
            >
              <div className="w-0.5 h-8 rounded-full bg-border group-hover:bg-primary/40 group-active:bg-primary/60 transition-colors" />
            </div>
          )}

          {/* Right sidebar */}
          {showSidebar && (
            <RightSidebar
              title="Execution Plan"
              badge={dagData.planSteps?.length}
              expanded={sidebarExpanded}
              onToggleExpand={() => { setSidebarExpanded(!sidebarExpanded); setCustomRatio(null) }}
              className={cn(!isDragging && "transition-all duration-300")}
              style={{ flex: `${currentRatio} 1 0%`, minWidth: 0 }}
            >
              {dagData.planSteps && dagData.planSteps.length > 0 ? (
                <DagFlowGraph
                  planSteps={dagData.planSteps}
                  stepStates={dagData.stepStates}
                  mode="sidebar"
                  expanded={sidebarExpanded}
                  resizeKey={resizeKey}
                  onStepClick={scrollToStep}
                />
              ) : (
                <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                  Waiting for plan...
                </div>
              )}
            </RightSidebar>
          )}
        </div>
      ) : (
        <div className="flex flex-1 flex-col justify-center min-h-0 w-full">
          <Examples
            mode={mode}
            language={language}
            onLanguageChange={onLanguageChange}
            onSelect={onExampleSelect}
            disabled={isRunning}
            agentPrompts={selectedAgent?.suggested_prompts}
            agentName={selectedAgent?.name}
            agentIcon={selectedAgent?.icon}
          />
        </div>
      )}

      {/* Input area -- pinned to bottom */}
      <div className="shrink-0 space-y-2">
        {/* Attached files */}
        {attachedFiles.length > 0 && (
          <div className="flex flex-wrap gap-2 pb-2">
            {attachedFiles.map((f) => {
              const isImage = isImageFile(f)
              return (
                <div
                  key={f.file_id}
                  className="flex items-center gap-1.5 rounded-md border border-border/60 bg-muted/30 px-2 py-1 text-xs"
                >
                  {isImage && f.previewUrl ? (
                    <img
                      src={f.previewUrl}
                      alt={f.filename}
                      className="h-8 w-8 rounded object-cover"
                    />
                  ) : (
                    <Paperclip className="h-3 w-3 text-muted-foreground" />
                  )}
                  <span className="max-w-[150px] truncate">{f.filename}</span>
                  <span className="text-muted-foreground">({formatFileSize(f.size)})</span>
                  <button
                    onClick={() => removeFile(f.file_id)}
                    className="ml-0.5 text-muted-foreground hover:text-foreground"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              )
            })}
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileUpload}
          accept=".txt,.md,.py,.js,.json,.csv,.pdf,.docx,.html,.htm,.xlsx,.jpg,.jpeg,.png,.gif,.webp,.svg,image/*"
        />
        <div className="flex items-end gap-2">
          <Textarea
            ref={textareaRef}
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            onCompositionStart={() => { composingRef.current = true; setComposing(true) }}
            onCompositionEnd={(e) => { composingRef.current = false; setComposing(false); onQueryChange(e.currentTarget.value) }}
            onKeyDown={handleKeyDownWithFiles}
            onPaste={handlePaste}
            placeholder={
              isRunning
                ? "Send a message to interrupt the agent..."
                : mode === "react"
                  ? "Ask the ReAct agent to solve a problem..."
                  : "Describe a multi-step task for the DAG planner..."
            }
            className="min-h-[72px] max-h-[160px] resize-none"
          />
          <Button
            onClick={isRunning ? ((query.trim() || composing) ? handleRunWithFiles : onAbort) : handleRunWithFiles}
            disabled={!isRunning && !query.trim() && !composing}
            className="h-[72px] w-16 shrink-0"
            variant={isRunning && !query.trim() && !composing ? "destructive" : "default"}
          >
            {isRunning && !query.trim() && !composing ? (
              <Square className="h-4 w-4" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
        {/* Mode toggle toolbar */}
        <div className="flex items-center gap-2">
          {/* "+" dropdown menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className={cn(
                  "inline-flex items-center justify-center rounded-full px-2 py-1 transition-colors",
                  "border border-border/60 bg-muted/40 text-muted-foreground hover:bg-muted/70 hover:text-foreground select-none"
                )}
              >
                {isUploading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Plus className="h-3.5 w-3.5" />
                )}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="start">
              <DropdownMenuItem
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
              >
                <Paperclip className="h-4 w-4" />
                Upload files
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <button
            type="button"
            disabled={isRunning}
            onClick={() => onModeChange(mode === "react" ? "dag" : "react")}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors",
              "border select-none",
              isRunning && "opacity-50 cursor-not-allowed",
              mode === "react"
                ? "border-border/60 bg-muted/40 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                : "border-amber-500/40 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20"
            )}
          >
            {mode === "react" ? (
              <Zap className="h-3 w-3" />
            ) : (
              <GitBranch className="h-3 w-3" />
            )}
            {mode === "react" ? "ReAct" : "DAG"}
          </button>
          {/* Agent selector */}
          {agents.length > 0 && (
            <Popover open={agentSelectorOpen} onOpenChange={setAgentSelectorOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  disabled={isRunning}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors",
                    "border select-none",
                    selectedAgent
                      ? "border-primary/40 text-primary"
                      : "border-border/60 text-muted-foreground",
                    isRunning
                      ? "opacity-50 cursor-not-allowed"
                      : "hover:bg-muted/70 hover:text-foreground"
                  )}
                >
                  <User className="h-3 w-3" />
                  {selectedAgent ? selectedAgent.name : "No Agent"}
                  <ChevronsUpDown className="h-3 w-3 opacity-50" />
                </button>
              </PopoverTrigger>
              <PopoverContent className="w-[200px] p-0" side="top" align="start">
                <Command>
                  <CommandInput placeholder="Search agents..." />
                  <CommandList>
                    <CommandEmpty>No agent found.</CommandEmpty>
                    <CommandGroup>
                      <CommandItem
                        value="__no_agent__"
                        onSelect={() => {
                          onAgentChange(null)
                          setAgentSelectorOpen(false)
                        }}
                      >
                        <Check
                          className={cn(
                            "h-3.5 w-3.5",
                            !selectedAgent ? "opacity-100" : "opacity-0"
                          )}
                        />
                        No Agent
                      </CommandItem>
                      {agents.map((a) => (
                        <CommandItem
                          key={a.id}
                          value={a.name}
                          onSelect={() => {
                            onAgentChange(a)
                            setAgentSelectorOpen(false)
                          }}
                        >
                          <Check
                            className={cn(
                              "h-3.5 w-3.5",
                              selectedAgent?.id === a.id ? "opacity-100" : "opacity-0"
                            )}
                          />
                          {a.name}
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
          )}
        </div>
      </div>
    </div>
  )
}
