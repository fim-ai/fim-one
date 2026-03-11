"use client"

import { useState, useRef, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Sparkles, Send, Loader2, Wand2, ArrowLeft } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { toast } from "sonner"
import { agentApi, connectorApi, kbApi, ApiError } from "@/lib/api"
import { builderApi } from "@/lib/builder-api"
import { PlaygroundPage } from "@/components/playground/playground-page"
import { ConversationProvider } from "@/contexts/conversation-context"
import type { AgentResponse } from "@/types/agent"
import type { ConnectorResponse } from "@/types/connector"

// ── Types ──────────────────────────────────────────────────────────────────

export type AIPanelMode = "agent" | "connector-api" | "connector-db" | "kb"

export interface AIPanelProps {
  mode: AIPanelMode
  id: string | null
  formDirty?: boolean
  isNewMode?: boolean
  onBuilderModeChange?: (active: boolean) => void

  // Mode-specific callbacks
  onAgentUpdated?: (agent: AgentResponse) => void
  onActionsChanged?: () => void
  onConnectorUpdated?: (connector: ConnectorResponse) => void
  onSchemaChanged?: () => void
  onKbChanged?: () => void
  onEntityCreated?: (entity: unknown) => void
}

interface AIMessage {
  role: "user" | "assistant"
  content: string
}

// ── Component ──────────────────────────────────────────────────────────────

