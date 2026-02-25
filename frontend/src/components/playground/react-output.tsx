"use client"

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { MarkdownContent } from "@/lib/markdown"
import { Loader2, Wrench, Brain, CheckCircle2, AlertCircle, Clock, RefreshCw, BarChart3 } from "lucide-react"
import { fmtDuration } from "@/lib/utils"
import type { ReactStepEvent, ReactDoneEvent } from "@/types/api"
import type { StepItem } from "@/hooks/use-react-steps"

interface ReactOutputProps {
  items: StepItem[]
}

export function ReactOutput({ items }: ReactOutputProps) {
  return (
    <div className="space-y-3 min-w-0 w-full">
      {items.map((item, idx) => {
        if (item.event === "step") {
          const step = item.data as ReactStepEvent
          return (
            <div key={idx} data-react-idx={idx}>
              <StepCard step={step} duration={item.duration} displayIteration={item.displayIteration} />
            </div>
          )
        }
        if (item.event === "done") {
          const done = item.data as ReactDoneEvent
          return (
            <div key={idx} data-react-idx={idx}>
              <DoneCard done={done} />
            </div>
          )
        }
        return null
      })}
    </div>
  )
}

function StepCard({ step, duration, displayIteration }: { step: ReactStepEvent; duration?: number; displayIteration?: number }) {
  const iterLabel = displayIteration ?? (step.iteration ?? 0) + 1

  if (step.type === "thinking") {
    return (
      <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-amber-500/20 py-4">
        <CardContent className="space-y-2">
          <div className="flex items-center gap-3">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500/10">
              <Brain className="h-3.5 w-3.5 text-amber-500" />
            </div>
            <Badge
              variant="outline"
              className="border-amber-500/30 text-amber-500 text-[10px] uppercase tracking-wider"
            >
              Thinking
            </Badge>
            <span className="text-xs text-muted-foreground">
              Iteration {iterLabel}
            </span>
            {duration != null && (
              <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground">
                <Clock className="h-2.5 w-2.5" />
                {fmtDuration(duration)}
              </span>
            )}
          </div>
          {step.reasoning && (
            <p className="text-sm italic text-muted-foreground leading-relaxed pl-9">
              {step.reasoning}
            </p>
          )}
        </CardContent>
      </Card>
    )
  }

  if (step.type === "tool_start") {
    return (
      <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-blue-500/20 py-4">
        <CardContent className="space-y-2">
          <div className="flex items-center gap-3">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-500/10">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />
            </div>
            <Badge
              variant="outline"
              className="border-blue-500/30 text-blue-500 text-[10px] uppercase tracking-wider"
            >
              Tool
            </Badge>
            <span className="text-sm font-medium text-foreground">
              {step.tool_name}
            </span>
            <span className="text-xs text-muted-foreground">
              Iteration {iterLabel}
            </span>
          </div>
          {step.reasoning && (
            <p className="text-sm italic text-muted-foreground leading-relaxed pl-9">
              {step.reasoning}
            </p>
          )}
          {step.tool_args && Object.keys(step.tool_args).length > 0 && (
            <ToolArgsBlock args={step.tool_args} className="ml-9" />
          )}
          <div className="flex items-center gap-2 ml-9 text-sm text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span className="shiny-text">Executing...</span>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (step.type === "tool_call") {
    return (
      <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-blue-500/20 py-4">
        <CardContent className="space-y-2">
          <div className="flex items-center gap-3">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-500/10">
              <Wrench className="h-3.5 w-3.5 text-blue-500" />
            </div>
            <Badge
              variant="outline"
              className="border-blue-500/30 text-blue-500 text-[10px] uppercase tracking-wider"
            >
              Tool
            </Badge>
            <span className="text-sm font-medium text-foreground">
              {step.tool_name}
            </span>
            <span className="text-xs text-muted-foreground">
              Iteration {iterLabel}
            </span>
            {duration != null && (
              <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground">
                <Clock className="h-2.5 w-2.5" />
                {fmtDuration(duration)}
              </span>
            )}
          </div>
          {step.reasoning && (
            <p className="text-sm italic text-muted-foreground leading-relaxed pl-9">
              {step.reasoning}
            </p>
          )}
          {step.tool_args && Object.keys(step.tool_args).length > 0 && (
            <ToolArgsBlock args={step.tool_args} className="ml-9" />
          )}
          {step.observation && (
            <div className="rounded-md border border-border/50 bg-muted/30 p-3 ml-9">
              <p className="text-xs font-medium text-muted-foreground mb-1 uppercase tracking-wider">
                Observation
              </p>
              <pre className="whitespace-pre-wrap text-sm text-foreground/90 font-mono leading-relaxed max-h-[300px] overflow-y-auto">
                {step.observation}
              </pre>
            </div>
          )}
          {step.error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 ml-9">
              <div className="flex items-center gap-1.5 mb-1">
                <AlertCircle className="h-3 w-3 text-destructive" />
                <p className="text-xs font-medium text-destructive uppercase tracking-wider">
                  Error
                </p>
              </div>
              <pre className="whitespace-pre-wrap text-sm text-destructive/90 font-mono">
                {step.error}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>
    )
  }

  return null
}

function ToolArgsBlock({
  args,
  className,
}: {
  args: Record<string, unknown>
  className?: string
}) {
  if (typeof args.code === "string") {
    const rest = { ...args }
    delete rest.code
    const hasRest = Object.keys(rest).length > 0
    return (
      <div className={className}>
        <MarkdownContent
          content={`\`\`\`python\n${args.code}\n\`\`\``}
          className="text-xs [&_pre]:my-0 [&_pre]:p-3 [&_pre]:max-h-[300px] [&_pre]:overflow-y-auto"
        />
        {hasRest && (
          <MarkdownContent
            content={`\`\`\`json\n${JSON.stringify(rest, null, 2)}\n\`\`\``}
            className="text-xs [&_pre]:my-0 [&_pre]:p-3 [&_pre]:max-h-[300px] [&_pre]:overflow-y-auto mt-2"
          />
        )}
      </div>
    )
  }
  return (
    <MarkdownContent
      content={`\`\`\`json\n${JSON.stringify(args, null, 2)}\n\`\`\``}
      className={`text-xs [&_pre]:my-0 [&_pre]:p-3 [&_pre]:max-h-[300px] [&_pre]:overflow-y-auto ${className ?? ""}`}
    />
  )
}

function DoneCard({ done }: { done: ReactDoneEvent }) {
  return (
    <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-green-500/20 py-4">
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10">
            <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
          </div>
          <CardTitle className="text-sm">Result</CardTitle>
          <div className="ml-auto flex items-center gap-3 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <RefreshCw className="h-2.5 w-2.5" />
              {done.iterations} iteration{done.iterations !== 1 ? "s" : ""}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(done.elapsed)}
            </span>
            {done.usage && (
              <span className="flex items-center gap-1">
                <BarChart3 className="h-2.5 w-2.5" />
                {done.usage.prompt_tokens} in · {done.usage.completion_tokens} out
              </span>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <MarkdownContent
          content={done.answer}
          className="prose-sm text-sm text-foreground/90"
        />
      </CardContent>
    </Card>
  )
}
