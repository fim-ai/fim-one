"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter, useParams } from "next/navigation"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { ArrowLeft, Loader2, ChevronDown, ChevronUp, Info } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useAuth } from "@/contexts/auth-context"
import { evalApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import type { EvalRunDetailResponse, EvalCaseResultResponse } from "@/types/eval"
import { cn } from "@/lib/utils"
import { usePageTitle } from "@/hooks/use-page-title"

function StatusBadge({ status }: { status: string }) {
  const t = useTranslations("eval")
  const style =
    status === "pass"
      ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
      : status === "fail"
        ? "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300"
        : "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300"
  const label =
    status === "pass"
      ? t("verdictPass")
      : status === "fail"
        ? t("verdictFail")
        : t("verdictError")
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        style,
      )}
    >
      {label}
    </span>
  )
}

function RunStatusBadge({ status }: { status: string }) {
  const t = useTranslations("eval")
  const variants: Record<string, string> = {
    pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
    running: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    completed: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
    failed: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  }
  const labels: Record<string, string> = {
    pending: t("statusPending"),
    running: t("statusRunning"),
    completed: t("statusCompleted"),
    failed: t("statusFailed"),
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        variants[status] ?? variants.pending,
      )}
    >
      {status === "running" && <Loader2 className="h-3 w-3 animate-spin" />}
      {labels[status] ?? status}
    </span>
  )
}