export function AIPanel({
  mode,
  id,
  formDirty = false,
  isNewMode = false,
  onBuilderModeChange,
  onAgentUpdated,
  onActionsChanged,
  onConnectorUpdated,
  onSchemaChanged,
  onKbChanged,
  onEntityCreated,
}: AIPanelProps) {
  const tAgents = useTranslations("agents")
  const tConnectors = useTranslations("connectors")
  const tKb = useTranslations("kb")

  // Pick the primary translation namespace by mode
  const t = mode === "agent" ? tAgents : mode === "kb" ? tKb : tConnectors

  const [messages, setMessages] = useState<AIMessage[]>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [builderMode, setBuilderMode] = useState(false)
  const [builderAgentId, setBuilderAgentId] = useState<string | null>(null)
  const [builderLoading, setBuilderLoading] = useState(false)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  // Focus input when id changes
  useEffect(() => {
    if (id) {
      inputRef.current?.focus()
    }
  }, [id])

  // Reset builder state when id changes
  useEffect(() => {
    setBuilderMode(false)
    setBuilderAgentId(null)
  }, [id])

  // Builder is only available for agent and connector-api modes
  const hasBuilder = mode === "agent" || mode === "connector-api"

  const openBuilder = async () => {
    if (!id || !hasBuilder) return
    if (builderAgentId) {
      setBuilderMode(true)
      window.dispatchEvent(new CustomEvent("builder-mode-change", { detail: { active: true } }))
      onBuilderModeChange?.(true)
      return
    }
    setBuilderLoading(true)
    try {
      const targetType = mode === "agent" ? "agent" : "connector"
      const res = await builderApi.createSession({ target_type: targetType, target_id: id })
      setBuilderAgentId(res.builder_agent_id)
      setBuilderMode(true)
      window.dispatchEvent(new CustomEvent("builder-mode-change", { detail: { active: true } }))
      onBuilderModeChange?.(true)
    } catch {
      const builderFailedKey = mode === "agent" ? "builderInitFailed" : "builderInitFailed"
      toast.error(t(builderFailedKey))
    } finally {
      setBuilderLoading(false)
    }
  }

  const handleSend = async () => {
    const trimmed = input.trim()
    if (!trimmed || isLoading) return

    const history = messages.slice(-10).map((m) => ({ role: m.role, content: m.content }))

    // ── Agent: new mode (create) ──────────────────────────────────────────
    if (mode === "agent" && isNewMode && !id) {
      const userMsg: AIMessage = { role: "user", content: trimmed }
      setMessages((prev) => [...prev, userMsg])
      setInput("")
      setIsLoading(true)
      try {
        const result = await agentApi.aiCreateAgent({ instruction: trimmed })
        const displayMessage = result.message_key
          ? tAgents(result.message_key, (result.message_args ?? {}) as Record<string, string | number>)
          : result.message || tAgents("agentCreated")
        setMessages((prev) => [...prev, { role: "assistant", content: displayMessage }])
        toast.success(tAgents("aiCreatedSuccess"))
        onEntityCreated?.(result.agent)
      } catch (err) {
        const errorMsg = err instanceof ApiError ? err.message : tAgents("aiError")
        setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${errorMsg}` }])
      } finally {
        setIsLoading(false)
      }
      return
    }

    // ── Connector API: new mode (create) ─────────────────────────────────
    if (mode === "connector-api" && isNewMode && !id) {
      const userMsg: AIMessage = { role: "user", content: trimmed }
      setMessages((prev) => [...prev, userMsg])
      setInput("")
      setIsLoading(true)
      try {
        const result = await connectorApi.aiCreateConnector({ instruction: trimmed })
        const displayMessage = result.message_key
          ? tConnectors(result.message_key, (result.message_args ?? {}) as Record<string, string | number>)
          : result.message || tConnectors("aiConnectorCreatedMessage")
        setMessages((prev) => [...prev, { role: "assistant", content: displayMessage }])
        toast.success(tConnectors("aiConnectorCreatedSuccess"))
        onEntityCreated?.(result.connector)
      } catch (err) {
        const errorMsg = err instanceof ApiError ? err.message : tConnectors("aiErrorFallback")
        setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${errorMsg}` }])
      } finally {
        setIsLoading(false)
      }
      return
    }

    if (!id) return

    // Block if form has unsaved changes
    if (formDirty) {
      if (mode === "agent") {
        toast.warning(tAgents("saveSettingsFirst"))
      } else {
        toast.warning(tConnectors("aiSaveSettingsFirst"))
      }
      return
    }

    const userMsg: AIMessage = { role: "user", content: trimmed }
    setMessages((prev) => [...prev, userMsg])
    setInput("")
    setIsLoading(true)

    try {
      // ── Agent: refine ──────────────────────────────────────────────────
      if (mode === "agent") {
        const result = await agentApi.aiRefineAgent(id, { instruction: trimmed, history })
        const translatedMessage = result.message_key
          ? tAgents(result.message_key, (result.message_args ?? {}) as Record<string, string | number>)
          : result.message
        const parts: string[] = []
        if (result.modified_fields && result.modified_fields.length > 0) {
          parts.push(tAgents("aiUpdatedFields", { fields: result.modified_fields.join(", ") }))
        }
        const summary = parts.length > 0
          ? `${translatedMessage} ${parts.join(". ")}.`
          : translatedMessage || tAgents("aiAgentUpdated")
        setMessages((prev) => [...prev, { role: "assistant", content: summary }])
        const hasChanges = result.modified_fields && result.modified_fields.length > 0
        if (hasChanges) {
          toast.success(tAgents("aiModifiedSuccess"))
          onAgentUpdated?.(result.agent)
        } else {
          toast.info(tAgents("ai_no_changes"))
        }
        return
      }

      // ── Connector DB: db-chat ──────────────────────────────────────────
      if (mode === "connector-db") {
        const result = await connectorApi.aiDbChat(id, trimmed, history)
        setMessages((prev) => [...prev, { role: "assistant", content: result.message }])
        if (result.ok && result.changes > 0) {
          toast.success(result.message)
          if (result.connector) {
            onConnectorUpdated?.(result.connector)
          } else {
            onSchemaChanged?.()
          }
        }
        return
      }

      // ── Connector API: refine ──────────────────────────────────────────
      if (mode === "connector-api") {
        const result = await connectorApi.aiRefineAction(id, { instruction: trimmed, history })
        const parts: string[] = []
        if (result.created.length > 0) {
          parts.push(tConnectors("aiCreatedActions", { count: result.created.length, names: result.created.map((a) => a.name).join(", ") }))
        }
        if (result.updated.length > 0) {
          parts.push(tConnectors("aiUpdatedActions", { count: result.updated.length, names: result.updated.map((a) => a.name).join(", ") }))
        }
        if (result.deleted.length > 0) {
          parts.push(tConnectors("aiDeletedActions", { count: result.deleted.length }))
        }
        if (result.connector_updated) {
          parts.push(tConnectors("aiConnectorSettingsUpdated"))
          onConnectorUpdated?.(result.connector_updated)
        }
        const translatedFallback = result.message_key
          ? tConnectors(result.message_key, (result.message_args ?? {}) as Record<string, string | number>)
          : result.message
        const summary = parts.length > 0 ? parts.join(". ") + "." : translatedFallback
        setMessages((prev) => [...prev, { role: "assistant", content: summary }])
        if (parts.length > 0) {
          toast.success(tConnectors("aiConnectorModified"))
        }
        onActionsChanged?.()
        return
      }

      // ── KB: ai chat ────────────────────────────────────────────────────
      if (mode === "kb") {
        const result = await kbApi.aiChat(id, trimmed, history)
        setMessages((prev) => [...prev, { role: "assistant", content: result.message }])
        if (result.ok) {
          onKbChanged?.()
        }
        return
      }
    } catch (err) {
      const errorMsg = err instanceof ApiError
        ? err.message
        : mode === "agent"
          ? tAgents("aiError")
          : mode === "kb"
            ? tKb("aiError")
            : tConnectors("aiErrorFallback")
      setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${errorMsg}` }])
    } finally {
      setIsLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const isDisabled = !isNewMode && id === null

  // ── Placeholder text by mode ───────────────────────────────────────────
  const getPlaceholder = () => {
    if (mode === "kb") return tKb("aiPlaceholder")
    if (mode === "agent") {
      return isNewMode && !id ? tAgents("aiCreatePlaceholder") : tAgents("aiRefinePlaceholder")
    }
    // connector modes
    if (isNewMode && !id) return tConnectors("aiPlaceholderNewMode")
    if (mode === "connector-db") return tConnectors("aiPlaceholderDbMode")
    return tConnectors("aiPlaceholderExistingMode")
  }

  // ── Empty state text by mode ───────────────────────────────────────────
  const getEmptyStateTitle = () => {
    if (mode === "kb") return tKb("aiEmptyTitle")
    if (mode === "agent") {
      return isNewMode && !id ? tAgents("describeCreate") : tAgents("describeModify")
    }
    if (mode === "connector-db") return tConnectors("aiEmptyDbMode")
    return isNewMode && !id ? tConnectors("aiEmptyNewMode") : tConnectors("aiEmptyExistingMode")
  }

  const getEmptyStateSubtitle = () => {
    if (mode === "kb") return tKb("aiEmptySubtitle")
    if (mode === "agent") {
      return isNewMode && !id ? tAgents("aiWillConfigure") : tAgents("aiWillApply")
    }
    if (mode === "connector-db") return tConnectors("aiSubtitleDbMode")
    return isNewMode && !id ? tConnectors("aiSubtitleNewMode") : tConnectors("aiSubtitleExistingMode")
  }

  const getDisabledText = () => {
    if (mode === "agent") return tAgents("saveFirst")
    if (mode === "kb") return tKb("aiPlaceholder")
    return tConnectors("saveConnectorFirst")
  }

  const getHeaderTitle = () => {
    if (mode === "kb") return tKb("aiAssistant")
    return t("aiAssistant")
  }

  const getThinkingText = () => {
    if (mode === "kb") return tKb("aiThinking")
    if (mode === "agent") return tAgents("thinking")
    return tConnectors("aiThinking")
  }

  // ── Builder mode ───────────────────────────────────────────────────────
  if (builderMode && builderAgentId) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border shrink-0">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => {
                  setBuilderMode(false)
                  window.dispatchEvent(new CustomEvent("builder-mode-change", { detail: { active: false } }))
                  onBuilderModeChange?.(false)
                }}
              >
                <ArrowLeft className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" sideOffset={5}>{t("backToAssistant")}</TooltipContent>
          </Tooltip>
          <Wand2 className="h-3.5 w-3.5 text-primary" />
          <span className="text-sm font-medium flex-1">{t("advancedBuilder")}</span>
        </div>
        <div className="flex-1 min-h-0 overflow-hidden">
          <ConversationProvider>
            <PlaygroundPage
              embedded
              initialAgentId={builderAgentId}
              isNewChat
              onClose={() => setBuilderMode(false)}
              onTurnComplete={() => {
                if (mode === "agent" && id) {
                  agentApi.get(id).then((agent) => onAgentUpdated?.(agent)).catch(() => {})
                } else {
                  onActionsChanged?.()
                }
              }}
            />
          </ConversationProvider>
        </div>
      </div>
    )
  }

  // ── Default AI assistant mode ──────────────────────────────────────────
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border shrink-0">
        <Sparkles className="h-3.5 w-3.5 text-amber-500" />
        <span className="text-sm font-medium flex-1">{getHeaderTitle()}</span>
        {id && hasBuilder && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                onClick={openBuilder}
                disabled={builderLoading}
                className="h-7 text-xs gap-1.5"
              >
                {builderLoading
                  ? <Loader2 className="h-3 w-3 animate-spin" />
                  : <Wand2 className="h-3 w-3" />}
                {t("advancedBuilder")}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" sideOffset={5}>{t("advancedBuilderDesc")}</TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {isDisabled ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-muted-foreground text-center">
              {getDisabledText()}
            </p>
          </div>
        ) : (
          <>
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-amber-500/10">
                  <Sparkles className="h-5 w-5 text-amber-500/60" />
                </div>
                <div className="space-y-1">
                  <p className="text-sm text-muted-foreground">
                    {getEmptyStateTitle()}
                  </p>
                  <p className="text-xs text-muted-foreground/50">
                    {getEmptyStateSubtitle()}
                  </p>
                </div>
              </div>
            )}
            {messages.map((msg, i) => {
              const forgotten = i < messages.length - 10
              return (
                <div
                  key={i}
                  className={`transition-opacity ${forgotten ? "opacity-30" : "opacity-100"} ${
                    msg.role === "user" ? "flex justify-end" : "flex justify-start"
                  }`}
                >
                  <div
                    className={
                      msg.role === "user"
                        ? "bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm max-w-[85%]"
                        : "bg-muted rounded-lg px-3 py-1.5 text-sm max-w-[85%]"
                    }
                  >
                    {msg.role === "assistant" ? (
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          p: ({ children }) => <p className="mb-1 last:mb-0">{children}</p>,
                          ul: ({ children }) => <ul className="list-disc pl-4 mb-1 space-y-0.5">{children}</ul>,
                          ol: ({ children }) => <ol className="list-decimal pl-4 mb-1 space-y-0.5">{children}</ol>,
                          li: ({ children }) => <li>{children}</li>,
                          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                          code: ({ children }) => (
                            <code className="bg-background/30 rounded px-1 py-0.5 text-xs font-mono">
                              {children}
                            </code>
                          ),
                        }}
                      >
                        {msg.content}
                      </ReactMarkdown>
                    ) : (
                      msg.content
                    )}
                  </div>
                </div>
              )
            })}
            {isLoading && (
              <div className="flex justify-start">
                <div className="bg-muted rounded-lg px-3 py-1.5 text-sm flex items-center gap-1.5 text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {getThinkingText()}
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input bar */}
      <div className="flex items-center gap-2 shrink-0 px-4 py-3 border-t border-border">
        <Input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={getPlaceholder()}
          disabled={isLoading || isDisabled}
          className="flex-1 h-8 text-sm"
        />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              size="icon-xs"
              onClick={handleSend}
              disabled={isLoading || isDisabled || !input.trim()}
            >
              <Send className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={5}>
            {mode === "kb" ? tKb("send") : mode === "agent" ? tAgents("send") : tConnectors("send")}
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  )
}
