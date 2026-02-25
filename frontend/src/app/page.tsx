"use client"

import { useState, useCallback, useRef, useEffect } from "react"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Send, Loader2, Trash2, PanelRightOpen, PanelRightClose, ArrowDown, Square } from "lucide-react"
import { useSSE } from "@/hooks/use-sse"
import { useDagSteps } from "@/hooks/use-dag-steps"
import { useReactSteps } from "@/hooks/use-react-steps"
import { useMediaQuery } from "@/hooks/use-media-query"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { API_BASE_URL } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { ReactOutput } from "@/components/playground/react-output"
import { DagOutput } from "@/components/playground/dag-output"
import { Examples } from "@/components/playground/examples"
import { RightSidebar } from "@/components/playground/right-sidebar"
import { ReactSidebarTimeline } from "@/components/playground/react-sidebar-timeline"
import { DagFlowGraph } from "@/components/dag/dag-flow-graph"
import type { AgentMode, Language } from "@/components/playground/examples"

export default function PlaygroundPage() {
  const [mode, setMode] = useState<AgentMode>("react")
  const [query, setQuery] = useState("")
  const [language, setLanguage] = useState<Language>("en")
  const [sourceMode, setSourceMode] = useState<AgentMode | null>(null)
  const { messages, isRunning, start, reset, abort } = useSSE()

  const runWithQuery = useCallback((q: string) => {
    const trimmed = q.trim()
    if (!trimmed || isRunning) return

    const endpoint = mode === "react" ? "react" : "dag"
    const url = `${API_BASE_URL}/api/${endpoint}?q=${encodeURIComponent(trimmed)}&user_id=default`
    setSourceMode(mode)
    start(url)
  }, [isRunning, mode, start])

  const handleRun = useCallback(() => {
    runWithQuery(query)
  }, [query, runWithQuery])

  const handleReset = useCallback(() => {
    reset()
    setQuery("")
    setSourceMode(null)
  }, [reset])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault()
        handleRun()
      }
    },
    [handleRun]
  )

  const handleExampleSelect = useCallback(
    (example: string) => {
      setQuery(example)
      runWithQuery(example)
    },
    [runWithQuery]
  )

  return (
    <div className="flex h-full flex-col">
      <Tabs
        value={mode}
        onValueChange={(v) => {
          if (!isRunning) {
            setMode(v as AgentMode)
          }
        }}
        className="flex h-full flex-col"
      >
        {/* Tab bar */}
        <div className="shrink-0 border-b border-border px-6 pt-3 pb-0">
          <TabsList variant="line">
            <TabsTrigger value="react" disabled={isRunning}>
              ReAct Agent
            </TabsTrigger>
            <TabsTrigger value="dag" disabled={isRunning}>
              DAG Planner
            </TabsTrigger>
          </TabsList>
        </div>

        {/* Content area - both tabs share the same layout */}
        <TabsContent value="react" className="flex-1 flex flex-col overflow-hidden m-0">
          <PlaygroundContent
            mode="react"
            sourceMode={sourceMode}
            query={query}
            language={language}
            messages={messages}
            isRunning={isRunning}
            onQueryChange={setQuery}
            onLanguageChange={setLanguage}
            onRun={handleRun}
            onAbort={abort}
            onReset={handleReset}
            onKeyDown={handleKeyDown}
            onExampleSelect={handleExampleSelect}
          />
        </TabsContent>
        <TabsContent value="dag" className="flex-1 flex flex-col overflow-hidden m-0">
          <PlaygroundContent
            mode="dag"
            sourceMode={sourceMode}
            query={query}
            language={language}
            messages={messages}
            isRunning={isRunning}
            onQueryChange={setQuery}
            onLanguageChange={setLanguage}
            onRun={handleRun}
            onAbort={abort}
            onReset={handleReset}
            onKeyDown={handleKeyDown}
            onExampleSelect={handleExampleSelect}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}

interface PlaygroundContentProps {
  mode: AgentMode
  sourceMode: AgentMode | null
  query: string
  language: Language
  messages: ReturnType<typeof useSSE>["messages"]
  isRunning: boolean
  onQueryChange: (q: string) => void
  onLanguageChange: (lang: Language) => void
  onRun: () => void
  onAbort: () => void
  onReset: () => void
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void
  onExampleSelect: (example: string) => void
}