function StatsPanel({ results }: { results: EvalCaseResultResponse[] }) {
  const t = useTranslations("eval")
  if (results.length === 0) return null

  const passed = results.filter((r) => r.status === "pass").length
  const failed = results.filter((r) => r.status === "fail").length
  const errors = results.filter((r) => r.status === "error").length
  const total = results.length

  const latencies = results
    .map((r) => r.latency_ms)
    .filter((v): v is number => v != null && v > 0)
  const minLatency = latencies.length > 0 ? Math.min(...latencies) : 0
  const maxLatency = latencies.length > 0 ? Math.max(...latencies) : 0
  const avgLatency =
    latencies.length > 0 ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) : 0

  const totalTokens = results.reduce(
    (sum, r) => sum + (r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0),
    0,
  )

  // Percentages for stacked bar
  const pPass = total > 0 ? (passed / total) * 100 : 0
  const pFail = total > 0 ? (failed / total) * 100 : 0
  const pError = total > 0 ? (errors / total) * 100 : 0

  return (
    <div className="mb-6 space-y-4">
      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-lg border p-3">
          <div className="text-xs font-medium text-muted-foreground">{t("passed")}</div>
          <div className="mt-1 text-2xl font-semibold text-green-600 dark:text-green-400">
            {passed}
            <span className="ml-1 text-sm font-normal text-muted-foreground">/ {total}</span>
          </div>
        </div>
        <div className="rounded-lg border p-3">
          <div className="text-xs font-medium text-muted-foreground">{t("failed")}</div>
          <div className="mt-1 text-2xl font-semibold text-red-600 dark:text-red-400">
            {failed}
            {errors > 0 && (
              <span className="ml-1 text-sm font-normal text-orange-500">+{errors} {t("errors").toLowerCase()}</span>
            )}
          </div>
        </div>
        <div className="rounded-lg border p-3">
          <div className="text-xs font-medium text-muted-foreground">{t("latencyDistribution")}</div>
          <div className="mt-1 text-sm tabular-nums">
            <span className="text-muted-foreground">{t("latencyMin")} </span>
            <span className="font-medium">{minLatency.toLocaleString()}</span>
            <span className="mx-1 text-muted-foreground">/</span>
            <span className="text-muted-foreground">{t("latencyAvg")} </span>
            <span className="font-medium">{avgLatency.toLocaleString()}</span>
            <span className="mx-1 text-muted-foreground">/</span>
            <span className="text-muted-foreground">{t("latencyMax")} </span>
            <span className="font-medium">{maxLatency.toLocaleString()}</span>
          </div>
        </div>
        <div className="rounded-lg border p-3">
          <div className="text-xs font-medium text-muted-foreground">{t("totalTokens")}</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">
            {totalTokens.toLocaleString()}
          </div>
        </div>
      </div>

      {/* Stacked distribution bar */}
      <div>
        <div className="mb-1.5 flex items-center justify-between text-xs text-muted-foreground">
          <span>{t("distribution")}</span>
          <span>
            {Math.round(pPass)}% {t("verdictPass")} / {Math.round(pFail)}% {t("verdictFail")}
            {errors > 0 && ` / ${Math.round(pError)}% ${t("verdictError")}`}
          </span>
        </div>
        <div className="flex h-3 overflow-hidden rounded-full bg-muted">
          {pPass > 0 && (
            <div
              className="bg-green-500 transition-all"
              style={{ width: `${pPass}%` }}
            />
          )}
          {pFail > 0 && (
            <div
              className="bg-red-500 transition-all"
              style={{ width: `${pFail}%` }}
            />
          )}
          {pError > 0 && (
            <div
              className="bg-orange-400 transition-all"
              style={{ width: `${pError}%` }}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function CaseResultRow({ result }: { result: EvalCaseResultResponse }) {
  const t = useTranslations("eval")
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <tr className="border-b last:border-0">
        <td className="px-4 py-2 max-w-xs">
          <span className="line-clamp-2 text-sm">{result.case_prompt ?? result.case_id}</span>
        </td>
        <td className="px-4 py-2">
          <StatusBadge status={result.status} />
        </td>
        <td className="px-4 py-2 text-sm text-muted-foreground max-w-xs">
          <span className="line-clamp-2">{result.agent_answer ?? "—"}</span>
        </td>
        <td className="px-4 py-2 text-sm text-muted-foreground max-w-xs">
          <span className="line-clamp-2">{result.grader_reasoning ?? "—"}</span>
        </td>
        <td className="px-4 py-2 text-sm text-muted-foreground whitespace-nowrap">
          {result.latency_ms != null ? `${result.latency_ms}${t("ms")}` : "—"}
        </td>
        <td className="px-4 py-2 text-sm text-muted-foreground whitespace-nowrap">
          {result.prompt_tokens != null || result.completion_tokens != null
            ? `${(result.prompt_tokens ?? 0) + (result.completion_tokens ?? 0)}`
            : "—"}
        </td>
        <td className="px-4 py-2">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            {expanded ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
            {expanded ? t("collapse") : t("expand")}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="border-b last:border-0 bg-muted/20">
          <td colSpan={7} className="px-4 py-3">
            <div className="grid grid-cols-1 gap-3 text-sm">
              {result.case_expected_behavior && (
                <div>
                  <div className="font-medium text-xs text-muted-foreground mb-1">
                    {t("expectedLabel")}
                  </div>
                  <div className="whitespace-pre-wrap">{result.case_expected_behavior}</div>
                </div>
              )}
              {result.agent_answer && (
                <div>
                  <div className="font-medium text-xs text-muted-foreground mb-1">
                    {t("agentAnswer")}
                  </div>
                  <div className="whitespace-pre-wrap">{result.agent_answer}</div>
                </div>
              )}
              {result.grader_reasoning && (
                <div>
                  <div className="font-medium text-xs text-muted-foreground mb-1">
                    {t("graderReasoning")}
                  </div>
                  <div className="whitespace-pre-wrap">{result.grader_reasoning}</div>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function RunResultsPage() {
  const t = useTranslations("eval")
  const tError = useTranslations("errors")
  const router = useRouter()
  const params = useParams()
  const runId = params.id as string
  const { user, isLoading: authLoading } = useAuth()

  const [run, setRun] = useState<EvalRunDetailResponse | null>(null)

  usePageTitle(run ? `${run.agent_name} — ${run.dataset_name}` : undefined)

  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login")
  }, [authLoading, user, router])

  const loadRun = useCallback(async () => {
    try {
      const data = await evalApi.getRun(runId)
      setRun(data)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setLoading(false)
    }
  }, [runId, tError])

  useEffect(() => {
    if (user) loadRun()
  }, [user, loadRun])

  // Poll every 3s while pending/running
  useEffect(() => {
    if (!run || (run.status !== "pending" && run.status !== "running")) return
    const interval = setInterval(loadRun, 3000)
    return () => clearInterval(interval)
  }, [run, loadRun])

  if (loading || !run) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const passRate =
    run.total_cases > 0 ? Math.round((run.passed_cases / run.total_cases) * 100) : 0

  return (
    <div className="flex flex-col h-full">
      <div className="border-b px-6 py-4">
        <Link
          href="/eval?tab=runs"
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-2 w-fit"
        >
          <ArrowLeft className="h-4 w-4" />
          {t("backToEval")}
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <h1 className="text-xl font-semibold">
                {run.agent_name ?? run.agent_id} &times;{" "}
                {run.dataset_name ?? run.dataset_id}
              </h1>
              <RunStatusBadge status={run.status} />
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-700 dark:bg-violet-900 dark:text-violet-300">
                      ReAct
                      <Info className="h-3 w-3" />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" className="max-w-xs">
                    <p>{t("executionModeHint")}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              <span>
                {t("passRate")}: {run.passed_cases}/{run.total_cases} ({passRate}%)
              </span>
              {run.avg_latency_ms != null && (
                <span>
                  {t("avgLatency")}: {Math.round(run.avg_latency_ms)}
                  {t("ms")}
                </span>
              )}
              {run.total_tokens != null && (
                <span>
                  {t("totalTokens")}: {run.total_tokens.toLocaleString()}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Progress bar */}
        {(run.status === "running" || run.status === "completed") && run.total_cases > 0 && (
          <div className="mt-3">
            <div className="h-2 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-green-500 transition-all"
                style={{ width: `${passRate}%` }}
              />
            </div>
          </div>
        )}

        {run.error_message && (
          <div className="mt-2 text-sm text-destructive">{run.error_message}</div>
        )}
      </div>

      <div className="flex-1 overflow-auto p-6">
        <StatsPanel results={run.results} />
        {run.results.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            {run.status === "pending" || run.status === "running" ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-sm">{t("statusRunning")}...</span>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No results yet.</p>
            )}
          </div>
        ) : (
          <div className="rounded-md border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/40">
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                    {t("promptLabel")}
                  </th>
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                    {t("resultLabel")}
                  </th>
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                    {t("agentAnswer")}
                  </th>
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                    {t("graderReasoning")}
                  </th>
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                    {t("latency")}
                  </th>
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground">
                    {t("tokens")}
                  </th>
                  <th className="px-4 py-2 text-left font-medium text-muted-foreground"></th>
                </tr>
              </thead>
              <tbody>
                {run.results.map((r) => (
                  <CaseResultRow key={r.id} result={r} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
