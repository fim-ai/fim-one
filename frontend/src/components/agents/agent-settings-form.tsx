"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Bot, Check, Loader2, Zap, GitBranch, Sparkles, Radar } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { EmojiPickerPopover } from "@/components/ui/emoji-picker-popover"
import { SuggestedPromptsEditor } from "@/components/agents/suggested-prompts-editor"
import { agentApi, kbApi, connectorApi, modelApi, skillApi } from "@/lib/api"
import type { AgentCreate, AgentResponse, SandboxConfig } from "@/types/agent"
import type { ConnectorResponse } from "@/types/connector"
import type { SkillResponse } from "@/types/skill"
import type { ModelConfigResponse } from "@/types/model_config"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { useToolCatalog } from "@/hooks/use-tool-catalog"

// Ordered: general first → common tools → advanced/specialized
const TOOL_CATEGORIES = ["general", "web", "computation", "filesystem", "knowledge", "connector", "mcp"] as const

// Tool category i18n key mapping
const TOOL_CATEGORY_KEYS: Record<string, { label: string; description: string; tools: string }> = {
  connector: { label: "toolCategoryConnector", description: "toolCategoryConnectorDesc", tools: "toolCategoryConnectorTools" },
  knowledge: { label: "toolCategoryKnowledge", description: "toolCategoryKnowledgeDesc", tools: "toolCategoryKnowledgeTools" },
  web: { label: "toolCategoryWeb", description: "toolCategoryWebDesc", tools: "toolCategoryWebTools" },
  computation: { label: "toolCategoryComputation", description: "toolCategoryComputationDesc", tools: "toolCategoryComputationTools" },
  filesystem: { label: "toolCategoryFilesystem", description: "toolCategoryFilesystemDesc", tools: "toolCategoryFilesystemTools" },
  mcp: { label: "toolCategoryMcp", description: "toolCategoryMcpDesc", tools: "toolCategoryMcpTools" },
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
  const [executionMode, setExecutionMode] = useState<"react" | "dag" | "auto">("auto")
  const [confidenceThreshold, setConfidenceThreshold] = useState<number | null>(null)
  const [temperature, setTemperature] = useState<number | null>(null)
  const [sandboxMemory, setSandboxMemory] = useState<string>("")
  const [sandboxCpu, setSandboxCpu] = useState<string>("")
  const [sandboxTimeout, setSandboxTimeout] = useState<string>("")
  const [selectedModelConfigId, setSelectedModelConfigId] = useState<string>("")
  const [selectedFastModelConfigId, setSelectedFastModelConfigId] = useState<string>("")
  const [discoverable, setDiscoverable] = useState<boolean>(false)
  const [selectedSkills, setSelectedSkills] = useState<string[]>([])
  const [compactInstructions, setCompactInstructions] = useState<string>("")
  const [systemModels, setSystemModels] = useState<ModelConfigResponse[]>([])

  const [availableKBs, setAvailableKBs] = useState<{ id: string; name: string; document_count: number }[]>([])
  const [availableConnectors, setAvailableConnectors] = useState<ConnectorResponse[]>([])
  const [availableSkills, setAvailableSkills] = useState<SkillResponse[]>([])
  const [isSubmitting, setIsSubmitting] = useState(false)
  const { data: catalog } = useToolCatalog()

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
      setDiscoverable(agent.discoverable ?? false)
      setSelectedSkills(agent.skill_ids || [])
      setCompactInstructions(agent.compact_instructions || "")
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
      setConfidenceThreshold(null)
      setTemperature(null)
      setSandboxMemory("")
      setSandboxCpu("")
      setSandboxTimeout("")
      setSelectedModelConfigId("")
      setSelectedFastModelConfigId("")
      setDiscoverable(false)
      setSelectedSkills([])
      setCompactInstructions("")
    }
  }, [agent])

  // Load available KBs/connectors on mount
  useEffect(() => {
    kbApi
      .list(1, 100)
      .then((d) => setAvailableKBs(d.items || []))
      .catch(() => setAvailableKBs([]))
    connectorApi
      .list(1, 100)
      .then((d) => setAvailableConnectors(d.items || []))
      .catch(() => setAvailableConnectors([]))
    modelApi.list("llm").then(setSystemModels).catch(() => {})
    skillApi
      .list(1, 100)
      .then((d) => setAvailableSkills((d.items || []) as SkillResponse[]))
      .catch(() => setAvailableSkills([]))
  }, [])

  // Compute and notify dirty state
  useEffect(() => {
    if (!onDirtyChange) return
    if (!agent) {
      // Create mode: dirty if user typed anything
      onDirtyChange(name.trim() !== "")
      return
    }
    const dirty =
      name !== agent.name ||
      icon !== (agent.icon || null) ||
      description !== (agent.description || "") ||
      instructions !== (agent.instructions || "") ||
      JSON.stringify(toolCategories) !== JSON.stringify(agent.tool_categories || []) ||
      JSON.stringify(suggestedPrompts) !== JSON.stringify(agent.suggested_prompts || []) ||
      JSON.stringify(selectedKBs) !== JSON.stringify(agent.kb_ids || []) ||
      JSON.stringify(selectedConnectors) !== JSON.stringify(agent.connector_ids || []) ||
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
      discoverable !== (agent.discoverable ?? false) ||
      JSON.stringify(selectedSkills) !== JSON.stringify(agent.skill_ids || []) ||
      compactInstructions !== (agent.compact_instructions || "")
    onDirtyChange(dirty)
  }, [agent, name, icon, description, instructions, executionMode, toolCategories, suggestedPrompts, selectedKBs, selectedConnectors, confidenceThreshold, temperature, sandboxMemory, sandboxCpu, sandboxTimeout, selectedModelConfigId, selectedFastModelConfigId, discoverable, selectedSkills, compactInstructions, onDirtyChange])

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
      const modelConfigJson = Object.keys(merged).length > 0 ? merged : undefined

      // Build sandbox_config: only include fields that have been set
      const sandboxCfg: SandboxConfig = {}
      if (sandboxMemory) sandboxCfg.memory = sandboxMemory
      if (sandboxCpu) sandboxCfg.cpu = parseFloat(sandboxCpu)
      if (sandboxTimeout) sandboxCfg.timeout = parseInt(sandboxTimeout, 10)
      const hasSandboxConfig = Object.keys(sandboxCfg).length > 0

      const data: AgentCreate & { discoverable?: boolean } = {
        name: trimmedName,
        icon: icon || null,
        description: description.trim() || null,
        instructions: instructions.trim() || null,
        execution_mode: executionMode,
        tool_categories: toolCategories,
        ...(prompts.length > 0 && { suggested_prompts: prompts }),
        kb_ids: selectedKBs,
        connector_ids: selectedConnectors,
        ...(selectedKBs.length > 0 && confidenceThreshold != null && {
          grounding_config: { confidence_threshold: confidenceThreshold },
        }),
        ...(modelConfigJson !== undefined && { model_config_json: modelConfigJson }),
        ...(hasSandboxConfig && { sandbox_config: sandboxCfg }),
        discoverable,
        skill_ids: selectedSkills,
        compact_instructions: compactInstructions.trim() || null,
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
            <label className="text-sm font-medium">{t("toolCategories")}</label>
            <p className="text-xs text-muted-foreground">
              {t("toolCategoriesHint")}
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

          {/* Knowledge Bases */}
          {(availableKBs.length > 0 || selectedKBs.some((id) => !availableKBs.some((kb) => kb.id === id))) && (
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
          {(availableConnectors.length > 0 || selectedConnectors.some((id) => !availableConnectors.some((c) => c.id === id))) && (
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

          {/* Bound Skills */}
          {(availableSkills.length > 0 || selectedSkills.some((id) => !availableSkills.some((s) => s.id === id))) && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">{t("skillIds")}</label>
              <p className="text-xs text-muted-foreground">
                {t("skillIdsHint")}
              </p>
              <div className="flex flex-col gap-1.5">
                {availableSkills.map((sk) => {
                  const isChecked = selectedSkills.includes(sk.id)
                  const toggleSkill = () =>
                    setSelectedSkills((prev) =>
                      prev.includes(sk.id)
                        ? prev.filter((sid) => sid !== sk.id)
                        : [...prev, sk.id]
                    )
                  return (
                    <div
                      key={sk.id}
                      role="checkbox"
                      aria-checked={isChecked}
                      tabIndex={0}
                      onClick={toggleSkill}
                      onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleSkill() } }}
                      className="flex items-center gap-1.5 text-sm cursor-pointer select-none"
                    >
                      <div className={`h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors ${isChecked ? "bg-primary border-primary" : "border-input"}`}>
                        {isChecked && <Check className="h-2.5 w-2.5 text-primary-foreground" />}
                      </div>
                      <span className="text-muted-foreground">
                        {sk.name}
                      </span>
                    </div>
                  )
                })}
                {selectedSkills
                  .filter((id) => !availableSkills.some((s) => s.id === id))
                  .map((orphanId) => {
                    const toggleOrphan = () =>
                      setSelectedSkills((prev) => prev.filter((sid) => sid !== orphanId))
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

          {/* Discoverable toggle */}
          <div className="flex items-center justify-between rounded-md border border-border px-3 py-2.5">
            <div className="flex items-center gap-2">
              <Radar className="h-4 w-4 text-muted-foreground shrink-0" />
              <div>
                <Label htmlFor="agent-discoverable" className="text-sm font-medium cursor-pointer">
                  {t("discoverable")}
                </Label>
                <p className="text-xs text-muted-foreground">{t("discoverableHelp")}</p>
              </div>
            </div>
            <Switch
              id="agent-discoverable"
              checked={discoverable}
              onCheckedChange={setDiscoverable}
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