function PlaygroundContent({
  mode,
  sourceMode,
  query,
  language,
  messages,
  isRunning,
  onQueryChange,
  onLanguageChange,
  onRun,
  onAbort,
  onReset,
  onKeyDown,
  onExampleSelect,
}: PlaygroundContentProps) {
  const modeMatches = sourceMode === mode
  const hasMessages = modeMatches && messages.length > 0
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const isNearBottomRef = useRef(true)
  const [showScrollBtn, setShowScrollBtn] = useState(false)

  // Sidebar state — persisted to localStorage
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

  // Priority: active drag > custom drag (persisted) > expand preset > normal preset
  const NORMAL_RATIO = 0.3
  const EXPANDED_RATIO = 0.7
  const currentRatio = dragRatio ?? customRatio ?? (sidebarExpanded ? EXPANDED_RATIO : NORMAL_RATIO)

  // Parse data at this level via hooks
  const dagData = useDagSteps(messages, isRunning)
  const reactItems = useReactSteps(messages, isRunning)

  const showSidebar = hasMessages && sidebarOpen && isWideScreen

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

  // Track scroll position — only auto-scroll when user is near bottom
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

  // Scroll the ScrollArea viewport to bottom (avoids scrollIntoView cascading to parent containers)
  const scrollViewportToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const root = scrollAreaRef.current
    if (!root) return
    const viewport = root.querySelector<HTMLElement>("[data-radix-scroll-area-viewport]")
    if (!viewport) return
    viewport.scrollTo({ top: viewport.scrollHeight, behavior })
  }, [])

  // Auto-scroll only when user is already near bottom.
  // Skip the initial mount to avoid scrolling on tab switch.
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

  const scrollToBottom = useCallback(() => {
    scrollViewportToBottom()
    setShowScrollBtn(false)
  }, [scrollViewportToBottom])

  // Scroll within the Radix ScrollArea viewport only (prevents parent container jumping)
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

  const scrollToReactItem = useCallback((idx: number) => {
    scrollInViewport(`[data-react-idx="${idx}"]`)
  }, [scrollInViewport])

  const statusText = (() => {
    if (!isRunning || !modeMatches) return null
    if (mode === "dag") {
      if (dagData.doneEvent) return null
      if (dagData.currentPhase === "planning") return "Planning..."
      if (dagData.currentPhase === "executing") return "Executing steps..."
      if (dagData.currentPhase === "analyzing") return "Analyzing..."
      return "Processing..."
    } else {
      if (reactItems.some(i => i.event === "done")) return null
      return "Processing..."
    }
  })()

  return (
    <div className="flex flex-1 flex-col overflow-hidden p-6 gap-4">
      {/* Input area */}
      <div className="shrink-0 space-y-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={
              mode === "react"
                ? "Ask the ReAct agent to solve a problem..."
                : "Describe a multi-step task for the DAG planner..."
            }
            disabled={isRunning}
            className="min-h-[72px] max-h-[160px] resize-none"
          />
          <Button
            onClick={isRunning ? onAbort : onRun}
            disabled={!isRunning && !query.trim()}
            className="h-[72px] w-16 shrink-0"
            variant={isRunning ? "destructive" : "default"}
          >
            {isRunning ? (
              <Square className="h-4 w-4" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
        {/* Examples */}
        {!hasMessages && (
          <Examples
            mode={mode}
            language={language}
            onLanguageChange={onLanguageChange}
            onSelect={onExampleSelect}
            disabled={isRunning}
          />
        )}
      </div>

      {/* Output area */}
      {(hasMessages || (isRunning && modeMatches)) && (
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
            {(hasMessages || (isRunning && modeMatches)) && (
              <div className="flex items-center shrink-0 px-4 py-3 border-b border-border/30 gap-1">
                <span className="text-sm font-medium">Execution Log</span>
                {statusText && (
                  <span className="flex items-center gap-1.5 ml-3 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    <span className="shiny-text">{statusText}</span>
                  </span>
                )}
                <div className="flex-1" />
                {hasMessages && !isRunning && (
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={onReset}
                    className="h-7 w-7 text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
                {isWideScreen && (
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
            )}

            <div className="relative flex-1 min-h-0">
              <ScrollArea ref={scrollAreaRef} className="h-full p-4">
                <div className="min-w-0 max-w-full">
                  {mode === "react" ? (
                    <ReactOutput items={reactItems} />
                  ) : (
                    <DagOutput
                      planSteps={dagData.planSteps}
                      stepStates={dagData.stepStates}
                      analysisPhase={dagData.analysisPhase}
                      doneEvent={dagData.doneEvent}
                      currentPhase={dagData.currentPhase}
                      hideDagGraph={showSidebar}
                    />
                  )}
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
              title={mode === "dag" ? "Execution Plan" : "Steps Timeline"}
              badge={mode === "dag" ? dagData.planSteps?.length : reactItems.filter(i => i.event === "step" || i.event === "done").length}
              expanded={sidebarExpanded}
              onToggleExpand={() => { setSidebarExpanded(!sidebarExpanded); setCustomRatio(null) }}
              className={cn(!isDragging && "transition-all duration-300")}
              style={{ flex: `${currentRatio} 1 0%`, minWidth: 0 }}
            >
              {mode === "dag" ? (
                dagData.planSteps && dagData.planSteps.length > 0 ? (
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
                )
              ) : (
                <ReactSidebarTimeline items={reactItems} isRunning={isRunning} onItemClick={scrollToReactItem} />
              )}
            </RightSidebar>
          )}
        </div>
      )}
    </div>
  )
}
