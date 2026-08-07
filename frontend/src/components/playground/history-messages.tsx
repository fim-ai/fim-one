"use client"

import React from "react"
import { useTranslations } from "next-intl"
import { Bot, Clock, RefreshCw, BarChart3, CheckCircle2, Target } from "lucide-react"
import { MarkdownContent } from "@/lib/markdown"
import { cn, fmtDuration } from "@/lib/utils"
import { useAuth } from "@/contexts/auth-context"
import { UserAvatar } from "@/components/shared/user-avatar"
import { CollapsibleText } from "@/components/playground/collapsible-text"
import { ClipMessageContent } from "@/components/playground/clip-message-content"
import type { ClipMessageMetadata } from "@/components/playground/clip-message-content"
import { CopyButton } from "@/components/ui/copy-button"
import { FileMessageContent } from "@/components/playground/file-message-content"
import type { FileMessageMetadata } from "@/components/playground/file-message-content"
import type { MessageResponse } from "@/types/conversation"

interface HistoryMessagesProps {
  messages: MessageResponse[]
}

export function HistoryMessages({ messages }: HistoryMessagesProps) {
  const { user } = useAuth()
  if (messages.length === 0) return null

  return (
    <div className="space-y-5">
      {messages.map((msg) => (
        <div key={msg.id}>
          {msg.role === "user" ? (
            <UserMessage content={msg.content} metadata={msg.metadata} avatar={user?.avatar} userId={user?.id} displayName={user?.display_name || user?.email} />
          ) : (
            <AssistantMessage content={msg.content} metadata={msg.metadata} />
          )}
        </div>
      ))}
    </div>
  )
}

const UserMessage = React.memo(function UserMessage({ content, metadata, avatar, userId, displayName }: { content: string | null; metadata?: Record<string, unknown> | null; avatar?: string | null; userId?: string; displayName?: string | null }) {
  const fallback = (displayName || "U").charAt(0).toUpperCase()

  // Detect clip metadata
  const hasClipMeta = Array.isArray(metadata?.clips) && (metadata.clips as unknown[]).length > 0
  const clipMetadata: ClipMessageMetadata | null = hasClipMeta
    ? {
        clips: metadata!.clips as ClipMessageMetadata["clips"],
        userQuery: (metadata!.userQuery as string) ?? "",
      }
    : null

  // Detect file metadata
  const hasFileMeta = Array.isArray(metadata?.files) && (metadata.files as unknown[]).length > 0
  const fileMetadata: FileMessageMetadata | null = hasFileMeta
    ? {
        files: metadata!.files as FileMessageMetadata["files"],
        userQuery: (metadata!.userQuery as string) ?? "",
      }
    : null

  return (
    <div className={cn("flex gap-3", !clipMetadata && !fileMetadata && "items-center")}>
      <UserAvatar avatar={avatar} userId={userId} fallback={fallback} className="h-7 w-7 shrink-0" iconClassName="h-3.5 w-3.5" />
      <div className="flex-1">
        {clipMetadata ? (
          <ClipMessageContent metadata={clipMetadata} />
        ) : fileMetadata ? (
          <FileMessageContent metadata={fileMetadata} />
        ) : (
          <CollapsibleText content={content ?? ""} className="text-sm text-foreground whitespace-pre-wrap" />
        )}
      </div>
    </div>
  )
})

const AssistantMessage = React.memo(function AssistantMessage({
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

  // Every chip that can appear in the bar counts: when none of them do, the bar
  // is dropped and the copy affordance floats over the answer instead, so a
  // stat-less reply doesn't carry an empty row.
  const hasStats =
    achieved != null || confidence != null || elapsed != null || iterations != null || usage != null

  return (
    <div className="group/answer flex gap-3">
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
              <span className="flex items-center gap-1 tabular-nums">
                <Clock className="h-2.5 w-2.5" />
                {fmtDuration(elapsed)}
              </span>
            )}
            {usage && (
              <span className="flex items-center gap-1 tabular-nums">
                <BarChart3 className="h-2.5 w-2.5" />
                {t("tokenIn", { value: (usage.prompt_tokens / 1000).toFixed(1) })} · {t("tokenOut", { value: (usage.completion_tokens / 1000).toFixed(1) })}
              </span>
            )}
            {content && (
              <span className="opacity-0 transition-opacity focus-within:opacity-100 group-hover/answer:opacity-100">
                <CopyButton text={content} title={t("copyAnswer")} className="-mx-1.5 py-0.5" iconClassName="h-3 w-3" />
              </span>
            )}
          </div>
        )}

        {/* Answer content */}
        {content ? (
          <div className="relative text-sm">
            {!hasStats && (
              <span className="absolute -top-1 right-0 z-10 rounded-md border border-border/60 bg-background/90 p-0.5 opacity-0 shadow-sm backdrop-blur transition-opacity focus-within:opacity-100 group-hover/answer:opacity-100">
                <CopyButton text={content} title={t("copyAnswer")} className="py-0.5" iconClassName="h-3 w-3" />
              </span>
            )}
            <MarkdownContent content={content} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground italic">{t("noContent")}</p>
        )}
      </div>
    </div>
  )
})
