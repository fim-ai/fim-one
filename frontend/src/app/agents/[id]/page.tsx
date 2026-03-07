"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { ArrowLeft, Loader2, Bot, MessageSquare } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { useAuth } from "@/contexts/auth-context"
import { agentApi } from "@/lib/api"
import { AgentSettingsForm } from "@/components/agents/agent-settings-form"
import { AgentAIPanel } from "@/components/agents/agent-ai-panel"
import type { AgentResponse } from "@/types/agent"

export default function AgentEditorPage() {
  const t = useTranslations("agents")
  const params = useParams()
  const router = useRouter()
  const { user, isLoading: authLoading } = useAuth()

  const id = params.id as string
  const [agent, setAgent] = useState<AgentResponse | null>(null)
  const [isNew, setIsNew] = useState(id === "new")
  const [isLoading, setIsLoading] = useState(id !== "new")
  const [formDirty, setFormDirty] = useState(false)
  const [showLeaveDialog, setShowLeaveDialog] = useState(false)

  // Warn on browser refresh / tab close
  useEffect(() => {
    if (!formDirty) return
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [formDirty])

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const loadAgent = useCallback(async () => {
    if (id === "new") return
    try {
      setIsLoading(true)
      const data = await agentApi.get(id)
      setAgent(data)
      setIsNew(false)
    } catch (err) {
      console.error("Failed to load agent:", err)
      router.replace("/agents")
    } finally {
      setIsLoading(false)
    }
  }, [id, router])

  useEffect(() => {
    if (user && id !== "new") loadAgent()
  }, [user, id, loadAgent])

  const handleAgentSaved = (saved: AgentResponse) => {
    setAgent(saved)
    if (isNew) {
      setIsNew(false)
      router.replace(`/agents/${saved.id}`)
    }
  }

  if (authLoading || !user) return null

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border/40 shrink-0">
        <Tooltip>
          <TooltipTrigger asChild>
            {formDirty ? (
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => setShowLeaveDialog(true)}
              >
                <ArrowLeft className="h-4 w-4" />
              </Button>
            ) : (
              <Button variant="ghost" size="icon-xs" asChild>
                <Link href="/agents">
                  <ArrowLeft className="h-4 w-4" />
                </Link>
              </Button>
            )}
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={5}>{t("backToAgents")}</TooltipContent>
        </Tooltip>
        <h1 className="text-sm font-semibold text-foreground truncate flex items-center gap-2">
          {agent?.icon ? (
            <span className="text-base leading-none shrink-0">{agent.icon}</span>
          ) : (
            <Bot className="h-4 w-4 shrink-0" />
          )}
          {isNew ? t("newAgent") : agent?.name || t("agent")}
        </h1>
        <div className="flex-1" />
        {agent && agent.status === "published" && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5 text-muted-foreground hover:text-primary"
                asChild
              >
                <Link href={`/new?agent=${agent.id}`}>
                  <MessageSquare className="h-4 w-4" />
                  {t("startChat")}
                </Link>
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t("startChatTooltip")}</TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Main content: left AI chat + right form */}
      <div className="flex flex-1 min-h-0">
        {/* Left: AI Chat Panel (1/3) */}
        <div className="w-1/3 border-r border-border flex flex-col min-h-0">
          <AgentAIPanel
            agentId={agent?.id ?? null}
            onAgentUpdated={(updated) => setAgent(updated)}
            formDirty={formDirty}
            isNewMode={isNew}
            onAgentCreated={handleAgentSaved}
          />
        </div>

        {/* Right: Settings Form (2/3) */}
        <div className="w-2/3 flex flex-col min-h-0 px-4 py-4">
          <AgentSettingsForm
            agent={agent}
            onSaved={handleAgentSaved}
            onDirtyChange={setFormDirty}
          />
        </div>
      </div>

      <AlertDialog open={showLeaveDialog} onOpenChange={setShowLeaveDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("unsavedChangesTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("unsavedChangesDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("stay")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => router.push("/agents")}>
              {t("discardAndLeave")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
