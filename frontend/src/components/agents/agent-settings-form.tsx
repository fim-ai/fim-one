"use client"

import { useState, useEffect } from "react"
import { Bot, Check, Loader2, Zap, GitBranch } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { EmojiPickerPopover } from "@/components/ui/emoji-picker-popover"
import { SuggestedPromptsEditor } from "@/components/agents/suggested-prompts-editor"
import { agentApi, kbApi, connectorApi, modelApi } from "@/lib/api"
import type { AgentCreate, AgentResponse, SandboxConfig } from "@/types/agent"
import type { ConnectorResponse } from "@/types/connector"
import type { ModelConfigResponse } from "@/types/model_config"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"
import { useToolCatalog } from "@/hooks/use-tool-catalog"

// Ordered: general first → common tools → advanced/specialized
const TOOL_CATEGORIES = ["general", "web", "computation", "filesystem", "knowledge", "connector", "mcp"] as const

const TOOL_CATEGORY_META: Record<string, { label: string; description: string; tools: string }> = {
  connector: {
    label: "Connector",
    description: "Access external API actions bound to this agent",
    tools: "Custom HTTP connectors, CRM, Slack, and more",
  },
  knowledge: {
    label: "Knowledge",
    description: "Query knowledge bases for grounded answers with citations",
    tools: "KB Retrieve, Grounded Retrieve, KB List",
  },
  web: {
    label: "Web",
    description: "Browse the internet and search for information",
    tools: "Web Search, Web Fetch, HTTP Request",
  },
  computation: {
    label: "Computation",
    description: "Run math calculations and execute Python code",
    tools: "Calculator, Python Exec, Shell Exec",
  },
  filesystem: {
    label: "Filesystem",
    description: "Read, write, and manage local files",
    tools: "File Read, File Write, File List, and more",
  },
  mcp: {
    label: "MCP",
    description: "Tools provided by external MCP servers",
    tools: "Configured via MCP_SERVERS in environment",
  },
  general: {
    label: "General",
    description: "Miscellaneous and uncategorized built-in tools",
    tools: "Utility tools without a specific category",
  },
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
  const [name, setName] = useState("")
  const [icon, setIcon] = useState<string | null>(null)
  const [description, setDescription] = useState("")
  const [instructions, setInstructions] = useState("")
  const [toolCategories, setToolCategories] = useState<string[]>([])
  const [suggestedPrompts, setSuggestedPrompts] = useState<string[]>([])
  const [selectedKBs, setSelectedKBs] = useState<string[]>([])
  const [selectedConnectors, setSelectedConnectors] = useState<string[]>([])
  const [executionMode, setExecutionMode] = useState<"react" | "dag">("react")
  const [confidenceThreshold, setConfidenceThreshold] = useState<number | null>(null)
  const [temperature, setTemperature] = useState<number | null>(null)
  const [sandboxMemory, setSandboxMemory] = useState<string>("")
  const [sandboxCpu, setSandboxCpu] = useState<string>("")
  const [sandboxTimeout, setSandboxTimeout] = useState<string>("")
  const [selectedModelConfigId, setSelectedModelConfigId] = useState<string>("")
  const [systemModels, setSystemModels] = useState<ModelConfigResponse[]>([])

  const [availableKBs, setAvailableKBs] = useState<{ id: string; name: string; document_count: number }[]>([])
  const [availableConnectors, setAvailableConnectors] = useState<ConnectorResponse[]>([])
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
      setExecutionMode(agent.execution_mode || "react")
      const ct = agent.grounding_config?.confidence_threshold
      setConfidenceThreshold(typeof ct === "number" ? ct : null)
      const rawTemp = agent.model_config_json?.temperature
      setTemperature(typeof rawTemp === "number" ? rawTemp : null)
      setSandboxMemory(agent.sandbox_config?.memory ?? "")
      setSandboxCpu(agent.sandbox_config?.cpu != null ? String(agent.sandbox_config.cpu) : "")
      setSandboxTimeout(agent.sandbox_config?.timeout != null ? String(agent.sandbox_config.timeout) : "")
      setSelectedModelConfigId((agent.model_config_json?.model_config_id as string) ?? "")
    } else {
      setName("")
      setIcon(null)
      setDescription("")
      setInstructions("")
      setExecutionMode("react")
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
      executionMode !== (agent.execution_mode || "react") ||
      (() => {
        const ct = agent.grounding_config?.confidence_threshold
        const origCt = typeof ct === "number" ? ct : null
        return confidenceThreshold !== origCt
      })() ||
      temperature !== (agent.model_config_json?.temperature ?? null) ||
      sandboxMemory !== (agent.sandbox_config?.memory ?? "") ||
      sandboxCpu !== (agent.sandbox_config?.cpu != null ? String(agent.sandbox_config.cpu) : "") ||
      sandboxTimeout !== (agent.sandbox_config?.timeout != null ? String(agent.sandbox_config.timeout) : "") ||
      selectedModelConfigId !== ((agent.model_config_json?.model_config_id as string) ?? "")
    onDirtyChange(dirty)
  }, [agent, name, icon, description, instructions, executionMode, toolCategories, suggestedPrompts, selectedKBs, selectedConnectors, confidenceThreshold, temperature, sandboxMemory, sandboxCpu, sandboxTimeout, selectedModelConfigId, onDirtyChange])

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
      let modelConfigJson: Record<string, unknown> | undefined
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
      modelConfigJson = Object.keys(merged).length > 0 ? merged : undefined

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
        ...(selectedKBs.length > 0 && confidenceThreshold != null && {
          grounding_config: { confidence_threshold: confidenceThreshold },
        }),
        ...(modelConfigJson !== undefined && { model_config_json: modelConfigJson }),
        ...(hasSandboxConfig && { sandbox_config: sandboxCfg }),
      }

      let result: AgentResponse
      if (agent) {
        result = await agentApi.update(agent.id, data)
      } else {
        result = await agentApi.create(data)
      }

      onSaved(result)
      toast.success(agent ? "Agent updated" : "Agent created")
    } catch (err) {
      console.error("Failed to save agent:", err)
      const message = err instanceof Error ? err.message : "Unknown error"
      toast.error(`Failed to save agent: ${message}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  const inputClass =
    "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"

  return (
    <form onSubmit={handleSubmit} className="flex flex-col h-full overflow-hidden">
      <ScrollArea className="flex-1 overflow-hidden">
        <div className="space-y-4 pr-4">
          {/* Name + Icon */}
          <div className="space-y-1.5">
            <label htmlFor="agent-name" className="text-sm font-medium">
              Name <span className="text-destructive">*</span>
            </label>
            <div className="flex items-center gap-2">
              <EmojiPickerPopover
                value={icon}
                onChange={setIcon}
                fallbackIcon={<Bot className="h-5 w-5" />}
              />
              <input
                id="agent-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My Agent"
                required
                className={inputClass}
              />
            </div>
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <label htmlFor="agent-description" className="text-sm font-medium">
              Description
            </label>
            <textarea
              id="agent-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="A brief description of what this agent does..."
              rows={2}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none"
            />
          </div>

          {/* Execution Mode */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Execution Mode</label>
            <p className="text-xs text-muted-foreground">
              Sets the default mode for new conversations. You can still switch modes anytime during a chat.
            </p>
            <div className="grid grid-cols-2 gap-2">
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
                  Standard (ReAct)
                </div>
                <span className="text-xs text-muted-foreground">
                  Quick response, flexible handling
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
                  Planner (DAG)
                </div>
                <span className="text-xs text-muted-foreground">
                  Systematic breakdown, step by step
                </span>
              </button>
            </div>
          </div>

          {/* Model */}
          {systemModels.length > 0 && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Model</label>
              <Select
                value={selectedModelConfigId}
                onValueChange={(v) => setSelectedModelConfigId(v === "__default__" ? "" : v)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Use system default" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">
                    Use system default
                    {systemModels.find((m) => m.is_default) && (
                      <span className="text-muted-foreground ml-1">
                        ({systemModels.find((m) => m.is_default)!.name})
                      </span>
                    )}
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
              <label className="text-sm font-medium">Temperature</label>
              <span className="text-xs text-muted-foreground font-mono">
                {temperature != null ? temperature.toFixed(2) : "Default"}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              Controls randomness. Lower = focused, higher = creative. Leave as Default to use the server setting.
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
                  Reset
                </button>
              )}
            </div>
          </div>

          {/* Sandbox Config */}
          <div className="space-y-2">
            <label className="text-sm font-medium">Sandbox Resources</label>
            <p className="text-xs text-muted-foreground">
              Resource limits for code execution tools. Only applies when using Docker backend (CODE_EXEC_BACKEND=docker).
            </p>
            <div className="grid grid-cols-3 gap-2">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Memory</label>
                <select
                  value={sandboxMemory}
                  onChange={(e) => setSandboxMemory(e.target.value)}
                  className="flex h-8 w-full rounded-md border border-input bg-transparent px-2 py-1 text-xs shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  <option value="">Default (256m)</option>
                  <option value="128m">128m</option>
                  <option value="256m">256m</option>
                  <option value="512m">512m</option>
                  <option value="1g">1g</option>
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">CPU</label>
                <select
                  value={sandboxCpu}
                  onChange={(e) => setSandboxCpu(e.target.value)}
                  className="flex h-8 w-full rounded-md border border-input bg-transparent px-2 py-1 text-xs shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  <option value="">Default (0.5)</option>
                  <option value="0.25">0.25</option>
                  <option value="0.5">0.5</option>
                  <option value="1">1.0</option>
                  <option value="2">2.0</option>
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Timeout (s)</label>
                <input
                  type="number"
                  min={1}
                  max={600}
                  value={sandboxTimeout}
                  onChange={(e) => setSandboxTimeout(e.target.value)}
                  placeholder="120"
                  className="flex h-8 w-full rounded-md border border-input bg-transparent px-2 py-1 text-xs shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                />
              </div>
            </div>
          </div>

          {/* Instructions */}
          <div className="space-y-1.5">
            <label htmlFor="agent-instructions" className="text-sm font-medium">
              Instructions
            </label>
            <textarea
              id="agent-instructions"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="System prompt for the agent. Tell it how to behave, what to focus on..."
              rows={5}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-y"
            />
          </div>

          {/* Tool Categories */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Tool Categories</label>
            <p className="text-xs text-muted-foreground">
              Hover over a category to see what tools it includes.
            </p>
            <TooltipProvider>
              <div className="flex flex-wrap gap-2">
                {TOOL_CATEGORIES.map((cat) => {
                  const meta = TOOL_CATEGORY_META[cat]
                  const toolsInCategory = catalog?.tools
                    ?.filter((t) => t.category === cat)
                    .map((t) => t.display_name)
                    .join(", ")
                  const toolsLabel = toolsInCategory || meta.tools
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
                          <span className="text-muted-foreground">{meta.label}</span>
                        </label>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="max-w-[200px] text-center">
                        <p className="font-medium mb-0.5">{meta.description}</p>
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
              <label className="text-sm font-medium">Knowledge Bases</label>
              <p className="text-xs text-muted-foreground">
                Bind KBs to enable evidence-grounded retrieval with citations
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
                        {kb.name} ({kb.document_count} docs)
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
                        <span className="text-destructive/60 text-xs">(deleted)</span>
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
                <label className="text-sm font-medium">Confidence Threshold</label>
                <span className="text-xs text-muted-foreground font-mono">
                  {confidenceThreshold != null
                    ? `${Math.round(confidenceThreshold * 100)}%`
                    : "Off"}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">
                When set, answers with confidence below this threshold will be
                rejected. Leave off to always show results.
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
              <label className="text-sm font-medium">Connectors</label>
              <p className="text-xs text-muted-foreground">
                Bind connectors to give the agent access to external API actions
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
                        {conn.name} ({conn.actions.length} actions)
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
                        <span className="text-destructive/60 text-xs">(deleted)</span>
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          {/* Suggested Prompts */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">
              Suggested Prompts
            </label>
            <p className="text-xs text-muted-foreground">
              Shown as quick-start suggestions in chat.
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
          Save
        </Button>
      </div>
    </form>
  )
}
