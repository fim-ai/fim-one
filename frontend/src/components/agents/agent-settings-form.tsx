"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { Bell, Bot, Check, Info, Loader2, Zap, GitBranch, ShieldCheck, Sparkles } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { EmojiPickerPopover } from "@/components/ui/emoji-picker-popover"
import { SuggestedPromptsEditor } from "@/components/agents/suggested-prompts-editor"
import { agentApi, kbApi, connectorApi, mcpServerApi, modelApi } from "@/lib/api"
import { channelsApi } from "@/lib/api/channels"
import type { AgentCreate, AgentResponse, SandboxConfig } from "@/types/agent"
import type { Channel } from "@/types/channel"
import type { ConnectorResponse } from "@/types/connector"
import type { MCPServerResponse } from "@/types/mcp-server"
import type { ModelConfigResponse } from "@/types/model_config"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { useToolCatalog } from "@/hooks/use-tool-catalog"

// Ordered: general first → common tools → advanced/specialized
const TOOL_CATEGORIES = ["general", "web", "computation", "filesystem", "knowledge"] as const

// Tool category i18n key mapping
const TOOL_CATEGORY_KEYS: Record<string, { label: string; description: string; tools: string }> = {
  knowledge: { label: "toolCategoryKnowledge", description: "toolCategoryKnowledgeDesc", tools: "toolCategoryKnowledgeTools" },
  web: { label: "toolCategoryWeb", description: "toolCategoryWebDesc", tools: "toolCategoryWebTools" },
  computation: { label: "toolCategoryComputation", description: "toolCategoryComputationDesc", tools: "toolCategoryComputationTools" },
  filesystem: { label: "toolCategoryFilesystem", description: "toolCategoryFilesystemDesc", tools: "toolCategoryFilesystemTools" },
  general: { label: "toolCategoryGeneral", description: "toolCategoryGeneralDesc", tools: "toolCategoryGeneralTools" },
}

interface AgentSettingsFormProps {
  agent: AgentResponse | null // null = create mode
  onSaved: (agent: AgentResponse) => void
  onDirtyChange?: (dirty: boolean) => void
}

