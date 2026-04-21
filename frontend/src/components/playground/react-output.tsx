"use client"

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { MarkdownContent } from "@/lib/markdown"
import { useMemo, useState } from "react"
import { useTranslations } from "next-intl"
import type { LucideIcon } from "lucide-react"
import { Loader2, Wrench, Brain, CheckCircle2, Clock, RefreshCw, BarChart3, ChevronDown, ChevronUp, ChevronRight, StopCircle } from "lucide-react"
import { useAuth } from "@/contexts/auth-context"
import { UserAvatar } from "@/components/shared/user-avatar"
import { fmtDuration } from "@/lib/utils"
import type { ReactStepEvent, ReactDoneEvent } from "@/types/api"
import type { StepItem } from "@/hooks/use-react-steps"
import { ReferencesSection } from "./references-section"
import { IterationCard, ArtifactChips } from "@/components/steps"
import type { IterationData } from "@/components/steps"
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "@/components/ui/collapsible"
import { CollapsibleText } from "@/components/playground/collapsible-text"
import { getToolDisplayName, getToolIcon } from "@/components/steps/step-summary"
import { useToolCatalog } from "@/hooks/use-tool-catalog"
import type { ToolMeta } from "@/hooks/use-tool-catalog"
import { SuggestedFollowups } from "./suggested-followups"
import { parseEvidence, parseSimpleEvidence, mergeEvidence, type ParsedEvidence } from "@/lib/evidence-utils"
import { EvidenceProvider } from "@/contexts/evidence-context"
import { ConfirmationCard } from "@/components/chat/confirmation-card"

/**
 * Wire shape emitted by the backend for `awaiting_confirmation` SSE events.
 * FROZEN — see Phase 1 Task #3. Keep in sync with `confirmation_sse.py::_request_to_event_payload`.
 */
interface AwaitingConfirmationEvent {
  type: "awaiting_confirmation"
  confirmation_id: string
  tool_name: string
  arguments: Record<string, unknown>
  mode?: "inline" | "channel"
  channel_label?: string
  approver_scope?: "initiator" | "agent_owner" | "org_members" | ""
  timeout_at: string
  agent_id: string
}

/* ------------------------------------------------------------------ */
/*  Iteration grouping helpers (ReAct mode)                            */
/* ------------------------------------------------------------------ */

/**
 * Extract the first sentence from reasoning text as an iteration title.
 * Strips markdown, takes first sentence (up to period/newline), truncates to 60 chars.
 */
