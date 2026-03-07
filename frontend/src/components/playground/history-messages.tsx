"use client"

import { useTranslations } from "next-intl"
import { User, Bot, Clock, RefreshCw, BarChart3, CheckCircle2, Target } from "lucide-react"
import { MarkdownContent } from "@/lib/markdown"
import { fmtDuration } from "@/lib/utils"
import type { MessageResponse } from "@/types/conversation"

interface HistoryMessagesProps {
  messages: MessageResponse[]
}

export function HistoryMessages({ messages }: HistoryMessagesProps) {
  if (messages.length === 0) return null

  return (
    <div className="space-y-5">
      {messages.map((msg) => (
        <div key={msg.id}>
          {msg.role === "user" ? (
            <UserMessage content={msg.content} />
          ) : (
            <AssistantMessage content={msg.content} metadata={msg.metadata} />
          )}
        </div>
      ))}
    </div>
  )
}

function UserMessage({ content }: { content: string | null }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
        <User className="h-3.5 w-3.5 text-primary" />
      </div>
      <div className="flex-1">
        <p className="text-sm text-foreground">{content}</p>
      </div>
    </div>
  )
}

function AssistantMessage({
  content,
  metadata,
}: {
  content: string | null
  metadata: Record<string, unknown> | null
}) {
  const t = useTranslations("playground")
  const elapsed = metadata?.elapsed as number | undefined
  const iterations = metadata?.iterations as number | undefined
  const usage = metadata?.usage as { prompt_tokens: number; completion_tokens: number; total_tokens: number } | undefined
  const achieved = metadata?.achieved as boolean | undefined
  const confidence = metadata?.confidence as number | undefined

  const hasStats = elapsed != null || iterations != null || usage != null

  return (
    <div className="flex gap-3">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-500/10">
        <Bot className="h-3.5 w-3.5 text-emerald-500" />
      </div>
      <div className="flex-1 min-w-0 pt-0.5 space-y-2">
        {/* Stats bar */}
        {hasStats && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
            {achieved != null && (
              <span className="flex items-center gap-1">
                <CheckCircle2 className={`h-2.5 w-2.5 ${achieved ? "text-emerald-500" : "text-amber-500"}`} />
                {achieved ? t("achieved") : t("partial")}
              </span>
            )}
            {confidence != null && (
              <span className="flex items-center gap-1">
                <Target className="h-2.5 w-2.5" />
                {(confidence * 100).toFixed(0)}%
              </span>
            )}
            {iterations != null && (
              <span className="flex items-center gap-1">
                <RefreshCw className="h-2.5 w-2.5" />
                {iterations !== 1 ? t("iterationCountPlural", { count: iterations }) : t("iterationCount", { count: iterations })}
              </span>
            )}
            {elapsed != null && (
              <span className="flex items-center gap-1">
                <Clock className="h-2.5 w-2.5" />
                {fmtDuration(elapsed)}
              </span>
            )}
            {usage && (
              <span className="flex items-center gap-1">
                <BarChart3 className="h-2.5 w-2.5" />
                {t("tokenIn", { value: (usage.prompt_tokens / 1000).toFixed(1) })} · {t("tokenOut", { value: (usage.completion_tokens / 1000).toFixed(1) })}
              </span>
            )}
          </div>
        )}

        {/* Answer content */}
        {content ? (
          <div className="text-sm">
            <MarkdownContent content={content} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground italic">{t("noContent")}</p>
        )}
      </div>
    </div>
  )
}
