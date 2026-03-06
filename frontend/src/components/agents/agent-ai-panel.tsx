"use client"

import { useState, useRef, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Sparkles, Send, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { toast } from "sonner"
import { agentApi, ApiError } from "@/lib/api"
import type { AgentResponse } from "@/types/agent"

interface AIMessage {
  role: "user" | "assistant"
  content: string
}

interface AgentAIPanelProps {
  agentId: string | null
  onAgentUpdated: (agent: AgentResponse) => void
  formDirty?: boolean
  isNewMode?: boolean
  onAgentCreated?: (agent: AgentResponse) => void
}

export function AgentAIPanel({
  agentId,
  onAgentUpdated,
  formDirty = false,
  isNewMode = false,
  onAgentCreated,
}: AgentAIPanelProps) {
  const t = useTranslations("agents")
  const [messages, setMessages] = useState<AIMessage[]>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  useEffect(() => {
    if (agentId) {
      inputRef.current?.focus()
    }
  }, [agentId])

  const handleSend = async () => {
    const trimmed = input.trim()
    if (!trimmed || isLoading) return

    // In new mode (no agentId), use AI create endpoint
    if (isNewMode && !agentId) {
      const userMessage: AIMessage = { role: "user", content: trimmed }
      setMessages((prev) => [...prev, userMessage])
      setInput("")
      setIsLoading(true)

      try {
        const result = await agentApi.aiCreateAgent({
          instruction: trimmed,
        })
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: result.message || t("agentCreated") },
        ])
        toast.success(t("aiCreatedSuccess"))
        onAgentCreated?.(result.agent)
      } catch (err) {
        const errorMsg =
          err instanceof ApiError
            ? err.message
            : t("aiError")
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `Error: ${errorMsg}` },
        ])
      } finally {
        setIsLoading(false)
      }
      return
    }

    if (!agentId) return

    // Block if agent form has unsaved changes
    if (formDirty) {
      toast.warning(t("saveSettingsFirst"))
      return
    }

    const userMessage: AIMessage = { role: "user", content: trimmed }
    setMessages((prev) => [...prev, userMessage])
    setInput("")
    setIsLoading(true)

    try {
      const result = await agentApi.aiRefineAgent(agentId, {
        instruction: trimmed,
      })

      const parts: string[] = []
      if (result.modified_fields && result.modified_fields.length > 0) {
        parts.push(t("aiUpdatedFields", { fields: result.modified_fields.join(", ") }))
      }

      const summary = parts.length > 0
        ? `${result.message} ${parts.join(". ")}.`
        : result.message || t("aiAgentUpdated")

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: summary },
      ])
      toast.success(t("aiModifiedSuccess"))
      onAgentUpdated(result.agent)
    } catch (err) {
      const errorMsg =
        err instanceof ApiError
          ? err.message
          : t("aiError")
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${errorMsg}` },
      ])
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

  const isDisabled = !isNewMode && agentId === null

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border shrink-0">
        <Sparkles className="h-3.5 w-3.5 text-amber-500" />
        <span className="text-sm font-medium">{t("aiAssistant")}</span>
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {isDisabled ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-muted-foreground text-center">
              {t("saveFirst")}
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
                    {isNewMode && !agentId
                      ? t("describeCreate")
                      : t("describeModify")}
                  </p>
                  <p className="text-xs text-muted-foreground/50">
                    {isNewMode && !agentId
                      ? t("aiWillConfigure")
                      : t("aiWillApply")}
                  </p>
                </div>
              </div>
            )}
            {messages.map((msg, i) => (
              <div
                key={i}
                className={
                  msg.role === "user"
                    ? "flex justify-end"
                    : "flex justify-start"
                }
              >
                <div
                  className={
                    msg.role === "user"
                      ? "bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm max-w-[85%]"
                      : "bg-muted rounded-lg px-3 py-1.5 text-sm max-w-[85%]"
                  }
                >
                  {msg.content}
                </div>
              </div>
            ))}
            {isLoading && (
              <div className="flex justify-start">
                <div className="bg-muted rounded-lg px-3 py-1.5 text-sm flex items-center gap-1.5 text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {t("thinking")}
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
          placeholder={isNewMode && !agentId
            ? t("aiCreatePlaceholder")
            : t("aiRefinePlaceholder")}
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
          <TooltipContent side="bottom" sideOffset={5}>{t("send")}</TooltipContent>
        </Tooltip>
      </div>
    </div>
  )
}