export function AgentSettingsForm({
  agent,
  onSaved,
  onDirtyChange,
}: AgentSettingsFormProps) {
  const t = useTranslations("agents")
  const tc = useTranslations("common")
  const [name, setName] = useState("")
  const [icon, setIcon] = useState<string | null>(null)
  const [description, setDescription] = useState("")
  const [instructions, setInstructions] = useState("")
  const [toolCategories, setToolCategories] = useState<string[]>([])
  const [suggestedPrompts, setSuggestedPrompts] = useState<string[]>([])
  const [selectedKBs, setSelectedKBs] = useState<string[]>([])
  const [selectedConnectors, setSelectedConnectors] = useState<string[]>([])
  const [selectedMCPServers, setSelectedMCPServers] = useState<string[]>([])
  const [executionMode, setExecutionMode] = useState<"react" | "dag" | "auto">("auto")
  const [confidenceThreshold, setConfidenceThreshold] = useState<number | null>(null)
  const [temperature, setTemperature] = useState<number | null>(null)
  const [sandboxMemory, setSandboxMemory] = useState<string>("")
  const [sandboxCpu, setSandboxCpu] = useState<string>("")
  const [sandboxTimeout, setSandboxTimeout] = useState<string>("")
  const [selectedModelConfigId, setSelectedModelConfigId] = useState<string>("")
  const [selectedFastModelConfigId, setSelectedFastModelConfigId] = useState<string>("")
  const [compactInstructions, setCompactInstructions] = useState<string>("")
  const [systemModels, setSystemModels] = useState<ModelConfigResponse[]>([])
  // Completion notification
  const [notifyOnComplete, setNotifyOnComplete] = useState<boolean>(false)
  const [notifyChannelId, setNotifyChannelId] = useState<string>("")
  const [availableChannels, setAvailableChannels] = useState<Channel[]>([])
  const [isLoadingChannels, setIsLoadingChannels] = useState<boolean>(false)
  // Approval routing (Phase 2)
  const [confirmationMode, setConfirmationMode] = useState<
    "auto" | "inline_only" | "channel_only"
  >("auto")
  const [confirmationApproverScope, setConfirmationApproverScope] = useState<
    "initiator" | "agent_owner" | "org_members"
  >("initiator")
  const [requireConfirmationForAll, setRequireConfirmationForAll] = useState<boolean>(false)
  const [approvalChannelId, setApprovalChannelId] = useState<string>("")
  // Follow-up suggestions toggle (default OFF)
  const [suggestFollowups, setSuggestFollowups] = useState<boolean>(false)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  const [availableKBs, setAvailableKBs] = useState<{ id: string; name: string; document_count: number }[]>([])
  const [availableConnectors, setAvailableConnectors] = useState<ConnectorResponse[]>([])
  const [availableMCPServers, setAvailableMCPServers] = useState<MCPServerResponse[]>([])
  // Track whether the inventory fetches have settled, so the orphan/(已删除)
  // branch only fires after we actually know what the user has access to.
  // Without this, a fast agent fetch + slow inventory fetch would briefly
  // render every selected ID as orphan, even if it's a perfectly live resource.
  const [kbsLoaded, setKbsLoaded] = useState(false)
  const [connectorsLoaded, setConnectorsLoaded] = useState(false)
  const [mcpServersLoaded, setMcpServersLoaded] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const { data: catalog } = useToolCatalog()

  const clearFieldError = (key: string) => {
    setFieldErrors((prev) => {
      if (!(key in prev)) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  // Pre-fill when agent prop changes (full sync)
  useEffect(() => {
    if (agent) {
      setName(agent.name)
      setIcon(agent.icon || null)
      setDescription(agent.description || "")
      setInstructions(agent.instructions || "")
      setToolCategories(agent.tool_categories || [])
      setSuggestedPrompts(agent.suggested_prompts || [])
      setSelectedKBs(agent.kb_ids || [])
      setSelectedConnectors(agent.connector_ids || [])
      setSelectedMCPServers(agent.mcp_server_ids || [])
      setExecutionMode(agent.execution_mode || "auto")
      const ct = agent.grounding_config?.confidence_threshold
      setConfidenceThreshold(typeof ct === "number" ? ct : null)
      const rawTemp = agent.model_config_json?.temperature
      setTemperature(typeof rawTemp === "number" ? rawTemp : null)
      setSandboxMemory(agent.sandbox_config?.memory ?? "")
      setSandboxCpu(agent.sandbox_config?.cpu != null ? String(agent.sandbox_config.cpu) : "")
      setSandboxTimeout(agent.sandbox_config?.timeout != null ? String(agent.sandbox_config.timeout) : "")
      setSelectedModelConfigId((agent.model_config_json?.model_config_id as string) ?? "")
      setSelectedFastModelConfigId((agent.model_config_json?.fast_model_config_id as string) ?? "")
      setCompactInstructions(agent.compact_instructions || "")
      // Completion notification config
      const notifications = (agent.model_config_json?.notifications as Record<string, unknown> | undefined) ?? {}
      const onComplete = (notifications?.on_complete as Record<string, unknown> | undefined) ?? {}
      setNotifyOnComplete(onComplete.enabled === true)
      setNotifyChannelId(typeof onComplete.channel_id === "string" ? onComplete.channel_id : "")
      // Approval routing
      setConfirmationMode(
        (agent.confirmation_mode as "auto" | "inline_only" | "channel_only") || "auto"
      )
      setConfirmationApproverScope(
        (agent.confirmation_approver_scope as
          | "initiator"
          | "agent_owner"
          | "org_members") || "initiator"
      )
      setRequireConfirmationForAll(agent.require_confirmation_for_all === true)
      setApprovalChannelId(
        typeof agent.approval_channel_id === "string" ? agent.approval_channel_id : ""
      )
      setSuggestFollowups(agent.suggest_followups === true)
      setFieldErrors({})
    } else {
      setName("")
      setIcon(null)
      setDescription("")
      setInstructions("")
      setExecutionMode("auto")
      setToolCategories([])
      setSuggestedPrompts([])
      setSelectedKBs([])
      setSelectedConnectors([])
      setSelectedMCPServers([])
      setConfidenceThreshold(null)
      setTemperature(null)
      setSandboxMemory("")
      setSandboxCpu("")
      setSandboxTimeout("")
      setSelectedModelConfigId("")
      setSelectedFastModelConfigId("")
      setCompactInstructions("")
      setNotifyOnComplete(false)
      setNotifyChannelId("")
      setConfirmationMode("auto")
      setConfirmationApproverScope("initiator")
      setRequireConfirmationForAll(false)
      setApprovalChannelId("")
      setSuggestFollowups(false)
      setFieldErrors({})
    }
  }, [agent])

  // Load available KBs/connectors on mount
  useEffect(() => {
    kbApi
      .list(1, 100)
      .then((d) => setAvailableKBs(d.items || []))
      .catch(() => setAvailableKBs([]))
      .finally(() => setKbsLoaded(true))
    connectorApi
      .list(1, 100)
      .then((d) => setAvailableConnectors(d.items || []))
      .catch(() => setAvailableConnectors([]))
      .finally(() => setConnectorsLoaded(true))
    mcpServerApi
      .list(1, 100)
      .then((d) => setAvailableMCPServers(d.items || []))
      .catch(() => setAvailableMCPServers([]))
      .finally(() => setMcpServersLoaded(true))
    modelApi.list("llm").then(setSystemModels).catch(() => {})
  }, [])

  // Load org channels for the notification picker. Only relevant if the agent
  // belongs to an org — personal agents have no org-scoped channels to pick.
  useEffect(() => {
    const orgId = agent?.org_id
    if (!orgId) {
      setAvailableChannels([])
      return
    }
    let cancelled = false
    setIsLoadingChannels(true)
    channelsApi
      .list(orgId)
      .then((d) => {
        if (cancelled) return
        setAvailableChannels((d.items || []).filter((c) => c.is_active))
      })
      .catch(() => {
        if (!cancelled) setAvailableChannels([])
      })
      .finally(() => {
        if (!cancelled) setIsLoadingChannels(false)
      })
    return () => {
      cancelled = true
    }
  }, [agent?.org_id])

  // Compute and notify dirty state
  useEffect(() => {
    if (!onDirtyChange) return
    if (!agent) {
      // Create mode: dirty if user typed anything
      onDirtyChange(name.trim() !== "")
      return
    }
    const origNotifications = (agent.model_config_json?.notifications as Record<string, unknown> | undefined) ?? {}
    const origOnComplete = (origNotifications?.on_complete as Record<string, unknown> | undefined) ?? {}
    const origNotifyEnabled = origOnComplete.enabled === true
    const origNotifyChannelId = typeof origOnComplete.channel_id === "string" ? origOnComplete.channel_id : ""

    const dirty =
      name !== agent.name ||
      icon !== (agent.icon || null) ||
      description !== (agent.description || "") ||
      instructions !== (agent.instructions || "") ||
      JSON.stringify(toolCategories) !== JSON.stringify(agent.tool_categories || []) ||
      JSON.stringify(suggestedPrompts) !== JSON.stringify(agent.suggested_prompts || []) ||
      JSON.stringify(selectedKBs) !== JSON.stringify(agent.kb_ids || []) ||
      JSON.stringify(selectedConnectors) !== JSON.stringify(agent.connector_ids || []) ||
      JSON.stringify(selectedMCPServers) !== JSON.stringify(agent.mcp_server_ids || []) ||
      executionMode !== (agent.execution_mode || "auto") ||
      (() => {
        const ct = agent.grounding_config?.confidence_threshold
        const origCt = typeof ct === "number" ? ct : null
        return confidenceThreshold !== origCt
      })() ||
      temperature !== (agent.model_config_json?.temperature ?? null) ||
      sandboxMemory !== (agent.sandbox_config?.memory ?? "") ||
      sandboxCpu !== (agent.sandbox_config?.cpu != null ? String(agent.sandbox_config.cpu) : "") ||
      sandboxTimeout !== (agent.sandbox_config?.timeout != null ? String(agent.sandbox_config.timeout) : "") ||
      selectedModelConfigId !== ((agent.model_config_json?.model_config_id as string) ?? "") ||
      selectedFastModelConfigId !== ((agent.model_config_json?.fast_model_config_id as string) ?? "") ||
      compactInstructions !== (agent.compact_instructions || "") ||
      notifyOnComplete !== origNotifyEnabled ||
      notifyChannelId !== origNotifyChannelId ||
      confirmationMode !== (agent.confirmation_mode || "auto") ||
      confirmationApproverScope !== (agent.confirmation_approver_scope || "initiator") ||
      requireConfirmationForAll !== (agent.require_confirmation_for_all === true) ||
      approvalChannelId !== (agent.approval_channel_id || "") ||
      suggestFollowups !== (agent.suggest_followups === true)
    onDirtyChange(dirty)
  }, [agent, name, icon, description, instructions, executionMode, toolCategories, suggestedPrompts, selectedKBs, selectedConnectors, selectedMCPServers, confidenceThreshold, temperature, sandboxMemory, sandboxCpu, sandboxTimeout, selectedModelConfigId, selectedFastModelConfigId, compactInstructions, notifyOnComplete, notifyChannelId, confirmationMode, confirmationApproverScope, requireConfirmationForAll, approvalChannelId, suggestFollowups, onDirtyChange])

  const toggleCategory = (cat: string) => {
    setToolCategories((prev) =>
      prev.includes(cat)
        ? prev.filter((c) => c !== cat)
        : [...prev, cat]
    )
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) return

    // Validation: if completion notification is on, a channel must be selectable AND selected.
    // Three unreachable states, each with its own feedback surface:
    //   1. Personal agent — Select isn't rendered; inline error would be invisible → toast.
    //   2. Org agent with zero channels — Select isn't rendered either → toast.
    //   3. Org agent with channels but none chosen — Select IS rendered → inline field error.
    if (notifyOnComplete && !notifyChannelId) {
      if (!agent?.org_id) {
        toast.error(t("notifications.personalAgentBlockedSave"))
        return
      }
      if (availableChannels.length === 0) {
        toast.error(t("notifications.emptyChannelsBlockedSave"))
        return
      }
      setFieldErrors((prev) => ({ ...prev, notifyChannel: t("notifications.missingChannelError") }))
      return
    }

    // Approval precheck: channel_only without any reachable channel is unshippable.
    // Block submit if mode=channel_only AND no approval channel AND no completion channel
    // AND org has zero channels — every path to deliver approval would fail.
    if (
      confirmationMode === "channel_only" &&
      !approvalChannelId &&
      !notifyChannelId &&
      availableChannels.length === 0
    ) {
      setFieldErrors((prev) => ({
        ...prev,
        approvalChannel: t("approval.channelOnlyNoChannelsError"),
      }))
      return
    }

    setIsSubmitting(true)
    try {
      const prompts = suggestedPrompts
        .filter((s) => s.trim())

      // Build model_config_json: merge temperature + model_config_id into existing config
      const baseModelConfig = agent?.model_config_json ? { ...agent.model_config_json } : {}
      const merged: Record<string, unknown> = { ...baseModelConfig }
      if (temperature != null) {
        merged.temperature = temperature
      } else {
        delete merged.temperature
      }
      if (selectedModelConfigId) {
        merged.model_config_id = selectedModelConfigId
      } else {
        delete merged.model_config_id
      }
      if (selectedFastModelConfigId) {
        merged.fast_model_config_id = selectedFastModelConfigId
      } else {
        delete merged.fast_model_config_id
      }
      // Completion notification — always persist the shape (even when disabled)
      // so the config is stable and observable by the backend.
      const existingNotifications = (merged.notifications as Record<string, unknown> | undefined) ?? {}
      merged.notifications = {
        ...existingNotifications,
        on_complete: {
          enabled: notifyOnComplete,
          channel_id: notifyOnComplete ? notifyChannelId : null,
        },
      }
      const modelConfigJson = Object.keys(merged).length > 0 ? merged : undefined

      // Build sandbox_config: only include fields that have been set
      const sandboxCfg: SandboxConfig = {}
      if (sandboxMemory) sandboxCfg.memory = sandboxMemory
      if (sandboxCpu) sandboxCfg.cpu = parseFloat(sandboxCpu)
      if (sandboxTimeout) sandboxCfg.timeout = parseInt(sandboxTimeout, 10)
      const hasSandboxConfig = Object.keys(sandboxCfg).length > 0

      const data: AgentCreate = {
        name: trimmedName,
        icon: icon || null,
        description: description.trim() || null,
        instructions: instructions.trim() || null,
        execution_mode: executionMode,
        tool_categories: toolCategories,
        ...(prompts.length > 0 && { suggested_prompts: prompts }),
        kb_ids: selectedKBs,
        connector_ids: selectedConnectors,
        mcp_server_ids: selectedMCPServers,
        ...(selectedKBs.length > 0 && confidenceThreshold != null && {
          grounding_config: { confidence_threshold: confidenceThreshold },
        }),
        ...(modelConfigJson !== undefined && { model_config_json: modelConfigJson }),
        ...(hasSandboxConfig && { sandbox_config: sandboxCfg }),
        compact_instructions: compactInstructions.trim() || null,
        confirmation_mode: confirmationMode,
        confirmation_approver_scope: confirmationApproverScope,
        require_confirmation_for_all: requireConfirmationForAll,
        approval_channel_id: approvalChannelId || null,
        suggest_followups: suggestFollowups,
      }

      let result: AgentResponse
      if (agent) {
        result = await agentApi.update(agent.id, data)
      } else {
        result = await agentApi.create(data)
      }

      onSaved(result)
      toast.success(agent ? t("agentUpdated") : t("agentCreated"))
    } catch (err) {
      console.error("Failed to save agent:", err)
      const message = err instanceof Error ? err.message : "Unknown error"
      toast.error(t("agentSaveFailed", { message }))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col h-full overflow-hidden">
      <ScrollArea className="flex-1 overflow-hidden">
        <div className="space-y-4">
          {/* Name + Icon */}
          <div className="space-y-1.5">
            <label htmlFor="agent-name" className="text-sm font-medium">
              {tc("name")} <span className="text-destructive">*</span>
            </label>
            <div className="flex items-center gap-2">
              <EmojiPickerPopover
                value={icon}
                onChange={setIcon}
                fallbackIcon={<Bot className="h-5 w-5" />}
              />
              <Input
                id="agent-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("namePlaceholder")}
                required
              />
            </div>
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <label htmlFor="agent-description" className="text-sm font-medium">
              {tc("description")}
            </label>
            <Textarea
              id="agent-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("descriptionPlaceholder")}
              rows={2}
              className="resize-none"
            />
          </div>

          {/* Execution Mode */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">{t("executionMode")}</label>
            <p className="text-xs text-muted-foreground">
              {t("executionModeDescription")}
            </p>
            <div className="grid grid-cols-3 gap-2">
              <button
                type="button"
                onClick={() => setExecutionMode("auto")}
                className={cn(
                  "flex flex-col items-start gap-0.5 rounded-md border p-3 text-left text-sm transition-colors",
                  executionMode === "auto"
                    ? "border-primary bg-primary/5"
                    : "border-input hover:border-muted-foreground/50"
                )}
              >
                <div className="flex items-center gap-1.5 font-medium">
                  <Sparkles className="h-3.5 w-3.5" />
                  {t("autoMode")}
                </div>
                <span className="text-xs text-muted-foreground">
                  {t("autoModeDescription")}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setExecutionMode("react")}
                className={cn(
                  "flex flex-col items-start gap-0.5 rounded-md border p-3 text-left text-sm transition-colors",
                  executionMode === "react"
                    ? "border-primary bg-primary/5"
                    : "border-input hover:border-muted-foreground/50"
                )}
              >
                <div className="flex items-center gap-1.5 font-medium">
                  <Zap className="h-3.5 w-3.5" />
                  {t("reactMode")}
                </div>
                <span className="text-xs text-muted-foreground">
                  {t("reactModeDescription")}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setExecutionMode("dag")}
                className={cn(
                  "flex flex-col items-start gap-0.5 rounded-md border p-3 text-left text-sm transition-colors",
                  executionMode === "dag"
                    ? "border-primary bg-primary/5"
                    : "border-input hover:border-muted-foreground/50"
                )}
              >
                <div className="flex items-center gap-1.5 font-medium">
                  <GitBranch className="h-3.5 w-3.5" />
                  {t("dagMode")}
                </div>
                <span className="text-xs text-muted-foreground">
                  {t("dagModeDescription")}
                </span>
              </button>
            </div>
          </div>

          {/* Model */}
          {systemModels.length > 0 && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">
                {executionMode === "dag" || executionMode === "auto" ? t("generalModel") : t("model")}
              </label>
              {(executionMode === "dag" || executionMode === "auto") && (
                <p className="text-xs text-muted-foreground">{t("generalModelDesc")}</p>
              )}
              <Select
                value={selectedModelConfigId}
                onValueChange={(v) => setSelectedModelConfigId(v === "__default__" ? "" : v)}
              >
                <SelectTrigger>
                  <SelectValue placeholder={t("useSystemDefault")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">
                    {t("useSystemDefault")}
                  </SelectItem>
                  {systemModels.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.name}
                      <span className="text-muted-foreground ml-1 text-xs">({m.model_name})</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Fast Model — only for DAG mode */}
          {systemModels.length > 0 && (executionMode === "dag" || executionMode === "auto") && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">{t("fastModel")}</label>
              <p className="text-xs text-muted-foreground">{t("fastModelDesc")}</p>
              <Select
                value={selectedFastModelConfigId}
                onValueChange={(v) => setSelectedFastModelConfigId(v === "__default__" ? "" : v)}
              >
                <SelectTrigger>
                  <SelectValue placeholder={t("useSystemDefault")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">
                    {t("useSystemDefault")}
                  </SelectItem>
                  {systemModels.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.name}
                      <span className="text-muted-foreground ml-1 text-xs">({m.model_name})</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Temperature */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium">{t("temperature")}</label>
              <span className="text-xs text-muted-foreground font-mono">
                {temperature != null ? temperature.toFixed(2) : t("temperatureDefault")}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("temperatureDescription")}
            </p>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={200}
                step={5}
                value={temperature != null ? Math.round(temperature * 100) : 70}
                onChange={(e) => {
                  setTemperature(parseInt(e.target.value) / 100)
                }}
                className="flex-1 h-1.5 accent-primary"
              />
              {temperature != null && (
                <button
                  type="button"
                  onClick={() => setTemperature(null)}
                  className="text-xs text-muted-foreground hover:text-foreground transition-colors shrink-0"
                >
                  {tc("reset")}
                </button>
              )}
            </div>
          </div>

          {/* Sandbox Config */}
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("sandboxResources")}</label>
            <p className="text-xs text-muted-foreground">
              {t("sandboxDescription")}
            </p>
            <div className="grid grid-cols-3 gap-2">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">{t("memory")}</label>
                <Select
                  value={sandboxMemory || "__default__"}
                  onValueChange={(v) => setSandboxMemory(v === "__default__" ? "" : v)}
                >
                  <SelectTrigger size="sm" className="w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__default__">{t("defaultMemory", { value: "256m" })}</SelectItem>
                    <SelectItem value="128m">128m</SelectItem>
                    <SelectItem value="256m">256m</SelectItem>
                    <SelectItem value="512m">512m</SelectItem>
                    <SelectItem value="1g">1g</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">{t("cpu")}</label>
                <Select
                  value={sandboxCpu || "__default__"}
                  onValueChange={(v) => setSandboxCpu(v === "__default__" ? "" : v)}
                >
                  <SelectTrigger size="sm" className="w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__default__">{t("defaultCpu", { value: "0.5" })}</SelectItem>
                    <SelectItem value="0.25">0.25</SelectItem>
                    <SelectItem value="0.5">0.5</SelectItem>
                    <SelectItem value="1">1.0</SelectItem>
                    <SelectItem value="2">2.0</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">{t("timeoutSeconds")}</label>
                <Input
                  type="number"
                  min={1}
                  max={600}
                  value={sandboxTimeout}
                  onChange={(e) => setSandboxTimeout(e.target.value)}
                  placeholder="120"
                  className="h-8 px-2 text-xs"
                />
              </div>
            </div>
          </div>

          {/* Instructions */}
          <div className="space-y-1.5">
            <label htmlFor="agent-instructions" className="text-sm font-medium">
              {t("instructionsLabel")}
            </label>
            <Textarea
              id="agent-instructions"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder={t("instructionsPlaceholder")}
              rows={5}
              className="resize-y"
            />
          </div>

          {/* Tool Categories */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">{t("builtInTools")}</label>
            <p className="text-xs text-muted-foreground">
              {t("builtInToolsHint")}
            </p>
            <TooltipProvider>
              <div className="flex flex-wrap gap-2">
                {TOOL_CATEGORIES.map((cat) => {
                  const keys = TOOL_CATEGORY_KEYS[cat]
                  const toolsInCategory = catalog?.tools
                    ?.filter((ct) => ct.category === cat)
                    .map((ct) => ct.display_name)
                    .join(", ")
                  const toolsLabel = toolsInCategory || t(keys.tools)
                  return (
                    <Tooltip key={cat}>
                      <TooltipTrigger asChild>
                        <label className="flex items-center gap-1.5 text-sm cursor-pointer select-none">
                          <input
                            type="checkbox"
                            checked={toolCategories.includes(cat)}
                            onChange={() => toggleCategory(cat)}
                            className="h-3.5 w-3.5 rounded border-input accent-primary"
                          />
                          <span className="text-muted-foreground">{t(keys.label)}</span>
                        </label>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="max-w-[200px] text-center">
                        <p className="font-medium mb-0.5">{t(keys.description)}</p>
                        <p className="text-[11px] opacity-75">{toolsLabel}</p>
                      </TooltipContent>
                    </Tooltip>
                  )
                })}
              </div>
            </TooltipProvider>
          </div>

          {/* Agent specialization hint */}
          <div className="rounded-md border border-blue-500/30 bg-blue-50 dark:bg-blue-950/20 px-4 py-3 flex items-start gap-3">
            <Info className="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" />
            <p className="text-xs text-blue-700 dark:text-blue-300 leading-relaxed">
              {t("specializationHint")}
            </p>
          </div>

          {/* Knowledge Bases */}
          {(availableKBs.length > 0 || (kbsLoaded && selectedKBs.some((id) => !availableKBs.some((kb) => kb.id === id)))) && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">{t("knowledgeBases")}</label>
              <p className="text-xs text-muted-foreground">
                {t("knowledgeBasesDescription")}
              </p>
              <div className="flex flex-col gap-1.5">
                {availableKBs.map((kb) => {
                  const isChecked = selectedKBs.includes(kb.id)
                  const toggleKB = () =>
                    setSelectedKBs((prev) =>
                      prev.includes(kb.id)
                        ? prev.filter((id) => id !== kb.id)
                        : [...prev, kb.id]
                    )
                  return (
                    <div
                      key={kb.id}
                      role="checkbox"
                      aria-checked={isChecked}
                      tabIndex={0}
                      onClick={toggleKB}
                      onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleKB() } }}
                      className="flex items-center gap-1.5 text-sm cursor-pointer select-none"
                    >
                      <div className={`h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors ${isChecked ? "bg-primary border-primary" : "border-input"}`}>
                        {isChecked && <Check className="h-2.5 w-2.5 text-primary-foreground" />}
                      </div>
                      <span className="text-muted-foreground">
                        {kb.name} ({t("kbDocCount", { count: kb.document_count })})
                      </span>
                    </div>
                  )
                })}
                {selectedKBs
                  .filter((id) => !availableKBs.some((kb) => kb.id === id))
                  .map((orphanId) => {
                    const toggleOrphan = () =>
                      setSelectedKBs((prev) => prev.filter((id) => id !== orphanId))
                    return (
                      <div
                        key={orphanId}
                        role="checkbox"
                        aria-checked={true}
                        tabIndex={0}
                        onClick={toggleOrphan}
                        onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleOrphan() } }}
                        className="flex items-center gap-1.5 text-sm cursor-pointer select-none rounded px-1 py-0.5 bg-destructive/10"
                      >
                        <div className="h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors bg-primary border-primary">
                          <Check className="h-2.5 w-2.5 text-primary-foreground" />
                        </div>
                        <span className="text-destructive/80 truncate max-w-[200px]" title={orphanId}>
                          {orphanId.length > 12 ? `${orphanId.slice(0, 12)}...` : orphanId}
                        </span>
                        <span className="text-destructive/60 text-xs">({t("deleted")})</span>
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          {/* Confidence Threshold - only show when KBs are selected */}
          {selectedKBs.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium">{t("confidenceThreshold")}</label>
                <span className="text-xs text-muted-foreground font-mono">
                  {confidenceThreshold != null
                    ? `${Math.round(confidenceThreshold * 100)}%`
                    : t("confidenceOff")}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">
                {t("confidenceDescription")}
              </p>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={
                    confidenceThreshold != null
                      ? Math.round(confidenceThreshold * 100)
                      : 0
                  }
                  onChange={(e) => {
                    const val = parseInt(e.target.value)
                    setConfidenceThreshold(val === 0 ? null : val / 100)
                  }}
                  className="flex-1 h-1.5 accent-primary"
                />
              </div>
            </div>
          )}

          {/* Connectors */}
          {(availableConnectors.length > 0 || (connectorsLoaded && selectedConnectors.some((id) => !availableConnectors.some((c) => c.id === id)))) && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">{t("connectors")}</label>
              <p className="text-xs text-muted-foreground">
                {t("connectorsDescription")}
              </p>
              <div className="flex flex-col gap-1.5">
                {availableConnectors.map((conn) => {
                  const isChecked = selectedConnectors.includes(conn.id)
                  const toggleConn = () =>
                    setSelectedConnectors((prev) =>
                      prev.includes(conn.id)
                        ? prev.filter((id) => id !== conn.id)
                        : [...prev, conn.id]
                    )
                  return (
                    <div
                      key={conn.id}
                      role="checkbox"
                      aria-checked={isChecked}
                      tabIndex={0}
                      onClick={toggleConn}
                      onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleConn() } }}
                      className="flex items-center gap-1.5 text-sm cursor-pointer select-none"
                    >
                      <div className={`h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors ${isChecked ? "bg-primary border-primary" : "border-input"}`}>
                        {isChecked && <Check className="h-2.5 w-2.5 text-primary-foreground" />}
                      </div>
                      <span className="text-muted-foreground">
                        {conn.name} ({conn.type === "database"
                          ? t("connectorTypeDatabase")
                          : `${t("connectorTypeApi")} · ${t("connectorActionCount", { count: conn.actions.length })}`})
                      </span>
                    </div>
                  )
                })}
                {selectedConnectors
                  .filter((id) => !availableConnectors.some((c) => c.id === id))
                  .map((orphanId) => {
                    const toggleOrphan = () =>
                      setSelectedConnectors((prev) => prev.filter((id) => id !== orphanId))
                    return (
                      <div
                        key={orphanId}
                        role="checkbox"
                        aria-checked={true}
                        tabIndex={0}
                        onClick={toggleOrphan}
                        onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleOrphan() } }}
                        className="flex items-center gap-1.5 text-sm cursor-pointer select-none rounded px-1 py-0.5 bg-destructive/10"
                      >
                        <div className="h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors bg-primary border-primary">
                          <Check className="h-2.5 w-2.5 text-primary-foreground" />
                        </div>
                        <span className="text-destructive/80 truncate max-w-[200px]" title={orphanId}>
                          {orphanId.length > 12 ? `${orphanId.slice(0, 12)}...` : orphanId}
                        </span>
                        <span className="text-destructive/60 text-xs">({t("deleted")})</span>
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          {/* MCP Servers */}
          {(availableMCPServers.length > 0 || (mcpServersLoaded && selectedMCPServers.some((id) => !availableMCPServers.some((s) => s.id === id)))) && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">{t("mcpServers")}</label>
              <p className="text-xs text-muted-foreground">
                {t("mcpServersDescription")}
              </p>
              <div className="flex flex-col gap-1.5">
                {availableMCPServers.map((server) => {
                  const isChecked = selectedMCPServers.includes(server.id)
                  const toggleServer = () =>
                    setSelectedMCPServers((prev) =>
                      prev.includes(server.id)
                        ? prev.filter((id) => id !== server.id)
                        : [...prev, server.id]
                    )
                  return (
                    <div
                      key={server.id}
                      role="checkbox"
                      aria-checked={isChecked}
                      tabIndex={0}
                      onClick={toggleServer}
                      onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleServer() } }}
                      className="flex items-center gap-1.5 text-sm cursor-pointer select-none"
                    >
                      <div className={`h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors ${isChecked ? "bg-primary border-primary" : "border-input"}`}>
                        {isChecked && <Check className="h-2.5 w-2.5 text-primary-foreground" />}
                      </div>
                      <span className="text-muted-foreground">
                        {server.name}
                        {server.description && (
                          <span className="text-xs ml-1 opacity-60">&mdash; {server.description}</span>
                        )}
                      </span>
                    </div>
                  )
                })}
                {selectedMCPServers
                  .filter((id) => !availableMCPServers.some((s) => s.id === id))
                  .map((orphanId) => {
                    const toggleOrphan = () =>
                      setSelectedMCPServers((prev) => prev.filter((id) => id !== orphanId))
                    return (
                      <div
                        key={orphanId}
                        role="checkbox"
                        aria-checked={true}
                        tabIndex={0}
                        onClick={toggleOrphan}
                        onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleOrphan() } }}
                        className="flex items-center gap-1.5 text-sm cursor-pointer select-none rounded px-1 py-0.5 bg-destructive/10"
                      >
                        <div className="h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors bg-primary border-primary">
                          <Check className="h-2.5 w-2.5 text-primary-foreground" />
                        </div>
                        <span className="text-destructive/80 truncate max-w-[200px]" title={orphanId}>
                          {orphanId.length > 12 ? `${orphanId.slice(0, 12)}...` : orphanId}
                        </span>
                        <span className="text-destructive/60 text-xs">({t("deleted")})</span>
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          {/* Completion Notification */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Bell className="h-4 w-4 text-muted-foreground" />
              <Label className="text-sm font-medium">{t("notifications.sectionTitle")}</Label>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("notifications.helperText")}
            </p>
            <div className="flex items-center justify-between gap-3 rounded-md border border-input px-3 py-2">
              <Label
                htmlFor="notify-on-complete"
                className="text-sm font-normal cursor-pointer"
              >
                {t("notifications.enabledLabel")}
              </Label>
              <Switch
                id="notify-on-complete"
                checked={notifyOnComplete}
                onCheckedChange={(checked) => {
                  setNotifyOnComplete(checked)
                  if (!checked) {
                    clearFieldError("notifyChannel")
                  }
                }}
              />
            </div>
            {notifyOnComplete && (
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">
                  {t("notifications.channelLabel")}
                </Label>
                {isLoadingChannels ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span>{t("notifications.loading")}</span>
                  </div>
                ) : !agent?.org_id ? (
                  <div className="rounded-md border border-dashed border-input px-3 py-2.5 space-y-1">
                    <p className="text-xs text-muted-foreground">
                      {t("notifications.personalAgentHint")}
                    </p>
                  </div>
                ) : availableChannels.length === 0 ? (
                  <div className="rounded-md border border-dashed border-input px-3 py-2.5 space-y-1">
                    <p className="text-xs text-muted-foreground">
                      {t("notifications.emptyState")}
                    </p>
                    <Button
                      type="button"
                      variant="link"
                      size="sm"
                      className="h-auto p-0 text-xs"
                      asChild
                    >
                      <Link href="/settings?tab=channels">
                        {t("notifications.emptyStateAction")}
                      </Link>
                    </Button>
                  </div>
                ) : (
                  <>
                    <Select
                      value={notifyChannelId || "__default__"}
                      onValueChange={(v) => {
                        setNotifyChannelId(v === "__default__" ? "" : v)
                        clearFieldError("notifyChannel")
                      }}
                    >
                      <SelectTrigger
                        className="w-full"
                        aria-invalid={!!fieldErrors.notifyChannel}
                      >
                        <SelectValue placeholder={t("notifications.channelPlaceholder")} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__default__">
                          {t("notifications.channelPlaceholder")}
                        </SelectItem>
                        {availableChannels.map((ch) => (
                          <SelectItem key={ch.id} value={ch.id}>
                            {ch.name}
                            <span className="text-muted-foreground ml-1 text-xs">
                              ({ch.type})
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {fieldErrors.notifyChannel && (
                      <p className="text-sm text-destructive">
                        {fieldErrors.notifyChannel}
                      </p>
                    )}
                  </>
                )}
              </div>
            )}
          </div>

          {/* Approval (human-in-the-loop routing) */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-muted-foreground" />
              <Label className="text-sm font-medium">{t("approval.sectionTitle")}</Label>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("approval.helperText")}
            </p>

            {/* Confirmation mode */}
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">
                {t("approval.modeLabel")}
              </Label>
              <Select
                value={confirmationMode}
                onValueChange={(v) => {
                  setConfirmationMode(v as "auto" | "inline_only" | "channel_only")
                  clearFieldError("approvalChannel")
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">{t("approval.modeAuto")}</SelectItem>
                  <SelectItem value="inline_only">{t("approval.modeInlineOnly")}</SelectItem>
                  <SelectItem value="channel_only">{t("approval.modeChannelOnly")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Approver scope */}
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">
                {t("approval.approverScopeLabel")}
              </Label>
              <Select
                value={confirmationApproverScope}
                onValueChange={(v) =>
                  setConfirmationApproverScope(
                    v as "initiator" | "agent_owner" | "org_members"
                  )
                }
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="initiator">{t("approval.approverInitiator")}</SelectItem>
                  <SelectItem value="agent_owner">{t("approval.approverAgentOwner")}</SelectItem>
                  <SelectItem value="org_members">{t("approval.approverOrgMembers")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Require-for-all switch */}
            <div className="flex items-center justify-between gap-3 rounded-md border border-input px-3 py-2">
              <div className="flex-1 space-y-0.5">
                <Label
                  htmlFor="require-confirmation-for-all"
                  className="text-sm font-normal cursor-pointer"
                >
                  {t("approval.requireAllLabel")}
                </Label>
                <p className="text-xs text-muted-foreground">
                  {t("approval.requireAllHelp")}
                </p>
              </div>
              <Switch
                id="require-confirmation-for-all"
                checked={requireConfirmationForAll}
                onCheckedChange={setRequireConfirmationForAll}
              />
            </div>

            {/* Approval channel (hidden when inline_only) */}
            {confirmationMode !== "inline_only" && (
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">
                  {t("approval.channelLabel")}
                </Label>
                {isLoadingChannels ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span>{t("notifications.loading")}</span>
                  </div>
                ) : !agent?.org_id ? (
                  <div className="rounded-md border border-dashed border-input px-3 py-2.5 space-y-1">
                    <p className="text-xs text-muted-foreground">
                      {t("approval.personalAgentHint")}
                    </p>
                  </div>
                ) : availableChannels.length === 0 ? (
                  <div className="rounded-md border border-dashed border-input px-3 py-2.5 space-y-1">
                    <p className="text-xs text-muted-foreground">
                      {t("approval.emptyChannels")}
                    </p>
                    <Button
                      type="button"
                      variant="link"
                      size="sm"
                      className="h-auto p-0 text-xs"
                      asChild
                    >
                      <Link href="/settings?tab=channels">
                        {t("approval.emptyChannelsAction")}
                      </Link>
                    </Button>
                  </div>
                ) : (
                  <Select
                    value={approvalChannelId || "__default__"}
                    onValueChange={(v) => {
                      setApprovalChannelId(v === "__default__" ? "" : v)
                      clearFieldError("approvalChannel")
                    }}
                  >
                    <SelectTrigger
                      className="w-full"
                      aria-invalid={!!fieldErrors.approvalChannel}
                    >
                      <SelectValue placeholder={t("approval.channelPlaceholder")} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__default__">
                        {t("approval.channelPlaceholder")}
                      </SelectItem>
                      {availableChannels.map((ch) => (
                        <SelectItem key={ch.id} value={ch.id}>
                          {ch.name}
                          <span className="text-muted-foreground ml-1 text-xs">
                            ({ch.type})
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
                <p className="text-xs text-muted-foreground">
                  {t("approval.channelHelp")}
                </p>
                {fieldErrors.approvalChannel && (
                  <p className="text-sm text-destructive">
                    {fieldErrors.approvalChannel}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Follow-up Suggestions */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-muted-foreground" />
              <Label className="text-sm font-medium">
                {t("suggestFollowups.sectionTitle")}
              </Label>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("suggestFollowups.helperText")}
            </p>
            <div className="flex items-center justify-between gap-3 rounded-md border border-input px-3 py-2">
              <Label
                htmlFor="suggest-followups"
                className="text-sm font-normal cursor-pointer"
              >
                {t("suggestFollowups.enabledLabel")}
              </Label>
              <Switch
                id="suggest-followups"
                checked={suggestFollowups}
                onCheckedChange={setSuggestFollowups}
              />
            </div>
          </div>

          {/* Compact Instructions */}
          <div className="space-y-2">
            <Label>{t("compactInstructions")}</Label>
            <p className="text-xs text-muted-foreground">{t("compactInstructionsHint")}</p>
            <Textarea
              value={compactInstructions}
              onChange={(e) => setCompactInstructions(e.target.value)}
              placeholder={t("compactInstructionsPlaceholder")}
              className="min-h-[80px] font-mono text-xs"
            />
          </div>

          {/* Suggested Prompts */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">
              {t("suggestedPrompts")}
            </label>
            <p className="text-xs text-muted-foreground">
              {t("suggestedPromptsDescription")}
            </p>
            <SuggestedPromptsEditor
              value={suggestedPrompts}
              onChange={setSuggestedPrompts}
            />
          </div>

        </div>
      </ScrollArea>

      {/* Save button outside scroll area */}
      <div className="flex justify-end pt-4">
        <Button type="submit" disabled={isSubmitting || !name.trim()}>
          {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
          {tc("save")}
        </Button>
      </div>
    </form>
  )
}
