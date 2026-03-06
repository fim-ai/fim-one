"use client"

import { useCallback, useMemo } from "react"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Globe, Code, Sparkles, GitBranch, ArrowRight } from "lucide-react"

type AgentMode = "react" | "dag"
type Language = "en" | "zh"

interface ExamplesProps {
  mode: AgentMode
  language: Language
  onLanguageChange: (lang: Language) => void
  onSelect: (query: string) => void
  disabled?: boolean
  agentPrompts?: string[] | null
  agentName?: string | null
  agentIcon?: string | null
}

interface ExampleItem {
  text: string
  category: "web" | "code" | "hybrid"
}

const CATEGORY_META: Record<
  ExampleItem["category"],
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

const EXAMPLES: Record<AgentMode, Record<Language, ExampleItem[]>> = {
  react: {
    en: [
      {
        text: "What are the top 5 stories on Hacker News right now? Fetch the page and give me a one-line summary of each",
        category: "web",
      },
      {
        text: "Search for the latest SpaceX launch, find the mission details, and calculate how many launches they've done this year",
        category: "web",
      },
      {
        text: "Look up the current population of the world's 5 largest cities, then calculate what % of the global population they hold",
        category: "web",
      },
      {
        text: "Simulate the Monty Hall problem 10,000 times -- should you switch doors? Show the win rates",
        category: "code",
      },
      {
        text: "Generate a random 15x15 maze and solve it with BFS, show the maze and solution path as ASCII art",
        category: "code",
      },
      {
        text: "Simulate a Rock-Paper-Scissors tournament: 8 AI strategies compete in elimination rounds -- who wins?",
        category: "code",
      },
      {
        text: "Fetch the Wikipedia page for 'Collatz conjecture', extract the formula, then test it on all numbers from 1 to 10,000 -- which starting number produces the longest chain?",
        category: "hybrid",
      },
      {
        text: "Search for today's weather in Tokyo, then write a Python program to convert and display the temperatures in Celsius, Fahrenheit, and Kelvin",
        category: "hybrid",
      },
    ],
    zh: [
      {
        text: "现在 Hacker News 上最火的 5 篇文章是什么？抓取页面并给出每篇的一句话摘要",
        category: "web",
      },
      {
        text: "搜索 SpaceX 最近一次发射的任务详情，算一算他们今年总共发射了多少次",
        category: "web",
      },
      {
        text: "查一下世界上人口最多的 5 个城市现在各有多少人，算出它们占全球总人口的百分比",
        category: "web",
      },
      {
        text: "模拟蒙提霍尔问题 10,000 次——应该换门吗？展示胜率统计",
        category: "code",
      },
      {
        text: "随机生成一个 15x15 迷宫并用 BFS 求解，用 ASCII 字符画展示迷宫和路径",
        category: "code",
      },
      {
        text: "石头剪刀布锦标赛：8 种 AI 策略淘汰赛，谁能笑到最后？",
        category: "code",
      },
      {
        text: "抓取维基百科'考拉兹猜想'页面，提取公式，然后对 1~10,000 所有数字测试——哪个起始数字产生的链最长？",
        category: "hybrid",
      },
      {
        text: "搜索东京今天的天气，然后写 Python 程序把温度转换成摄氏、华氏和开尔文分别展示",
        category: "hybrid",
      },
    ],
  },
  dag: {
    en: [
      {
        text: "Search for Python, Rust, and Go on the TIOBE index in parallel, then synthesize a report comparing their popularity trends and job market outlook",
        category: "web",
      },
      {
        text: "Fetch the Hacker News front page, find the top 5 stories, then fetch and summarize each article in parallel",
        category: "web",
      },
      {
        text: "Fetch the Hacker News front page, Reddit r/programming hot posts, and GitHub trending repos in parallel, then produce a unified 'Tech Pulse' briefing",
        category: "web",
      },
      {
        text: "Search for reviews of ChatGPT, Claude, and Gemini in parallel, then create a comparison table rating each on speed, accuracy, and creativity",
        category: "web",
      },
      {
        text: "Fetch the Wikipedia pages for Earth, Mars, and Jupiter in parallel, extract key stats (mass, radius, distance from Sun), then calculate how much you'd weigh on each planet",
        category: "hybrid",
      },
      {
        text: "Search for the current price of Bitcoin, Ethereum, and Solana in parallel, then calculate their 24h changes and generate an investment risk comparison",
        category: "hybrid",
      },
      {
        text: "Fetch 3 different news articles about AI regulation in parallel, summarize each, then write Python code to find common themes using word frequency analysis",
        category: "hybrid",
      },
      {
        text: "Search for the population of New York, London, and Tokyo in parallel, then simulate a random 'city growth race' over 50 years and report who wins",
        category: "hybrid",
      },
      {
        text: "Generate a random 20x20 maze, then solve it using BFS and DFS in parallel, compare which explored fewer cells and visualize both paths in ASCII",
        category: "code",
      },
    ],
    zh: [
      {
        text: "并行搜索 Python、Rust、Go 在 TIOBE 指数上的排名，然后综合一份报告对比它们的流行趋势和就业前景",
        category: "web",
      },
      {
        text: "抓取 Hacker News 首页，找出最火的 5 篇文章，然后并行抓取每篇文章并生成一句话摘要",
        category: "web",
      },
      {
        text: "并行抓取 Hacker News 首页、Reddit r/programming 热帖和 GitHub Trending 仓库，生成一份统一的'技术脉搏'简报",
        category: "web",
      },
      {
        text: "并行搜索 ChatGPT、Claude 和 Gemini 的评测，然后生成对比表格，从速度、准确性、创造力三个维度打分",
        category: "web",
      },
      {
        text: "并行抓取维基百科上地球、火星和木星的页面，提取关键数据（质量、半径、距太阳距离），然后算出你在每个星球上的体重",
        category: "hybrid",
      },
      {
        text: "并行搜索 Bitcoin、Ethereum 和 Solana 的当前价格，计算 24 小时涨跌幅，生成投资风险对比分析",
        category: "hybrid",
      },
      {
        text: "并行抓取 3 篇关于 AI 监管的新闻文章，分别摘要，然后用 Python 词频分析找出共同主题",
        category: "hybrid",
      },
      {
        text: "并行搜索纽约、伦敦、东京的人口数据，然后模拟一个 50 年的'城市增长竞赛'，看谁先到 2000 万",
        category: "hybrid",
      },
      {
        text: "随机生成 20x20 迷宫，然后用 BFS 和 DFS 并行求解，对比哪个探索的格子更少，用 ASCII 画出两条路径",
        category: "code",
      },
    ],
  },
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
  language,
  onLanguageChange,
  onSelect,
  disabled,
  agentPrompts,
  agentName,
  agentIcon,
}: ExamplesProps) {
  const t = useTranslations("playground")
  const allExamples = EXAMPLES[mode][language]
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
                "group relative flex items-center gap-3 rounded-xl border border-border bg-card p-4 text-left transition-all duration-200 shadow-sm" +
                " hover:border-primary/30 hover:shadow-md hover:shadow-black/5 hover:-translate-y-0.5" +
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
            {mode === "react" ? t("reactSubtitle") : t("dagSubtitle")}
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-border/50 bg-muted/30 p-0.5">
          <Button
            variant={language === "en" ? "secondary" : "ghost"}
            size="xs"
            onClick={() => onLanguageChange("en")}
            className="text-xs rounded-md"
          >
            EN
          </Button>
          <Button
            variant={language === "zh" ? "secondary" : "ghost"}
            size="xs"
            onClick={() => onLanguageChange("zh")}
            className="text-xs rounded-md"
          >
            中文
          </Button>
        </div>
      </div>

      {/* Mode indicator */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5 rounded-full border border-border/40 bg-muted/20 px-3 py-1 text-xs text-muted-foreground">
          {mode === "react" ? (
            <Sparkles className="h-3 w-3" />
          ) : (
            <GitBranch className="h-3 w-3" />
          )}
          {mode === "react" ? t("standardMode") : t("plannerMode")}
        </div>
      </div>

      {/* Cards grid */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {examples.map((example, i) => {
          const meta = CATEGORY_META[example.category]
          const Icon = meta.icon

          return (
            <button
              key={`${language}-${mode}-${i}`}
              type="button"
              disabled={disabled}
              onClick={() => handleSelect(example.text)}
              className={
                "group relative flex flex-col gap-3 rounded-xl border border-border bg-card p-4 text-left transition-all duration-200 shadow-sm" +
                " hover:border-primary/30 hover:shadow-md hover:shadow-black/5 hover:-translate-y-0.5" +
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

export type { AgentMode, Language }