function extractReasoningTitle(reasoning?: string): string | null {
  if (!reasoning) return null
  const plain = reasoning
    .replace(/^#+\s*/gm, "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/\*(.*?)\*/g, "$1")
    .replace(/`(.*?)`/g, "$1")
    .replace(/\[(.*?)\]\(.*?\)/g, "$1")
    .trim()
  if (!plain) return null
  // Split on sentence-ending punctuation or newline
  const first = plain.split(/[.。!！?\n]/)[0]?.trim()
  if (!first || first.length < 4) return null
  return first.length > 60 ? first.slice(0, 57) + "\u2026" : first
}

/** Generate a short title for a group from its tool calls */
function generateGroupToolTitle(
  group: ReactIterGroup,
  tools?: ToolMeta[],
): string | null {
  const toolSteps = group.stepItems
    .map(s => s.item.data as ReactStepEvent)
    .filter(s => s.type === "iteration" && s.tool_name)
  if (toolSteps.length === 0) return null

  const names = [...new Set(toolSteps.map(s =>
    getToolDisplayName(s.tool_name!, tools),
  ))]
  return names.join(", ")
}

interface ReactIterGroup {
  iteration: number
  stepItems: Array<{ item: StepItem; originalIdx: number }>
  totalDuration: number
  toolCounts: Array<{ name: string; displayName: string; Icon: LucideIcon; count: number }>
}

/** Group step items by displayIteration for collapsible rendering */
function groupStepsByIteration(
  stepItems: StepItem[],
  allItems: StepItem[],
  tools?: ToolMeta[],
): ReactIterGroup[] {
  const groups: ReactIterGroup[] = []

  for (const item of stepItems) {
    const step = item.data as ReactStepEvent
    const iterNum = item.displayIteration ?? (step.iteration ?? 0) + 1
    const originalIdx = allItems.indexOf(item)
    const last = groups[groups.length - 1]

    if (last && last.iteration === iterNum) {
      last.stepItems.push({ item, originalIdx })
      last.totalDuration += item.duration ?? 0
    } else {
      groups.push({
        iteration: iterNum,
        stepItems: [{ item, originalIdx }],
        totalDuration: item.duration ?? 0,
        toolCounts: [],
      })
    }
  }

  // Compute tool counts per group
  for (const group of groups) {
    const counts = new Map<string, { displayName: string; Icon: LucideIcon; count: number }>()
    for (const { item } of group.stepItems) {
      const step = item.data as ReactStepEvent
      if (step.type === "iteration" && step.tool_name) {
        const existing = counts.get(step.tool_name)
        if (existing) {
          existing.count++
        } else {
          counts.set(step.tool_name, {
            displayName: getToolDisplayName(step.tool_name, tools),
            Icon: getToolIcon(step.tool_name, tools),
            count: 1,
          })
        }
      }
    }
    group.toolCounts = Array.from(counts.entries()).map(([name, data]) => ({
      name,
      ...data,
    }))
  }

  return groups
}

/* ------------------------------------------------------------------ */
/*  ReactIterationTimeline — collapsible timeline for completed view   */
/* ------------------------------------------------------------------ */

function ReactIterationTimeline({ stepItems, allItems }: { stepItems: StepItem[]; allItems: StepItem[] }) {
  const { data: catalog } = useToolCatalog()
  const groups = useMemo(
    () => groupStepsByIteration(stepItems, allItems, catalog?.tools),
    [stepItems, allItems, catalog?.tools],
  )

  return (
    <div className="relative">
      {groups.map((group, idx) => (
        <div key={group.iteration} className="relative">
          {idx < groups.length - 1 && (
            <div className="absolute left-[10px] top-[22px] bottom-0 w-px bg-border/30" />
          )}
          <ReactIterationNode group={group} />
        </div>
      ))}
    </div>
  )
}

function ReactIterationNode({ group }: { group: ReactIterGroup }) {
  const t = useTranslations("playground")
  const { data: catalog } = useToolCatalog()
  const [expanded, setExpanded] = useState(false)

  // Title cascade: reasoning first sentence → tool summary → "Iteration N"
  const title = useMemo(() => {
    // 1. Try reasoning from the thinking step
    const thinkingStep = group.stepItems
      .map(s => s.item.data as ReactStepEvent)
      .find(s => s.type === "thinking")
    const reasoningTitle = extractReasoningTitle(thinkingStep?.reasoning)
    if (reasoningTitle) return reasoningTitle

    // 2. Try tool name summary
    const toolTitle = generateGroupToolTitle(group, catalog?.tools)
    if (toolTitle) return toolTitle

    // 3. Fallback
    return null
  }, [group, catalog?.tools])

  return (
    <div className="pl-8 pb-3 relative">
      {/* Timeline dot — matches DAG step style */}
      <div className="absolute left-0 top-0 z-10 flex h-[22px] w-[22px] items-center justify-center rounded-full bg-green-500/10 ring-2 ring-background">
        <CheckCircle2 className="h-3 w-3 text-green-500" />
      </div>

      {/* Clickable header */}
      <div
        className="rounded-md px-2 py-1 transition-colors cursor-pointer hover:bg-muted/30"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-medium text-foreground shrink-0">
            {t("iterationLabel", { n: group.iteration })}
          </span>
          {title && (
            <span className="text-xs text-muted-foreground truncate min-w-0">
              {title}
            </span>
          )}
          <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground shrink-0 tabular-nums">
            <Clock className="h-2.5 w-2.5" />
            {fmtDuration(group.totalDuration)}
          </span>
          {expanded
            ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          }
        </div>

        {/* Collapsed: tool summary badges */}
        {!expanded && group.toolCounts.length > 0 && (
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            {group.toolCounts.map(({ name, displayName, Icon, count }) => (
              <span
                key={name}
                className="inline-flex items-center gap-1 text-[10px] text-muted-foreground bg-muted/40 rounded px-1.5 py-0.5"
              >
                <Icon className="h-2.5 w-2.5" />
                <span>{displayName}</span>
                {count > 1 && <span className="font-medium">×{count}</span>}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Expanded: original step cards */}
      {expanded && (
        <div className="mt-1.5 space-y-2 pl-2">
          {group.stepItems.map(({ item, originalIdx }) => {
            const step = item.data as ReactStepEvent
            return (
              <div key={originalIdx} data-react-idx={originalIdx}>
                <StepCard step={step} duration={item.duration} displayIteration={item.displayIteration} />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */

interface ReactOutputProps {
  items: StepItem[]
  isStreaming?: boolean
  streamingAnswer?: string
  suggestions?: string[]
  onSuggestionSelect?: (query: string) => void
  isPostProcessing?: boolean
}

export function ReactOutput({ items, isStreaming, streamingAnswer, suggestions, onSuggestionSelect, isPostProcessing }: ReactOutputProps) {
  const t = useTranslations("playground")
  const { user } = useAuth()
  const userFallback = (user?.display_name || user?.email || "U").charAt(0).toUpperCase()
  const [stepsExpanded, setStepsExpanded] = useState(false)

  const hasDone = items.some((i) => i.event === "done")
  const stepItems = items.filter((i) => i.event === "step")
  const doneItem = items.find((i) => i.event === "done")

  const toolCallCount = stepItems.filter((i) => {
    const step = i.data as ReactStepEvent
    return step.type === "iteration"
  }).length

  const done = doneItem?.data as ReactDoneEvent | undefined
  const elapsed = done?.elapsed ?? 0

  // Determine which answer to show: done.answer is authoritative, fall back to streaming
  const displayAnswer = done?.answer ?? streamingAnswer ?? ""
  const isAnswerStreaming = !!streamingAnswer && !done

  // After completion with tool calls: show collapsible summary bar + done card
  if (hasDone && toolCallCount > 0) {
    return (
      <div className="space-y-3 min-w-0 w-full">
        {/* Collapsible step group — ALL steps (thinking + iterations) nested inside */}
        <div className="rounded-lg border border-border/40 bg-muted/20">
          <button
            type="button"
            onClick={() => setStepsExpanded((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-2.5 cursor-pointer hover:bg-muted/40 transition-colors text-xs text-muted-foreground rounded-lg"
          >
            <Wrench className="h-3.5 w-3.5 shrink-0" />
            <span className="tabular-nums">
              {toolCallCount !== 1 ? t("toolCallCountPlural", { count: toolCallCount }) : t("toolCallCount", { count: toolCallCount })}
              {" \u00b7 "}
              {fmtDuration(elapsed)}
            </span>
            {stepsExpanded ? (
              <ChevronUp className="h-3.5 w-3.5 ml-auto shrink-0" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 ml-auto shrink-0" />
            )}
          </button>

          {stepsExpanded && (
            <div className="px-4 pt-1 pb-3">
              <ReactIterationTimeline stepItems={stepItems} allItems={items} />
            </div>
          )}
        </div>

        {/* Inject events — always visible (they are user messages) */}
        {items.filter((i) => i.event === "inject").map((item) => {
          const originalIdx = items.indexOf(item)
          const injectData = item.data as { content: string }
          return (
            <div key={originalIdx} data-react-idx={originalIdx} className="flex gap-3">
              <UserAvatar avatar={user?.avatar} userId={user?.id} fallback={userFallback} className="h-7 w-7" iconClassName="h-3.5 w-3.5" />
              <div className="flex-1 pt-0.5">
                <CollapsibleText content={injectData.content} className="text-sm text-foreground whitespace-pre-wrap" />
              </div>
            </div>
          )
        })}

        {/* Awaiting-confirmation cards — persist after done so the operator's
             decision record stays visible in the transcript. */}
        {items.filter((i) => i.event === "awaiting_confirmation").map((item) => {
          const originalIdx = items.indexOf(item)
          const ev = item.data as AwaitingConfirmationEvent
          return (
            <div key={`conf-${ev.confirmation_id}`} data-react-idx={originalIdx}>
              <ConfirmationCard
                confirmationId={ev.confirmation_id}
                toolName={ev.tool_name}
                arguments={ev.arguments}
                timeoutAt={ev.timeout_at}
                agentId={ev.agent_id}
                mode={ev.mode}
                channelLabel={ev.channel_label}
                approverScope={ev.approver_scope}
              />
            </div>
          )
        })}

        {/* Answer card — shown during streaming or after done */}
        {(displayAnswer || isAnswerStreaming) && (
          <div data-react-idx={doneItem ? items.indexOf(doneItem) : undefined}>
            {done ? (
              <DoneCard done={done} items={items} suggestions={suggestions} onSuggestionSelect={onSuggestionSelect} isPostProcessing={isPostProcessing} />
            ) : (
              <StreamingAnswerCard content={displayAnswer} />
            )}
          </div>
        )}
      </div>
    )
  }

  // Show streaming answer card when answer step exists or streaming content arrives (before done)
  const hasAnswerStep = items.some(i => i.event === "step" && (i.data as ReactStepEvent).type === "answer")
  const showStreamingAnswer = !hasDone && (hasAnswerStep || (isAnswerStreaming && displayAnswer))
  const isAborted = !isStreaming && !hasDone

  // Streaming (no done yet) or direct answer (no steps): render as before
  return (
    <div className="space-y-3 min-w-0 w-full">
      {/* Initial loading indicator before any step events arrive */}
      {isStreaming && items.length === 0 && !showStreamingAnswer && (
        <div className="flex flex-col gap-3 px-1 py-2">
          <div className="flex items-center gap-3">
            <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
            <span className="text-sm text-shimmer">{t("statusProcessing")}</span>
          </div>
        </div>
      )}
      {items.map((item, idx) => {
        if (item.event === "step") {
          const step = item.data as ReactStepEvent
          return (
            <div key={idx} data-react-idx={idx}>
              <StepCard step={step} duration={item.duration} displayIteration={item.displayIteration} />
            </div>
          )
        }
        if (item.event === "inject") {
          const injectData = item.data as { content: string }
          return (
            <div key={idx} data-react-idx={idx} className="flex gap-3">
              <UserAvatar avatar={user?.avatar} userId={user?.id} fallback={userFallback} className="h-7 w-7" iconClassName="h-3.5 w-3.5" />
              <div className="flex-1 pt-0.5">
                <CollapsibleText content={injectData.content} className="text-sm text-foreground whitespace-pre-wrap" />
              </div>
            </div>
          )
        }
        if (item.event === "done") {
          const done = item.data as ReactDoneEvent
          return (
            <div key={idx} data-react-idx={idx}>
              <DoneCard done={done} items={items} suggestions={suggestions} onSuggestionSelect={onSuggestionSelect} isPostProcessing={isPostProcessing} />
            </div>
          )
        }
        if (item.event === "awaiting_confirmation") {
          const ev = item.data as AwaitingConfirmationEvent
          return (
            <div key={`conf-${ev.confirmation_id}`} data-react-idx={idx}>
              <ConfirmationCard
                confirmationId={ev.confirmation_id}
                toolName={ev.tool_name}
                arguments={ev.arguments}
                timeoutAt={ev.timeout_at}
                agentId={ev.agent_id}
                mode={ev.mode}
                channelLabel={ev.channel_label}
                approverScope={ev.approver_scope}
              />
            </div>
          )
        }
        return null
      })}
      {/* Streaming answer — shown before done arrives */}
      {showStreamingAnswer && (
        <StreamingAnswerCard content={displayAnswer} aborted={isAborted} />
      )}
    </div>
  )
}

function StreamingAnswerCard({ content, aborted }: { content: string; aborted?: boolean }) {
  const t = useTranslations("playground")
  return (
    <Card className="border-green-500/20 py-4">
      <CardHeader className={content ? "pb-0" : undefined}>
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10">
            {aborted ? (
              <StopCircle className="h-3.5 w-3.5 text-green-500" />
            ) : (
              <Loader2 className="h-3.5 w-3.5 text-green-500 animate-spin" />
            )}
          </div>
          <CardTitle className="text-sm">{aborted ? t("result") : t("answerGenerating")}</CardTitle>
        </div>
      </CardHeader>
      {content && (
        <CardContent>
          <MarkdownContent
            content={content}
            className={`prose-sm text-sm text-foreground/90${aborted ? "" : " streaming-cursor"}`}
          />
        </CardContent>
      )}
    </Card>
  )
}

function ThinkingCard({ iterLabel, duration, reasoning }: { iterLabel: number; duration?: number; reasoning?: string }) {
  const t = useTranslations("playground")
  const isWaiting = !reasoning && duration == null

  return (
    <Card className="border-border py-4">
      <CardContent className="space-y-2">
        <div className="flex items-center gap-3">
          <Badge
            variant="outline"
            className="border-muted-foreground/30 text-muted-foreground text-[10px] uppercase tracking-wider gap-1"
          >
            <Brain className="h-3 w-3" />
            {t("reasoning")}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {t("iterationLabel", { n: iterLabel })}
          </span>
          {duration != null && (
            <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground tabular-nums">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(duration)}
            </span>
          )}
        </div>
        {isWaiting && (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground leading-relaxed">
              <Loader2 className="inline h-3 w-3 animate-spin mr-1.5 align-text-bottom" />
              <span className="text-shimmer">{t("statusProcessing")}</span>
            </p>
          </div>
        )}
        {reasoning && (
          <div className="text-xs italic text-muted-foreground leading-relaxed">
            <MarkdownContent content={reasoning} className={`prose-xs text-xs text-muted-foreground${duration == null ? " streaming-cursor" : ""}`} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function StepCard({ step, duration, displayIteration }: { step: ReactStepEvent; duration?: number; displayIteration?: number }) {
  const iterLabel = displayIteration ?? (step.iteration ?? 0) + 1

  if (step.type === "thinking") {
    // Skip empty thinking rounds (final iteration that went straight to answer)
    if (!step.reasoning && step.status === "done") return null
    return <ThinkingCard iterLabel={iterLabel} duration={duration} reasoning={step.reasoning} />
  }

  // "answer" step is merged into StreamingAnswerCard — skip standalone rendering
  if (step.type === "answer") {
    return null
  }

  // iteration type — map to shared IterationData
  const iterData: IterationData = {
    type: step.type,
    displayIteration: iterLabel,
    tool_name: step.tool_name,
    tool_args: step.tool_args,
    reasoning: step.reasoning,
    observation: step.observation,
    error: step.error,
    duration,
    loading: step.status === "start",
    content_type: step.content_type,
    artifacts: step.artifacts,
  }

  return <IterationCard data={iterData} variant="card" defaultCollapsed={true} />
}

function DoneCard({ done, items, suggestions, onSuggestionSelect, isPostProcessing }: { done: ReactDoneEvent; items?: StepItem[]; suggestions?: string[]; onSuggestionSelect?: (query: string) => void; isPostProcessing?: boolean }) {
  const t = useTranslations("playground")
  const tDag = useTranslations("dag")

  // Effective iterations: count unique displayIterations remaining in the items
  // (empty thinking rounds have been filtered out by useReactSteps)
  const effectiveIterations = items
    ? new Set(
        items
          .filter(i => i.event === "step")
          .map(i => i.displayIteration)
          .filter(Boolean)
      ).size || done.iterations
    : done.iterations

  // Collect all artifacts from step events
  const allArtifacts = (items ?? [])
    .filter(i => i.event === "step")
    .flatMap(i => (i.data as ReactStepEvent).artifacts ?? [])

  // Compute deliverables and other artifacts
  const deliverables = done.deliverables ?? []
  const otherArtifacts = deliverables.length > 0
    ? allArtifacts.filter(a => !deliverables.some(d => d.url === a.url))
    : []

  // Pre-compute evidence for both EvidenceProvider (citations) and ReferencesSection
  const evidence = useMemo<ParsedEvidence | null>(() => {
    const blocks: ParsedEvidence[] = []
    for (const item of (items ?? [])) {
      if (item.event === "step") {
        const step = item.data as ReactStepEvent
        if (step.type === "iteration" && step.observation) {
          const parsed = parseEvidence(step.observation) ?? parseSimpleEvidence(step.observation)
          if (parsed) blocks.push(parsed)
        }
      }
    }
    return blocks.length > 0 ? mergeEvidence(blocks) : null
  }, [items])

  return (
    <Card className="py-4">
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10">
            <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
          </div>
          <CardTitle className="text-sm">{t("result")}</CardTitle>
          <div className="ml-auto flex items-center gap-3 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <RefreshCw className="h-2.5 w-2.5" />
              {effectiveIterations !== 1 ? t("iterationCountPlural", { count: effectiveIterations }) : t("iterationCount", { count: effectiveIterations })}
            </span>
            <span className="flex items-center gap-1 tabular-nums">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(done.elapsed)}
            </span>
            {done.usage && (
              <span className="flex items-center gap-1 tabular-nums">
                <BarChart3 className="h-2.5 w-2.5" />
                {t("tokenIn", { value: (done.usage.prompt_tokens / 1000).toFixed(1) })} · {t("tokenOut", { value: (done.usage.completion_tokens / 1000).toFixed(1) })}
              </span>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <EvidenceProvider sources={evidence?.sources ?? []}>
          <MarkdownContent
            content={done.answer}
            className="prose-sm text-sm text-foreground/90"
          />
        </EvidenceProvider>
        {deliverables.length > 0 && (
          <div className="mt-3 pt-3 border-t border-border/30">
            <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wider">
              {tDag("deliverables")}
            </p>
            <ArtifactChips artifacts={deliverables} />
          </div>
        )}
        {otherArtifacts.length > 0 && (
          <div className="mt-2 pt-2 border-t border-border/20">
            <Collapsible defaultOpen={false}>
              <CollapsibleTrigger className="flex items-center gap-1.5 cursor-pointer group">
                <ChevronRight className="h-3 w-3 text-muted-foreground/60 transition-transform duration-200 group-data-[state=open]:rotate-90" />
                <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-wider">
                  {tDag("generatedFilesCount", { count: otherArtifacts.length })}
                </p>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="opacity-60 mt-1.5">
                  <ArtifactChips artifacts={otherArtifacts} />
                </div>
              </CollapsibleContent>
            </Collapsible>
          </div>
        )}
        {deliverables.length === 0 && allArtifacts.length > 0 && (
          <div className="mt-3 pt-3 border-t border-border/30">
            <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wider">
              {tDag("generatedFiles")}
            </p>
            <ArtifactChips artifacts={allArtifacts} />
          </div>
        )}
        {items && <ReferencesSection items={items} evidence={evidence} />}
        {/* Use prop suggestions first, fall back to done.suggestions for stored conversations */}
        {(isPostProcessing || suggestions?.length || done.suggestions?.length) && onSuggestionSelect ? (
          <SuggestedFollowups
            suggestions={suggestions?.length ? suggestions : done.suggestions!}
            onSelect={onSuggestionSelect}
            isLoading={isPostProcessing}
          />
        ) : null}
      </CardContent>
    </Card>
  )
}
