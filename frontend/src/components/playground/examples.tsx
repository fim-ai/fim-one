"use client"

import { useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

type AgentMode = "react" | "dag"
type Language = "en" | "zh"

interface ExamplesProps {
  mode: AgentMode
  language: Language
  onLanguageChange: (lang: Language) => void
  onSelect: (query: string) => void
  disabled?: boolean
}

const EXAMPLES: Record<AgentMode, Record<Language, string[]>> = {
  react: {
    en: [
      // Web + analysis
      "What are the top 5 stories on Hacker News right now? Fetch the page and give me a one-line summary of each",
      "Search for the latest SpaceX launch, find the mission details, and calculate how many launches they've done this year",
      "Look up the current population of the world's 5 largest cities, then calculate what % of the global population they hold",
      // Pure code (keep a few fun ones)
      "Simulate the Monty Hall problem 10,000 times — should you switch doors? Show the win rates",
      "Generate a random 15x15 maze and solve it with BFS, show the maze and solution path as ASCII art",
      "Simulate a Rock-Paper-Scissors tournament: 8 AI strategies compete in elimination rounds — who wins?",
      // Web + code combo
      "Fetch the Wikipedia page for 'Collatz conjecture', extract the formula, then test it on all numbers from 1 to 10,000 — which starting number produces the longest chain?",
      "Search for today's weather in Tokyo, then write a Python program to convert and display the temperatures in Celsius, Fahrenheit, and Kelvin",
    ],
    zh: [
      // 联网 + 分析
      "现在 Hacker News 上最火的 5 篇文章是什么？抓取页面并给出每篇的一句话摘要",
      "搜索 SpaceX 最近一次发射的任务详情，算一算他们今年总共发射了多少次",
      "查一下世界上人口最多的 5 个城市现在各有多少人，算出它们占全球总人口的百分比",
      // 纯代码
      "模拟蒙提霍尔问题 10,000 次——应该换门吗？展示胜率统计",
      "随机生成一个 15x15 迷宫并用 BFS 求解，用 ASCII 字符画展示迷宫和路径",
      "石头剪刀布锦标赛：8 种 AI 策略淘汰赛，谁能笑到最后？",
      // 联网 + 代码
      "抓取维基百科'考拉兹猜想'页面，提取公式，然后对 1~10,000 所有数字测试——哪个起始数字产生的链最长？",
      "搜索东京今天的天气，然后写 Python 程序把温度转换成摄氏、华氏和开尔文分别展示",
    ],
  },
  dag: {
    en: [
      // Multi-source research → synthesis (real DAG value)
      "Search for Python, Rust, and Go on the TIOBE index in parallel, then synthesize a report comparing their popularity trends and job market outlook",
      "Fetch the Hacker News front page, find the top 5 stories, then fetch and summarize each article in parallel",
      "Fetch the Hacker News front page, Reddit r/programming hot posts, and GitHub trending repos in parallel, then produce a unified 'Tech Pulse' briefing",
      "Search for reviews of ChatGPT, Claude, and Gemini in parallel, then create a comparison table rating each on speed, accuracy, and creativity",
      // Data collection → computation → report
      "Fetch the Wikipedia pages for Earth, Mars, and Jupiter in parallel, extract key stats (mass, radius, distance from Sun), then calculate how much you'd weigh on each planet",
      "Search for the current price of Bitcoin, Ethereum, and Solana in parallel, then calculate their 24h changes and generate an investment risk comparison",
      // Web + code hybrid DAG
      "Fetch 3 different news articles about AI regulation in parallel, summarize each, then write Python code to find common themes using word frequency analysis",
      "Search for the population of New York, London, and Tokyo in parallel, then simulate a random 'city growth race' over 50 years and report who wins",
      // Pure code DAG (one classic kept)
      "Generate a random 20x20 maze, then solve it using BFS and DFS in parallel, compare which explored fewer cells and visualize both paths in ASCII",
    ],
    zh: [
      // 多源调研 → 汇总（真正的 DAG 价值）
      "并行搜索 Python、Rust、Go 在 TIOBE 指数上的排名，然后综合一份报告对比它们的流行趋势和就业前景",
      "抓取 Hacker News 首页，找出最火的 5 篇文章，然后并行抓取每篇文章并生成一句话摘要",
      "并行抓取 Hacker News 首页、Reddit r/programming 热帖和 GitHub Trending 仓库，生成一份统一的'技术脉搏'简报",
      "并行搜索 ChatGPT、Claude 和 Gemini 的评测，然后生成对比表格，从速度、准确性、创造力三个维度打分",
      // 数据采集 → 计算 → 报告
      "并行抓取维基百科上地球、火星和木星的页面，提取关键数据（质量、半径、距太阳距离），然后算出你在每个星球上的体重",
      "并行搜索 Bitcoin、Ethereum 和 Solana 的当前价格，计算 24 小时涨跌幅，生成投资风险对比分析",
      // 联网 + 代码混合 DAG
      "并行抓取 3 篇关于 AI 监管的新闻文章，分别摘要，然后用 Python 词频分析找出共同主题",
      "并行搜索纽约、伦敦、东京的人口数据，然后模拟一个 50 年的'城市增长竞赛'，看谁先到 2000 万",
      // 纯代码 DAG（保留一个经典）
      "随机生成 20x20 迷宫，然后用 BFS 和 DFS 并行求解，对比哪个探索的格子更少，用 ASCII 画出两条路径",
    ],
  },
}

export function Examples({
  mode,
  language,
  onLanguageChange,
  onSelect,
  disabled,
}: ExamplesProps) {
  const examples = EXAMPLES[mode][language]

  const handleSelect = useCallback(
    (query: string) => {
      if (!disabled) {
        onSelect(query)
      }
    },
    [disabled, onSelect]
  )

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          Examples
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant={language === "en" ? "secondary" : "ghost"}
            size="xs"
            onClick={() => onLanguageChange("en")}
            className="text-xs"
          >
            EN
          </Button>
          <Button
            variant={language === "zh" ? "secondary" : "ghost"}
            size="xs"
            onClick={() => onLanguageChange("zh")}
            className="text-xs"
          >
            中文
          </Button>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {examples.map((example, i) => (
          <Badge
            key={i}
            variant="outline"
            className={
              "cursor-pointer text-xs font-normal transition-colors hover:bg-accent hover:text-accent-foreground max-w-full" +
              (disabled ? " opacity-50 pointer-events-none" : "")
            }
            onClick={() => handleSelect(example)}
          >
            <span className="truncate">{example}</span>
          </Badge>
        ))}
      </div>
    </div>
  )
}

export type { AgentMode, Language }
