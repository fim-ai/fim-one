"use client"

import { useState, useCallback, useRef, useEffect, useMemo, Fragment } from "react"
import { useTranslations } from "next-intl"
import { useRouter, useSearchParams, usePathname } from "next/navigation"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Send, Loader2, PanelRightOpen, PanelRightClose, ArrowDown, Square, Zap, GitBranch, Bot, Paperclip, X, Plus, ChevronsUpDown, Check, Undo2, RotateCcw, Download, FileText, ChevronDown, ChevronUp } from "lucide-react"
import { UserAvatar } from "@/components/shared/user-avatar"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error-utils"
import { useSSE } from "@/hooks/use-sse"
import { useSlashCommands } from "@/hooks/use-slash-commands"
import { SlashCommandMenu } from "@/components/playground/slash-command-menu"
import { ExportDialog } from "@/components/playground/export-dialog"
import { CollapsibleText } from "@/components/playground/collapsible-text"
import { ClipMessageContent } from "@/components/playground/clip-message-content"
import type { ClipMessageMetadata } from "@/components/playground/clip-message-content"
import { useDagSteps } from "@/hooks/use-dag-steps"
import { useReactSteps } from "@/hooks/use-react-steps"
import { useMediaQuery } from "@/hooks/use-media-query"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { useAuth } from "@/contexts/auth-context"
import { useConversation } from "@/contexts/conversation-context"
import { agentApi, fileApi, chatApi } from "@/lib/api"
import { getApiBaseUrl, getApiDirectUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
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
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip"
import { ReactOutput } from "@/components/playground/react-output"
import { DagOutput, type DagOutputHandle } from "@/components/playground/dag-output"
import { Examples } from "@/components/playground/examples"
import { RightSidebar } from "@/components/playground/right-sidebar"
import { DagFlowGraph } from "@/components/dag/dag-flow-graph"
import { HistoryMessages } from "@/components/playground/history-messages"
import { reconstructSSEMessages } from "@/lib/sse-utils"
import type { SSEMessage } from "@/hooks/use-sse"
import type { MessageResponse } from "@/types/conversation"
import type { AgentResponse } from "@/types/agent"
import type { FileUploadResponse } from "@/types/file"
import type { AgentMode } from "@/components/playground/examples"


interface PastedClip {
  id: string
  content: string
  preview: string
  charCount: number
}

interface PlaygroundPageProps {
  /** When true, this is a fresh "new chat" page — no conversation should be loaded from URL */
  isNewChat?: boolean
  /** When true, skip auth redirect and URL sync (used inside BuilderDialog) */
  embedded?: boolean
  /** Close callback for embedded mode */
  onClose?: () => void
  /** Pre-select a specific agent on mount */
  initialAgentId?: string
  /** Called after each assistant turn completes (streaming ends) */
  onTurnComplete?: () => void
}

export function PlaygroundPage({ isNewChat, embedded, onClose, initialAgentId, onTurnComplete }: PlaygroundPageProps) {
  const t = useTranslations("playground")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
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
    animateTitle,
    clearActive,
  } = useConversation()

  const [mode, setMode] = useState<AgentMode>("react")
  const [selectedAgent, setSelectedAgent] = useState<AgentResponse | null>(null)
  const [query, setQuery] = useState("")
  const [sourceMode, setSourceMode] = useState<AgentMode | null>(null)
  const [pendingQuery, setPendingQuery] = useState<string | null>(null)
  const [pendingMode, setPendingMode] = useState<AgentMode | null>(null)
  const { messages, isRunning, isError, start, reset, abort } = useSSE()
  const [injectedMessages, setInjectedMessages] = useState<{id?: string; content: string; ts: number}[]>([])
  const failedInjectRef = useRef<string | null>(null)

  // Read agent param from URL for quick chat link
  const agentParam = isNewChat ? searchParams.get("agent") : null

  // Stable callback — avoids auto-select effect re-running on every render
  const handleAgentChange = useCallback((agent: AgentResponse | null) => {
    setSelectedAgent(agent)
    // Sync mode from agent's default when no active conversation
    if (!activeId && agent?.execution_mode) {
      setMode(agent.execution_mode)
    }
  }, [activeId])

  // Ref to track conversation IDs we created ourselves (via send),
  // so the "switch conversation" effect doesn't reset SSE for them.
  const selfCreatedIdRef = useRef<string | null>(null)

  // Auth guard
  useEffect(() => {
    if (embedded) return
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [embedded, authLoading, user, router])

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
    if (embedded) return
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
      // Extract auto-generated title from title event (new) or done event (backward compat)
      const titleMsg = messages.find((m) => m.event === "title")
      const doneMsg = messages.find((m) => m.event === "done")
      const doneTitle = titleMsg
        ? (titleMsg.data as { title: string }).title
        : (doneMsg?.data as { title?: string } | undefined)?.title
      if (doneTitle && activeId) {
        animateTitle(activeId, doneTitle)
      }
      loadConversations()
      onTurnComplete?.()
      // Restore failed inject content to input box for user to re-send
      const queued = failedInjectRef.current
      if (queued) {
        failedInjectRef.current = null
        setQuery(queued)
        toast.warning(t("injectFailed"))
      }
    }
  }, [isRunning]) // eslint-disable-line react-hooks/exhaustive-deps

  // (Agent selection no longer overrides mode -- user controls mode independently)

  const runWithQuery = useCallback(
    async (q: string, imageIds?: string[], userMetadata?: Record<string, unknown>) => {
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
        } catch (err) {
          setInjectedMessages(prev => prev.filter(m => m.ts !== ts))
          const msg = getErrorMessage(err, tError)
          toast.error(msg)
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
      // SSE connects directly to backend via POST, bypassing Next.js
      // rewrite proxy which buffers streaming responses.
      const url = `${getApiDirectUrl()}/api/${endpoint}`
      const body: Record<string, unknown> = {
        q: trimmed,
        conversation_id: convId,
      }
      const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY)
      if (accessToken) body.token = accessToken
      if (selectedAgent?.id) body.agent_id = selectedAgent.id
      if (imageIds?.length) body.image_ids = imageIds.join(",")
      if (userMetadata) body.user_metadata = JSON.stringify(userMetadata)
      setSourceMode(mode)
      start(url, { body, onError: (err) => toast.error(getErrorMessage(err, tError)) })
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

  if (!embedded && (authLoading || !user)) return null

  return (
    <div className="flex h-full flex-col">
      <PlaygroundContent
        mode={mode}
        sourceMode={sourceMode}
        query={query}
        pendingQuery={pendingQuery}
        messages={messages}
        isRunning={isRunning}
        isError={isError}
        activeConversation={activeConversation}
        selectedAgent={selectedAgent}
        injectedMessages={injectedMessages}
        onRecallInject={handleRecallInject}
        onAgentChange={handleAgentChange}
        onQueryChange={setQuery}
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
        onNewChat={() => {
          reset()
          setPendingQuery(null)
    
          setSourceMode(null)
          clearActive()
          if (!embedded) router.push("/new")
        }}
        isNewChat={isNewChat}
        initialAgentId={initialAgentId ?? agentParam}
        embedded={embedded}
      />

      {/* Mode switch confirmation dialog */}
      <Dialog open={pendingMode !== null} onOpenChange={(open) => { if (!open) setPendingMode(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("switchModeTitle", { mode: pendingMode === "react" ? t("modeStandard") : t("modePlanner") })}</DialogTitle>
            <DialogDescription>
              {t("switchModeDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingMode(null)}>
              {tc("cancel")}
            </Button>
            <Button className="px-6" onClick={() => {
              if (pendingMode) {
                reset()
                setPendingQuery(null)
          
                setSourceMode(null)
                clearActive()
                setMode(pendingMode)
                // Navigate to /new when switching mode (skip in embedded mode)
                if (!embedded) router.push("/new")
              }
              setPendingMode(null)
            }}>
              {t("switchButton")}
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
          <DialogContent className="max-w-3xl max-h-[90vh] overflow-hidden flex flex-col gap-3 pt-4">
            <a
              href={blobUrl}
              download={filename}
              className="absolute right-12 top-4 rounded-sm opacity-70 hover:opacity-100 transition-opacity text-foreground"
              onClick={(e) => e.stopPropagation()}
            >
              <Download className="h-4 w-4" />
            </a>
            <DialogTitle className="leading-normal pb-1 pr-24 truncate text-xs font-medium">{filename}</DialogTitle>
            <img src={blobUrl} alt={filename} className="max-h-[calc(90vh-6rem)] max-w-full w-auto mx-auto block rounded object-contain" />
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
  const { user } = useAuth()
  const userFallback = (user?.display_name || user?.email || "U").charAt(0).toUpperCase()
  const { items: reactItems, streamingAnswer: reactStreamingAnswer, suggestions: reactSuggestions } = useReactSteps(sseMessages, false)
  const dagData = useDagSteps(sseMessages, false)

  // Detect clip metadata in user message
  const hasClipMeta = Array.isArray(userMetadata?.clips) && (userMetadata.clips as unknown[]).length > 0
  const clipMetadata: ClipMessageMetadata | null = hasClipMeta
    ? {
        clips: userMetadata!.clips as ClipMessageMetadata["clips"],
        userQuery: (userMetadata!.userQuery as string) ?? "",
      }
    : null

  return (
    <>
      {userContent && (
        <div className={cn("flex gap-3", !clipMetadata && "items-center")}>
          <UserAvatar avatar={user?.avatar} userId={user?.id} fallback={userFallback} className="h-7 w-7 shrink-0" iconClassName="h-3.5 w-3.5" />
          <div className="flex-1">
            {clipMetadata ? (
              <ClipMessageContent metadata={clipMetadata} />
            ) : (
              <CollapsibleText content={userContent} className="text-sm text-foreground whitespace-pre-wrap" />
            )}
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
        <ReactOutput items={reactItems} streamingAnswer={reactStreamingAnswer} suggestions={reactSuggestions} />
      ) : (
        <DagOutput
          planSteps={dagData.planSteps}
          stepStates={dagData.stepStates}
          analysisPhase={dagData.analysisPhase}
          doneEvent={dagData.doneEvent}
          currentPhase={dagData.currentPhase}
          currentRound={dagData.currentRound}
          previousRounds={dagData.previousRounds}
          injectEvents={dagData.injectEvents}
          streamingAnswer={dagData.streamingAnswer}
          answerDone={dagData.answerDone}
          suggestions={dagData.suggestions}
          hideDagGraph={hideDagGraph}
        />
      )}
    </>
  )
}

/** Subtle divider shown when the backend compacted (summarized) older conversation context. */
function CompactDivider({ originalCount, keptCount }: { originalCount: number; keptCount: number }) {
  const t = useTranslations("playground")
  return (
    <div className="flex items-center gap-3 py-2">
      <div className="flex-1 border-t border-dashed border-border/50" />
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground/70 select-none">
        <span>&#9986;</span>
        <span>{t("compactDivider", { count: originalCount - keptCount })}</span>
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
  messages: ReturnType<typeof useSSE>["messages"]
  isRunning: boolean
  isError: boolean
  activeConversation: ReturnType<typeof useConversation>["activeConversation"]
  selectedAgent: AgentResponse | null
  injectedMessages: {id?: string; content: string; ts: number}[]
  onRecallInject: (msg: {id?: string; content: string; ts: number}) => void
  onAgentChange: (agent: AgentResponse | null) => void
  onQueryChange: (q: string) => void
  onModeChange: (mode: AgentMode) => void
  onRunWithQuery: (q: string, imageIds?: string[], userMetadata?: Record<string, unknown>) => void
  onAbort: () => void
  onExampleSelect: (example: string) => void
  onNewChat: () => void
  isNewChat?: boolean
  initialAgentId?: string | null
  embedded?: boolean
}

function PlaygroundContent({
  mode,
  sourceMode,
  query,
  pendingQuery,
  messages,
  isRunning,
  isError,
  activeConversation,
  selectedAgent,
  injectedMessages,
  onRecallInject,
  onAgentChange,
  onQueryChange,
  onModeChange,
  onRunWithQuery,
  onAbort,
  onExampleSelect,
  onNewChat,
  isNewChat,
  initialAgentId,
  embedded,
}: PlaygroundContentProps) {
  const t = useTranslations("playground")
  const { user } = useAuth()
  const userFallback = (user?.display_name || user?.email || "U").charAt(0).toUpperCase()
  const modeMatches = sourceMode === mode
  const hasLiveMessages = modeMatches && messages.length > 0
  const hasHistory = !!(activeConversation?.messages && activeConversation.messages.length > 0)
  const hasMessages = hasLiveMessages || hasHistory || !!pendingQuery
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isNearBottomRef = useRef(true)
  const dagOutputRef = useRef<DagOutputHandle>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const composingRef = useRef(false)
  const [composing, setComposing] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)

  // Clip metadata for the current pending query (cleared when pendingQuery clears)
  const [pendingClipMetadata, setPendingClipMetadata] = useState<ClipMessageMetadata | null>(null)

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

  // Pasted clips (long text folded into cards)
  const [clips, setClips] = useState<PastedClip[]>([])
  const [expandedClips, setExpandedClips] = useState<Set<string>>(new Set())

  // Slash commands
  const slashCommands = useSlashCommands({
    query,
    isComposing: composing,
    agents,
    mode,
    isRunning,
    onNewChat,
    onAgentChange: (agentId) => {
      if (!agentId) {
        onAgentChange(null)
      } else {
        const agent = agents.find((a) => a.id === agentId)
        if (agent) onAgentChange(agent)
      }
    },
    onModeChange,
    onQueryChange,
    onAbort,
  })

  // Priority: active drag > custom drag (persisted) > expand preset > normal preset
  const NORMAL_RATIO = 0.3
  const EXPANDED_RATIO = 0.7
  const currentRatio = dragRatio ?? customRatio ?? (sidebarExpanded ? EXPANDED_RATIO : NORMAL_RATIO)

  // Parse data at this level via hooks
  const dagData = useDagSteps(messages, isRunning)
  const { items: reactItems, streamingAnswer: reactStreamingAnswer, suggestions: reactSuggestions } = useReactSteps(messages, isRunning)

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

  const handleSuggestionSelect = useCallback((q: string) => {
    // Clear any attached files/images/clips so they don't show up as "still attached"
    // on the follow-up turn. The suggestion never sends them to the backend anyway.
    setAttachedFiles([])
    setPendingImages([])
    setClips([])
    setExpandedClips(new Set())
    setPendingClipMetadata(null)
    onRunWithQuery(q)
    requestAnimationFrame(() => scrollViewportToBottom())
  }, [onRunWithQuery, scrollViewportToBottom, setAttachedFiles, setPendingImages])

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

  // Streaming answer now pushes content naturally — no need to snap-scroll
  // to the result block on completion.

  // Reset scroll state on clear
  useEffect(() => {
    if (!hasMessages) {
      isNearBottomRef.current = true
      setShowScrollBtn(false)
    }
  }, [hasMessages])

  // Clear pending images and clip metadata when pending query is cleared
  useEffect(() => {
    if (!pendingQuery) {
      setPendingImages([])
      setPendingClipMetadata(null)
    }
  }, [pendingQuery])

  const scrollToBottom = useCallback(() => {
    scrollViewportToBottom()
    setShowScrollBtn(false)
  }, [scrollViewportToBottom])

  const scrollToStep = useCallback((stepId: string) => {
    // Expand the collapsed step section first (in case it's folded after completion)
    dagOutputRef.current?.expandSteps()
    // Double-rAF: first frame lets React commit the expansion re-render,
    // second frame waits for browser layout so bounding rects are correct.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => scrollInViewport(`[data-step-id="${stepId}"]`))
    )
  }, [scrollInViewport])

  // Fetch published agents on mount
  useEffect(() => {
    if (agentsLoaded) return
    agentApi.list(1, 50, "published").then((res) => {
      setAgents((res.items as AgentResponse[]).filter(a => !a.name.startsWith("__builder_")))
      setAgentsLoaded(true)
    }).catch(() => setAgentsLoaded(true))
  }, [agentsLoaded])

  // Keep a ref to onAgentChange so the auto-select effect doesn't need it as a dep
  const onAgentChangeRef = useRef(onAgentChange)
  useEffect(() => { onAgentChangeRef.current = onAgentChange }, [onAgentChange])

  // Auto-select agent from URL param
  useEffect(() => {
    if (!initialAgentId || !agentsLoaded) return
    if (selectedAgent?.id === initialAgentId) return // already selected
    const found = agents.find(a => a.id === initialAgentId)
    if (found) {
      onAgentChangeRef.current(found)
    } else {
      // Agent not in published list (e.g. builder agents) — fetch directly
      agentApi.get(initialAgentId).then(agent => {
        onAgentChangeRef.current(agent)
      }).catch(() => {})
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialAgentId, agentsLoaded, agents, selectedAgent])

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

  // Paste handler — extract images from clipboard & fold long text into clips
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
      return
    }

    // Long text paste → fold into clip card
    const text = e.clipboardData?.getData("text/plain")
    if (text && text.length > 500) {
      e.preventDefault()
      const clip: PastedClip = {
        id: crypto.randomUUID(),
        content: text,
        preview: text.slice(0, 80).replace(/\n/g, " ") + (text.length > 80 ? "..." : ""),
        charCount: text.length,
      }
      setClips((prev) => [...prev, clip])
    }
  }, [uploadFiles])

  const removeFile = useCallback((fileId: string) => {
    setAttachedFiles((prev) => {
      const file = prev.find((f) => f.file_id === fileId)
      if (file?.previewUrl) URL.revokeObjectURL(file.previewUrl)
      return prev.filter((f) => f.file_id !== fileId)
    })
  }, [])

  const removeClip = useCallback((clipId: string) => {
    setClips((prev) => prev.filter((c) => c.id !== clipId))
    setExpandedClips((prev) => {
      const next = new Set(prev)
      next.delete(clipId)
      return next
    })
  }, [])

  const toggleClipExpand = useCallback((clipId: string) => {
    setExpandedClips((prev) => {
      const next = new Set(prev)
      if (next.has(clipId)) {
        next.delete(clipId)
      } else {
        next.add(clipId)
      }
      return next
    })
  }, [])

  // Run with file content injection (text files), clips, and image_ids passthrough
  const handleRunWithFiles = useCallback(() => {
    // Reset IME composing state — compositionEnd may not fire when
    // the user clicks the Send button instead of pressing Enter.
    composingRef.current = false
    setComposing(false)

    let finalQuery = query.trim()
    if (!finalQuery && clips.length === 0) return

    const textFiles = attachedFiles.filter((f) => !isImageFile(f))
    const imageFiles = attachedFiles.filter((f) => isImageFile(f))

    // Build clip metadata for persistence & rendering
    let clipMetadata: ClipMessageMetadata | null = null
    if (clips.length > 0) {
      clipMetadata = {
        clips: clips.map((c) => ({
          content: c.content,
          preview: c.preview,
          charCount: c.charCount,
        })),
        userQuery: finalQuery, // the text the user typed (before clip injection)
      }
    }

    // Clips: prepend pasted content before user query
    if (clips.length > 0) {
      const clipContext = clips
        .map((c, i) => {
          const label = clips.length > 1
            ? `${t("pastedContent")} ${i + 1}:`
            : `${t("pastedContent")}:`
          return `${label}\n\`\`\`\n${c.content}\n\`\`\``
        })
        .join("\n\n")
      finalQuery = finalQuery
        ? `${clipContext}\n\n${finalQuery}`
        : clipContext
    }

    // Text files: inject content into query (existing behavior)
    if (textFiles.length > 0) {
      const fileContext = textFiles
        .map((f) => `File: ${f.filename}\n\`\`\`\n${f.content_preview || "[No preview available]"}\n\`\`\``)
        .join("\n\n")
      finalQuery = `Uploaded files:\n${fileContext}\n\n${finalQuery}`
    }

    // Image files: pass as image_ids parameter
    const imageIds = imageFiles.map((f) => f.file_id)

    // Save image info and clip metadata for pending display before clearing
    setPendingImages(imageFiles.map((f) => ({ file_id: f.file_id, filename: f.filename })))
    setPendingClipMetadata(clipMetadata)

    // Clean up preview URLs
    attachedFiles.forEach((f) => {
      if (f.previewUrl) URL.revokeObjectURL(f.previewUrl)
    })
    setAttachedFiles([])
    setClips([])
    setExpandedClips(new Set())

    // Build user_metadata to persist with the message
    const userMetadata: Record<string, unknown> | undefined = clipMetadata
      ? { clips: clipMetadata.clips, userQuery: clipMetadata.userQuery }
      : undefined

    onRunWithQuery(finalQuery, imageIds.length > 0 ? imageIds : undefined, userMetadata)
    requestAnimationFrame(() => scrollViewportToBottom())
  }, [attachedFiles, clips, query, onRunWithQuery, scrollViewportToBottom, t])

  const handleKeyDownWithFiles = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Let slash command menu handle first
      if (slashCommands.handleKeyDown(e)) return
      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault()
        handleRunWithFiles()
      }
    },
    [handleRunWithFiles, slashCommands.handleKeyDown],
  )

  const statusText = (() => {
    if (!isRunning || !modeMatches) return null
    if (mode === "dag") {
      if (dagData.doneEvent) return null
      if (dagData.currentPhase === "replanning") return t("statusReplanning")
      if (dagData.currentPhase === "planning") return dagData.currentRound > 1 ? t("statusPlanningRound", { round: dagData.currentRound }) : t("statusPlanning")
      if (dagData.currentPhase === "executing") return dagData.currentRound > 1 ? t("statusExecutingRound", { round: dagData.currentRound }) : t("statusExecuting")
      if (dagData.currentPhase === "analyzing") return dagData.currentRound > 1 ? t("statusAnalyzingRound", { round: dagData.currentRound }) : t("statusAnalyzing")
      return t("statusProcessing")
    } else {
      if (reactItems.some(i => i.event === "done")) return null
      return t("statusProcessing")
    }
  })()

  // True when a task was submitted but aborted/errored before completing (current session)
  const wasStopped = !isRunning && !!pendingQuery && (
    isError || (hasLiveMessages && (
      mode === "dag"
        ? !dagData.doneEvent
        : !reactItems.some(i => i.event === "done")
    ))
  )

  // After page refresh: last message is a user message with no assistant reply → was stopped
  const refreshStoppedQuery = useMemo(() => {
    if (isRunning || hasLiveMessages) return null
    const msgs = activeConversation?.messages
    if (!msgs?.length) return null
    const last = msgs[msgs.length - 1]
    if (last.role === "user" && last.message_type !== "inject") return last.content
    return null
  }, [isRunning, hasLiveMessages, activeConversation?.messages])

  const retryQuery = wasStopped ? pendingQuery : refreshStoppedQuery

  return (
    <>
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
              <span className="text-xs font-medium">
                {hasLiveMessages || pendingQuery || hasRichHistory ? t("executionLog") : t("history")}
              </span>
              {statusText && (
                <span className="flex items-center gap-1.5 ml-3 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  <span className="shiny-text">{statusText}</span>
                </span>
              )}
              {retryQuery && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onRunWithQuery(retryQuery)}
                  className="ml-2 h-6 px-2 text-xs gap-1.5 text-muted-foreground hover:text-foreground"
                >
                  <RotateCcw className="h-3 w-3" />
                  {t("retryButton")}
                </Button>
              )}
              <div className="flex-1" />
              {activeConversation && !isRunning && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setExportOpen(true)}
                  className="h-7 w-7 text-muted-foreground"
                >
                  <Download className="h-3.5 w-3.5" />
                </Button>
              )}
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
                    <div className={cn("flex gap-3", !pendingClipMetadata && "items-center")}>
                      <UserAvatar avatar={user?.avatar} userId={user?.id} fallback={userFallback} className="h-7 w-7 shrink-0" iconClassName="h-3.5 w-3.5" />
                      <div className="flex-1">
                        {pendingClipMetadata ? (
                          <ClipMessageContent metadata={pendingClipMetadata} />
                        ) : (
                          <CollapsibleText content={pendingQuery} className="text-sm text-foreground whitespace-pre-wrap" />
                        )}
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
                    <div data-live-output>
                      {mode === "react" ? (
                        <ReactOutput items={reactItems} isStreaming={isRunning && modeMatches} streamingAnswer={reactStreamingAnswer} suggestions={reactSuggestions} onSuggestionSelect={handleSuggestionSelect} />
                      ) : (
                        <DagOutput
                          ref={dagOutputRef}
                          planSteps={dagData.planSteps}
                          stepStates={dagData.stepStates}
                          analysisPhase={dagData.analysisPhase}
                          doneEvent={dagData.doneEvent}
                          currentPhase={dagData.currentPhase}
                          currentRound={dagData.currentRound}
                          previousRounds={dagData.previousRounds}
                          injectEvents={dagData.injectEvents}
                          streamingAnswer={dagData.streamingAnswer}
                          answerDone={dagData.answerDone}
                          suggestions={dagData.suggestions}
                          hideDagGraph
                          onSuggestionSelect={handleSuggestionSelect}
                        />
                      )}
                    </div>
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
                      <UserAvatar avatar={user?.avatar} userId={user?.id} fallback={userFallback} className="h-7 w-7" iconClassName="h-3.5 w-3.5" />
                      <div className="flex-1">
                        <p className="text-sm text-foreground">{msg.content}</p>
                        <div className="flex items-center gap-2 mt-0.5">
                          {msg.id ? (
                            <button
                              onClick={() => onRecallInject(msg)}
                              className="inline-flex items-center gap-1 text-[10px] text-muted-foreground/50 hover:text-destructive transition-colors"
                            >
                              <Undo2 className="h-2.5 w-2.5" />
                              {t("recall")}
                            </button>
                          ) : (
                            <span className="flex items-center gap-1 text-[10px] text-muted-foreground/50">
                              <Loader2 className="h-2.5 w-2.5 animate-spin" />
                              {t("queued")}
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
                  {t("newUpdates")}
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
              title={t("executionPlan")}
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
                  {t("waitingForPlan")}
                </div>
              )}
            </RightSidebar>
          )}
        </div>
      ) : (
        <div className="flex flex-1 flex-col justify-center min-h-0 w-full">
          {!embedded && (
            <Examples
              mode={mode}
              onSelect={onExampleSelect}
              disabled={isRunning}
              agentPrompts={selectedAgent?.suggested_prompts}
              agentName={selectedAgent?.name}
              agentIcon={selectedAgent?.icon}
            />
          )}
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
        {/* Pasted clips */}
        {clips.length > 0 && (
          <div className="flex flex-col gap-2 pb-2">
            {clips.map((clip) => {
              const isExpanded = expandedClips.has(clip.id)
              return (
                <div
                  key={clip.id}
                  className="rounded-lg border border-border/60 bg-muted/50 text-xs overflow-hidden"
                >
                  <div className="flex items-center">
                    <button
                      type="button"
                      onClick={() => toggleClipExpand(clip.id)}
                      className="flex flex-1 min-w-0 items-center gap-2 px-3 py-2 cursor-pointer hover:bg-muted/80 transition-colors text-left"
                      aria-label={isExpanded ? t("collapseClip") : t("expandClip")}
                    >
                      <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="flex-1 min-w-0 truncate text-foreground">{clip.preview}</span>
                      <span className="shrink-0 text-muted-foreground">({clip.charCount.toLocaleString()} {t("chars")})</span>
                      {isExpanded ? (
                        <ChevronUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => removeClip(clip.id)}
                      className="shrink-0 px-2 py-2 text-muted-foreground hover:text-foreground transition-colors"
                      aria-label={t("removeClip")}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                  {isExpanded && (
                    <div className="border-t border-border/40 bg-muted px-3 py-2 max-h-[200px] overflow-y-auto">
                      <pre className="whitespace-pre-wrap break-words font-mono text-xs text-foreground/80">{clip.content}</pre>
                    </div>
                  )}
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
        <div className="relative flex items-end gap-2">
          <SlashCommandMenu
            isOpen={slashCommands.isOpen}
            filteredCommands={slashCommands.filteredCommands}
            subMenuCommand={slashCommands.subMenuCommand}
            subMenuItems={slashCommands.subMenuItems}
            selectedIndex={slashCommands.selectedIndex}
            onSelect={slashCommands.executeCommand}
            onQueryChange={onQueryChange}
          />
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
                ? t("placeholderInterrupt")
                : mode === "react"
                  ? t("placeholderReact")
                  : t("placeholderDag")
            }
            className="min-h-[72px] max-h-[160px] resize-none"
          />
          <Button
            onClick={isRunning ? ((query.trim() || composing) ? handleRunWithFiles : onAbort) : handleRunWithFiles}
            disabled={!isRunning && !query.trim() && !composing && clips.length === 0}
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
                {t("uploadFiles")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
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
                  {mode === "react" ? t("modeStandard") : t("modePlanner")}
                </button>
              </TooltipTrigger>
              <TooltipContent side="top">
                {mode === "react"
                  ? t("modeStandardTooltip")
                  : t("modePlannerTooltip")}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          {/* Agent selector — hidden in embedded/builder mode */}
          {!embedded && agents.length > 0 && (
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
                  {selectedAgent?.icon
                    ? <span className="text-sm leading-none">{selectedAgent.icon}</span>
                    : <Bot className="h-3 w-3" />
                  }
                  {selectedAgent ? selectedAgent.name : t("noAgent")}
                  <ChevronsUpDown className="h-3 w-3 opacity-50" />
                </button>
              </PopoverTrigger>
              <PopoverContent className="w-[200px] p-0" side="top" align="start">
                <Command>
                  <CommandInput placeholder={t("searchAgents")} />
                  <CommandList>
                    <CommandEmpty>{t("noAgentFound")}</CommandEmpty>
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
                        {t("noAgent")}
                      </CommandItem>
                      {agents.map((a) => (
                        <CommandItem
                          key={a.id}
                          value={a.id}
                          keywords={[a.name]}
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
    {activeConversation && (
      <ExportDialog
        conversationId={activeConversation.id}
        conversationTitle={activeConversation.title}
        open={exportOpen}
        onOpenChange={setExportOpen}
      />
    )}
    </>
  )
}
