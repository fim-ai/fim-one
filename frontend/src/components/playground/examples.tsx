"use client"

import { useCallback, useMemo } from "react"
import { useTranslations } from "next-intl"
import { Globe, Code, Sparkles, GitBranch, ArrowRight, Zap } from "lucide-react"

type AgentMode = "react" | "dag" | "auto"

interface ExamplesProps {
  mode: AgentMode
  onSelect: (query: string) => void
  disabled?: boolean
  agentPrompts?: string[] | null
  agentName?: string | null
  agentIcon?: string | null
}

type ExampleCategory = "web" | "code" | "hybrid"

interface ExampleItem {
  text: string
  category: ExampleCategory
}

const CATEGORY_META: Record<
  ExampleCategory,
  { icon: typeof Globe; tKey: string; color: string }
> = {
  web: {
    icon: Globe,
    tKey: "categoryWeb",
    color: "text-amber-400",
  },
  code: {
    icon: Code,
    tKey: "categoryCode",
    color: "text-emerald-400",
  },
  hybrid: {
    icon: Sparkles,
    tKey: "categoryHybrid",
    color: "text-amber-400",
  },
}

/**
 * Category assignments per mode — kept in code (not i18n) because these are
 * programmatic enum values, not translatable UI text.  Order must match the
 * corresponding `examples.<mode>` string arrays in playground.json.
 */
const EXAMPLE_CATEGORIES: Record<AgentMode, ExampleCategory[]> = {
  react:  ["web", "web", "code", "code", "hybrid", "hybrid"],
  dag:    ["web", "web", "code", "code", "hybrid", "hybrid"],
  auto:   ["web", "code", "hybrid", "web", "hybrid", "code"],
}


/** Pick a stable pseudo-random subset: hash by mode+lang to get a consistent selection per session */
function pickExamples(items: ExampleItem[], count: number): ExampleItem[] {
  if (items.length <= count) return items
  // Pick one from each category, then fill remaining slots
  const categories: ExampleItem["category"][] = ["web", "code", "hybrid"]
  const picked: ExampleItem[] = []
  const usedIndices = new Set<number>()

  for (const cat of categories) {
    const candidates = items
      .map((item, idx) => ({ item, idx }))
      .filter(({ item }) => item.category === cat)
    if (candidates.length > 0) {
      const choice = candidates[0]
      picked.push(choice.item)
      usedIndices.add(choice.idx)
    }
    if (picked.length >= count) break
  }

  // Fill remaining from round-robin across categories
  let catIdx = 0
  while (picked.length < count) {
    const cat = categories[catIdx % categories.length]
    const candidates = items
      .map((item, idx) => ({ item, idx }))
      .filter(({ item, idx }) => item.category === cat && !usedIndices.has(idx))
    if (candidates.length > 0) {
      const choice = candidates[0]
      picked.push(choice.item)
      usedIndices.add(choice.idx)
    }
    catIdx++
    // Safety: prevent infinite loop if all items exhausted
    if (catIdx > count * 3) break
  }

  return picked
}

const DISPLAY_COUNT = 6

export function Examples({
  mode,
  onSelect,
  disabled,
  agentPrompts,
  agentName,
  agentIcon,
}: ExamplesProps) {
  const t = useTranslations("playground")
  const texts = t.raw(`examples.${mode}`) as string[]
  const categories = EXAMPLE_CATEGORIES[mode]
  const allExamples: ExampleItem[] = useMemo(
    () => texts.map((text, i) => ({ text, category: categories[i] ?? "web" })),
    [texts, categories]
  )
  const examples = useMemo(
    () => pickExamples(allExamples, DISPLAY_COUNT),
    [allExamples]
  )

  const handleSelect = useCallback(
    (query: string) => {
      if (!disabled) {
        onSelect(query)
      }
    },
    [disabled, onSelect]
  )

  const hasAgentPrompts = agentPrompts && agentPrompts.length > 0

  // Agent-specific prompts layout
  if (hasAgentPrompts) {
    return (
      <div className="mx-auto w-full max-w-3xl space-y-6 px-4">
        {/* Agent header */}
        <div className="space-y-1">
          <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
            {agentIcon && <span className="text-xl">{agentIcon}</span>}
            {agentName}
          </h2>
          <p className="text-sm text-muted-foreground">
            {t("suggestedPrompts")}
          </p>
        </div>

        {/* Agent prompt cards */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {agentPrompts.map((prompt, i) => (
            <button
              key={`agent-prompt-${i}`}
              type="button"
              disabled={disabled}
              onClick={() => handleSelect(prompt)}
              className={
                "group relative flex items-center gap-3 rounded-xl border border-border bg-card p-4 text-left transition-all duration-200 card-shadow" +
                " hover:border-primary/30 hover:card-shadow-hover hover:-translate-y-0.5" +
                " focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background" +
                (disabled ? " opacity-50 pointer-events-none" : " cursor-pointer")
              }
            >
              <p className="flex-1 text-[13px] leading-relaxed text-muted-foreground transition-colors duration-200 group-hover:text-foreground/90">
                {prompt}
              </p>
              <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground/0 transition-all duration-200 group-hover:text-muted-foreground/70 group-hover:translate-x-0.5" />
            </button>
          ))}
        </div>
      </div>
    )
  }

  // Default hardcoded examples layout
  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold text-foreground">
            {t("tryExample")}
          </h2>
          <p className="text-sm text-muted-foreground">
            {mode === "auto" ? t("autoSubtitle") : mode === "react" ? t("reactSubtitle") : t("dagSubtitle")}
          </p>
        </div>
      </div>

      {/* Mode indicator */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5 rounded-full border border-border/40 bg-muted/20 px-3 py-1 text-xs text-muted-foreground">
          {mode === "auto" ? (
            <Sparkles className="h-3 w-3" />
          ) : mode === "react" ? (
            <Zap className="h-3 w-3" />
          ) : (
            <GitBranch className="h-3 w-3" />
          )}
          {mode === "auto" ? t("autoMode") : mode === "react" ? t("standardMode") : t("plannerMode")}
        </div>
      </div>

      {/* Cards grid */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {examples.map((example, i) => {
          const meta = CATEGORY_META[example.category]
          const Icon = meta.icon

          return (
            <button
              key={`${mode}-${i}`}
              type="button"
              disabled={disabled}
              onClick={() => handleSelect(example.text)}
              className={
                "group relative flex flex-col gap-3 rounded-xl border border-border bg-card p-4 text-left transition-all duration-200 card-shadow" +
                " hover:border-primary/30 hover:card-shadow-hover hover:-translate-y-0.5" +
                " focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background" +
                (disabled ? " opacity-50 pointer-events-none" : " cursor-pointer")
              }
            >
              {/* Category tag */}
              <div className="flex items-center justify-between">
                <span
                  className={
                    "inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider " +
                    meta.color
                  }
                >
                  <Icon className="h-3 w-3" />
                  {t(meta.tKey)}
                </span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/0 transition-all duration-200 group-hover:text-muted-foreground/70 group-hover:translate-x-0.5" />
              </div>

              {/* Example text */}
              <p className="text-[13px] leading-relaxed text-muted-foreground transition-colors duration-200 group-hover:text-foreground/90">
                {example.text}
              </p>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export type { AgentMode }
