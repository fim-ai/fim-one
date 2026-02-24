"use client"

import { useState, useCallback, useRef, useEffect } from "react"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Send, Loader2, Trash2 } from "lucide-react"
import { useSSE } from "@/hooks/use-sse"
import { API_BASE_URL } from "@/lib/constants"
import { ReactOutput } from "@/components/playground/react-output"
import { DagOutput } from "@/components/playground/dag-output"
import { Examples } from "@/components/playground/examples"
import type { AgentMode, Language } from "@/components/playground/examples"

export default function PlaygroundPage() {
  const [mode, setMode] = useState<AgentMode>("react")
  const [query, setQuery] = useState("")
  const [language, setLanguage] = useState<Language>("en")
  const { messages, isRunning, start, reset } = useSSE()

  const runWithQuery = useCallback((q: string) => {
    const trimmed = q.trim()
    if (!trimmed || isRunning) return

    const endpoint = mode === "react" ? "react" : "dag"
    const url = `${API_BASE_URL}/api/${endpoint}?q=${encodeURIComponent(trimmed)}&user_id=default`
    start(url)
  }, [isRunning, mode, start])

  const handleRun = useCallback(() => {
    runWithQuery(query)
  }, [query, runWithQuery])

  const handleReset = useCallback(() => {
    reset()
    setQuery("")
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
            reset()
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
            query={query}
            language={language}
            messages={messages}
            isRunning={isRunning}
            onQueryChange={setQuery}
            onLanguageChange={setLanguage}
            onRun={handleRun}
            onReset={handleReset}
            onKeyDown={handleKeyDown}
            onExampleSelect={handleExampleSelect}
          />
        </TabsContent>
        <TabsContent value="dag" className="flex-1 flex flex-col overflow-hidden m-0">
          <PlaygroundContent
            mode="dag"
            query={query}
            language={language}
            messages={messages}
            isRunning={isRunning}
            onQueryChange={setQuery}
            onLanguageChange={setLanguage}
            onRun={handleRun}
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
  query: string
  language: Language
  messages: ReturnType<typeof useSSE>["messages"]
  isRunning: boolean
  onQueryChange: (q: string) => void
  onLanguageChange: (lang: Language) => void
  onRun: () => void
  onReset: () => void
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void
  onExampleSelect: (example: string) => void
}

function PlaygroundContent({
  mode,
  query,
  language,
  messages,
  isRunning,
  onQueryChange,
  onLanguageChange,
  onRun,
  onReset,
  onKeyDown,
  onExampleSelect,
}: PlaygroundContentProps) {
  const hasMessages = messages.length > 0
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

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
            onClick={onRun}
            disabled={isRunning || !query.trim()}
            className="h-[72px] w-16 shrink-0"
          >
            {isRunning ? (
              <Loader2 className="h-4 w-4 animate-spin" />
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
      {(hasMessages || isRunning) && (
        <div className="flex flex-1 flex-col min-h-0 rounded-lg border border-border/50 bg-muted/10 overflow-hidden">
          {/* Output header */}
          {hasMessages && !isRunning && (
            <div className="flex items-center justify-end shrink-0 px-4 pt-3">
              <Button
                variant="ghost"
                size="sm"
                onClick={onReset}
                className="h-7 gap-1.5 text-xs text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="h-3 w-3" />
                Clear
              </Button>
            </div>
          )}
          <ScrollArea className="flex-1 min-h-0 p-4">
            <div className="min-w-0 max-w-full">
              {mode === "react" ? (
                <ReactOutput messages={messages} isRunning={isRunning} />
              ) : (
                <DagOutput messages={messages} isRunning={isRunning} />
              )}
              <div ref={bottomRef} />
            </div>
          </ScrollArea>
        </div>
      )}
    </div>
  )
}
