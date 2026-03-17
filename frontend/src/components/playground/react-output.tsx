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
import { Loader2, Wrench, Brain, CheckCircle2, Clock, RefreshCw, BarChart3, ChevronDown, ChevronUp, ChevronRight } from "lucide-react"
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
import { SuggestedFollowups } from "./suggested-followups"
import { parseEvidence, parseSimpleEvidence, mergeEvidence, type ParsedEvidence } from "@/lib/evidence-utils"
import { EvidenceProvider } from "@/contexts/evidence-context"

interface ReactOutputProps {
  items: StepItem[]
  isStreaming?: boolean
  streamingAnswer?: string
  suggestions?: string[]
  onSuggestionSelect?: (query: string) => void
}

export function ReactOutput({ items, isStreaming, streamingAnswer, suggestions, onSuggestionSelect }: ReactOutputProps) {
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
            <div className="space-y-3 px-4 pt-1 pb-3">
              {stepItems.map((item) => {
                const originalIdx = items.indexOf(item)
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

        {/* Answer card — shown during streaming or after done */}
        {(displayAnswer || isAnswerStreaming) && (
          <div data-react-idx={doneItem ? items.indexOf(doneItem) : undefined}>
            {done ? (
              <DoneCard done={done} items={items} suggestions={suggestions} onSuggestionSelect={onSuggestionSelect} />
            ) : (
              <StreamingAnswerCard content={displayAnswer} />
            )}
          </div>
        )}
      </div>
    )
  }

  // Show streaming answer before done arrives (no tool calls, or still streaming)
  const showStreamingAnswer = isAnswerStreaming && !hasDone && displayAnswer

  // Streaming (no done yet) or direct answer (no steps): render as before
  return (
    <div className="space-y-3 min-w-0 w-full">
      {/* Initial loading indicator before any step events arrive */}
      {isStreaming && items.length === 0 && !showStreamingAnswer && (
        <div className="flex items-center gap-3 px-1 py-2">
          <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
          <span className="text-sm text-muted-foreground shiny-text">{t("statusProcessing")}</span>
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
              <DoneCard done={done} items={items} suggestions={suggestions} onSuggestionSelect={onSuggestionSelect} />
            </div>
          )
        }
        return null
      })}
      {/* Streaming answer — shown before done arrives */}
      {showStreamingAnswer && (
        <StreamingAnswerCard content={displayAnswer} />
      )}
    </div>
  )
}

function StreamingAnswerCard({ content }: { content: string }) {
  const t = useTranslations("playground")
  return (
    <Card className="py-4">
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10">
            <Loader2 className="h-3.5 w-3.5 text-green-500 animate-spin" />
          </div>
          <CardTitle className="text-sm">{t("result")}</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        <MarkdownContent
          content={content}
          className="prose-sm text-sm text-foreground/90 streaming-cursor"
        />
      </CardContent>
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
          <p className="text-xs text-muted-foreground leading-relaxed">
            <Loader2 className="inline h-3 w-3 animate-spin mr-1.5 align-text-bottom" />
            <span className="shiny-text">{t("statusProcessing")}</span>
          </p>
        )}
        {reasoning && (
          <div className="text-xs italic text-muted-foreground leading-relaxed">
            <MarkdownContent content={reasoning} className="prose-xs text-xs text-muted-foreground" />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function StepCard({ step, duration, displayIteration }: { step: ReactStepEvent; duration?: number; displayIteration?: number }) {
  const t = useTranslations("playground")
  const iterLabel = displayIteration ?? (step.iteration ?? 0) + 1

  if (step.type === "thinking") {
    // Skip empty thinking rounds (final iteration that went straight to answer)
    if (!step.reasoning && step.status === "done") return null
    return <ThinkingCard iterLabel={iterLabel} duration={duration} reasoning={step.reasoning} />
  }

  if (step.type === "answer") {
    return (
      <Card className="border-green-500/20 py-4">
        <CardContent className="flex items-center gap-3">
          <Loader2 className="h-4 w-4 animate-spin text-green-500" />
          <span className="text-sm shiny-text">{t("answerGenerating")}</span>
        </CardContent>
      </Card>
    )
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

function DoneCard({ done, items, suggestions, onSuggestionSelect }: { done: ReactDoneEvent; items?: StepItem[]; suggestions?: string[]; onSuggestionSelect?: (query: string) => void }) {
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
        {(suggestions?.length || done.suggestions?.length) && onSuggestionSelect ? (
          <SuggestedFollowups
            suggestions={suggestions?.length ? suggestions : done.suggestions!}
            onSelect={onSuggestionSelect}
          />
        ) : null}
      </CardContent>
    </Card>
  )
}
