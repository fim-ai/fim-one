"use client"

import { useState, useCallback, useMemo } from "react"
import { useTranslations } from "next-intl"
import { ChevronDown, Plus, Trash2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import { Separator } from "@/components/ui/separator"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { VariablePicker } from "./variable-picker"
import type { Node } from "@xyflow/react"
import type { WorkflowNodeType, ErrorStrategy } from "@/types/workflow"

interface NodeConfigPanelProps {
  node: Node | null
  allNodes: Node[]
  onUpdate: (nodeId: string, data: Record<string, unknown>) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}

export function NodeConfigPanel({ node, allNodes, onUpdate, onDelete, onClose }: NodeConfigPanelProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")

  const updateField = useCallback(
    (field: string, value: unknown) => {
      if (!node) return
      onUpdate(node.id, { ...node.data, [field]: value })
    },
    [node, onUpdate],
  )

  // All nodes except the currently selected one — variable sources
  const otherNodes = useMemo(
    () => (node ? allNodes.filter((n) => n.id !== node.id) : []),
    [allNodes, node],
  )

  if (!node) {
    return (
      <div className="flex flex-col h-full border-l border-border/40 bg-background w-[300px]">
        <div className="flex items-center justify-center h-full">
          <p className="text-xs text-muted-foreground">{t("configNoSelection")}</p>
        </div>
      </div>
    )
  }

  const nodeType = node.type as WorkflowNodeType

  return (
    <div className="flex flex-col h-full border-l border-border/40 bg-background w-[300px]">
      <div className="flex items-center justify-between px-3 pt-3 pb-2 shrink-0 border-b border-border/40">
        <h3 className="text-xs font-semibold text-foreground">
          {t("configTitle")}
        </h3>
        <Button variant="ghost" size="icon-sm" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <ScrollArea className="flex-1 min-h-0">
        <div className="p-3 space-y-4">
          <p className="text-sm font-medium text-foreground">
            {t(`nodeType_${nodeType}` as Parameters<typeof t>[0])}
          </p>

          {/* Note annotation — available for all node types */}
          <div className="space-y-1">
            <label className="text-[11px] font-medium text-muted-foreground">
              {t("configNote")}
            </label>
            <Textarea
              value={(node.data.note as string) ?? ""}
              onChange={(e) => updateField("note", e.target.value || undefined)}
              placeholder={t("configNotePlaceholder")}
              className="min-h-[32px] h-auto text-xs resize-none"
              rows={1}
              onInput={(e) => {
                const target = e.target as HTMLTextAreaElement
                target.style.height = "auto"
                target.style.height = `${target.scrollHeight}px`
              }}
            />
          </div>

          <NodeConfigFields
            nodeType={nodeType}
            data={node.data as Record<string, unknown>}
            updateField={updateField}
            otherNodes={otherNodes}
          />

          {/* Advanced section — error strategy + timeout (not for Start/End) */}
          {nodeType !== "start" && nodeType !== "end" && (
            <AdvancedSection
              errorStrategy={(node.data.error_strategy as ErrorStrategy) ?? "stop_workflow"}
              timeoutMs={(node.data.timeout_ms as number) ?? 30000}
              onChangeErrorStrategy={(v) => updateField("error_strategy", v)}
              onChangeTimeout={(v) => updateField("timeout_ms", v)}
            />
          )}

          {/* Delete node button — disabled for start/end nodes */}
          {nodeType !== "start" && nodeType !== "end" && (
            <>
              <Separator className="my-1" />
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    variant="destructive"
                    size="sm"
                    className="w-full gap-1.5"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {t("deleteNode")}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent size="sm">
                  <AlertDialogHeader>
                    <AlertDialogTitle>{t("deleteNode")}</AlertDialogTitle>
                    <AlertDialogDescription>
                      {t("deleteNodeConfirm")}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
                    <AlertDialogAction
                      variant="destructive"
                      onClick={() => onDelete(node.id)}
                    >
                      {tc("delete")}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

// --- Advanced Section (error strategy + timeout) ---

const ERROR_STRATEGIES: ErrorStrategy[] = ["stop_workflow", "continue", "fail_branch"]

function AdvancedSection({
  errorStrategy,
  timeoutMs,
  onChangeErrorStrategy,
  onChangeTimeout,
}: {
  errorStrategy: ErrorStrategy
  timeoutMs: number
  onChangeErrorStrategy: (v: ErrorStrategy) => void
  onChangeTimeout: (v: number) => void
}) {
  const t = useTranslations("workflows")
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <Separator className="my-1" />
      <button
        type="button"
        className="flex items-center justify-between w-full text-xs font-medium text-muted-foreground hover:text-foreground transition-colors py-1"
        onClick={() => setExpanded((v) => !v)}
      >
        {t("configSectionAdvanced")}
        <ChevronDown
          className={cn(
            "h-3 w-3 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded && (
        <div className="space-y-3 pb-1">
          {/* Error strategy */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t("configErrorStrategy")}</label>
            <Select
              value={errorStrategy}
              onValueChange={(v) => onChangeErrorStrategy(v as ErrorStrategy)}
            >
              <SelectTrigger className="w-full h-7 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ERROR_STRATEGIES.map((s) => (
                  <SelectItem key={s} value={s} className="text-xs">
                    {t(`configErrorStrategy_${s}` as Parameters<typeof t>[0])}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[10px] text-muted-foreground">
              {t(`configErrorStrategyHint_${errorStrategy}` as Parameters<typeof t>[0])}
            </p>
          </div>
          {/* Timeout */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t("configTimeout")}</label>
            <Input
              type="number"
              className="h-7 text-xs"
              value={timeoutMs}
              min={1000}
              max={600000}
              step={1000}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10)
                if (!isNaN(v) && v > 0) onChangeTimeout(v)
              }}
            />
            <p className="text-[10px] text-muted-foreground">
              {t("configTimeoutHint")}
            </p>
          </div>
        </div>
      )}
    </>
  )
}

// --- Per-node config fields ---

interface NodeConfigFieldsProps {
  nodeType: WorkflowNodeType
  data: Record<string, unknown>
  updateField: (field: string, value: unknown) => void
  otherNodes: Node[]
}

function NodeConfigFields({ nodeType, data, updateField, otherNodes }: NodeConfigFieldsProps) {
  const t = useTranslations("workflows")

  switch (nodeType) {
    case "start":
      return <StartConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "end":
      return <EndConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "llm":
      return <LLMConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "conditionBranch":
      return <ConditionConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "questionClassifier":
      return <QuestionClassifierConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "agent":
      return <AgentConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "knowledgeRetrieval":
      return <KnowledgeRetrievalConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "connector":
      return <ConnectorConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "httpRequest":
      return <HTTPRequestConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "variableAssign":
      return <VariableAssignConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "templateTransform":
      return <TemplateTransformConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "codeExecution":
      return <CodeExecutionConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "iterator":
      return <IteratorConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "loop":
      return <LoopConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "variableAggregator":
      return <VariableAggregatorConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "parameterExtractor":
      return <ParameterExtractorConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "listOperation":
      return <ListOperationConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "transform":
      return <TransformConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "documentExtractor":
      return <DocumentExtractorConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "questionUnderstanding":
      return <QuestionUnderstandingConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "humanIntervention":
      return <HumanInterventionConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "mcp":
      return <MCPConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "builtinTool":
      return <BuiltinToolConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    default:
      return <p className="text-xs text-muted-foreground">No configuration available</p>
  }
}

type ConfigProps = {
  data: Record<string, unknown>
  updateField: (field: string, value: unknown) => void
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: any
  otherNodes: Node[]
}

/** Reusable section header */
function SectionHeader({ label }: { label: string }) {
  return (
    <>
      <Separator className="my-1" />
      <p className="text-[10px] font-semibold text-muted-foreground/70 uppercase tracking-wider">
        {label}
      </p>
    </>
  )
}

/** Reusable output variable field */
function OutputVariableField({ data, updateField, t }: ConfigProps) {
  return (
    <>
      <SectionHeader label={t("configSectionOutput")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="result"
          value={(data.output_variable ?? "") as string}
          onChange={(e) => updateField("output_variable", e.target.value)}
        />
        <p className="text-[10px] text-muted-foreground/60">
          {t("configVariableRefHint")}
        </p>
      </div>
    </>
  )
}

/** Variable insert bar with picker and hint text */
function InsertVariableBar({
  otherNodes,
  onInsert,
  t,
}: {
  otherNodes: Node[]
  onInsert: (reference: string) => void
  t: ConfigProps["t"]
}) {
  return (
    <div className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
      <VariablePicker sourceNodes={otherNodes} onInsert={onInsert} />
      <span>{t("configVariableRefHint")}</span>
    </div>
  )
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function StartConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const variables = (data.variables ?? []) as Array<{ name: string; type: string; default_value?: string; required?: boolean }>

  const addVariable = () => {
    updateField("variables", [...variables, { name: "", type: "string", required: false }])
  }

  const removeVariable = (idx: number) => {
    updateField("variables", variables.filter((_, i) => i !== idx))
  }

  const updateVariable = (idx: number, field: string, value: unknown) => {
    const updated = variables.map((v, i) => (i === idx ? { ...v, [field]: value } : v))
    updateField("variables", updated)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configVariables")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addVariable}>
          <Plus className="h-3 w-3" />
          {t("configAddVariable")}
        </Button>
      </div>

      {/* Compact table header */}
      {variables.length > 0 && (
        <div className="grid grid-cols-[1fr_72px_28px] gap-1 px-0.5">
          <span className="text-[10px] font-medium text-muted-foreground/70 uppercase tracking-wider">
            {t("configStartName")}
          </span>
          <span className="text-[10px] font-medium text-muted-foreground/70 uppercase tracking-wider">
            {t("configStartType")}
          </span>
          <span />
        </div>
      )}

      {/* Compact inline rows */}
      {variables.map((v, i) => (
        <div key={i} className="space-y-1.5 rounded-md border border-border p-2">
          {/* Row 1: Name + Type + Remove */}
          <div className="grid grid-cols-[1fr_72px_28px] gap-1 items-center">
            <Input
              className="h-7 text-xs"
              placeholder={t("configVariableName")}
              value={v.name}
              onChange={(e) => updateVariable(i, "name", e.target.value)}
            />
            <Select value={v.type} onValueChange={(val) => updateVariable(i, "type", val)}>
              <SelectTrigger className="w-full h-7 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="string">String</SelectItem>
                <SelectItem value="number">Number</SelectItem>
                <SelectItem value="boolean">Boolean</SelectItem>
                <SelectItem value="object">Object</SelectItem>
                <SelectItem value="array">Array</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="ghost" size="icon-sm" className="h-7 w-7" onClick={() => removeVariable(i)}>
              <Trash2 className="h-3 w-3 text-destructive" />
            </Button>
          </div>
          {/* Row 2: Default + Required toggle */}
          <div className="flex items-center gap-2">
            <Input
              className="h-7 text-xs flex-1"
              placeholder={t("configStartDefault")}
              value={v.default_value ?? ""}
              onChange={(e) => updateVariable(i, "default_value", e.target.value)}
            />
            <div className="flex items-center gap-1.5 shrink-0">
              <label className="text-[10px] text-muted-foreground">{t("configStartRequired")}</label>
              <Switch
                checked={v.required ?? false}
                onCheckedChange={(checked) => updateVariable(i, "required", checked)}
              />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function EndConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const mapping = (data.output_mapping ?? {}) as Record<string, string>
  const entries = Object.entries(mapping)

  const addMapping = () => {
    updateField("output_mapping", { ...mapping, "": "" })
  }

  const removeMapping = (key: string) => {
    const updated = { ...mapping }
    delete updated[key]
    updateField("output_mapping", updated)
  }

  const updateMapping = (oldKey: string, newKey: string, value: string) => {
    const updated: Record<string, string> = {}
    for (const [k, v] of Object.entries(mapping)) {
      if (k === oldKey) {
        updated[newKey] = value
      } else {
        updated[k] = v
      }
    }
    updateField("output_mapping", updated)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configOutputMapping")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addMapping}>
          <Plus className="h-3 w-3" />
          {t("configAddMapping")}
        </Button>
      </div>
      <p className="text-[10px] text-muted-foreground/60">
        {t("configOutputMappingHint")}
      </p>
      {entries.map(([key, value], i) => (
        <div key={i} className="space-y-1 rounded-md border border-border p-2">
          <div className="flex items-center gap-2">
            <Input
              className="h-7 text-xs flex-1"
              placeholder={t("configKey")}
              value={key}
              onChange={(e) => updateMapping(key, e.target.value, value)}
            />
            <Button variant="ghost" size="icon-sm" className="h-7 w-7" onClick={() => removeMapping(key)}>
              <Trash2 className="h-3 w-3 text-destructive" />
            </Button>
          </div>
          <div className="flex items-center gap-1">
            <Input
              className="h-7 text-xs flex-1 font-mono"
              placeholder={t("configValue")}
              value={value}
              onChange={(e) => updateMapping(key, key, e.target.value)}
            />
            <VariablePicker
              sourceNodes={otherNodes}
              onInsert={(ref) => updateMapping(key, key, value + ref)}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

function LLMConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const modelTier = (data.model_tier ?? "main") as string

  return (
    <div className="space-y-3">
      {/* Model section */}
      <SectionHeader label={t("configSectionModel")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configModel")}</label>
        <Select
          value={(data.model ?? "__default__") as string}
          onValueChange={(v) => updateField("model", v === "__default__" ? "" : v)}
        >
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue placeholder={t("configModelPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__default__">{t("configModelPlaceholder")}</SelectItem>
            <SelectItem value="gpt-4o">GPT-4o</SelectItem>
            <SelectItem value="gpt-4o-mini">GPT-4o Mini</SelectItem>
            <SelectItem value="gpt-4-turbo">GPT-4 Turbo</SelectItem>
            <SelectItem value="claude-sonnet-4-20250514">Claude Sonnet 4</SelectItem>
            <SelectItem value="claude-3-5-haiku-20241022">Claude 3.5 Haiku</SelectItem>
            <SelectItem value="deepseek-chat">DeepSeek Chat</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Model Tier toggle */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configModelTier")}</label>
        <div className="flex rounded-md border border-border overflow-hidden">
          <button
            type="button"
            className={`flex-1 h-7 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${
              modelTier === "fast"
                ? "bg-primary text-primary-foreground"
                : "bg-background text-muted-foreground hover:bg-muted"
            }`}
            onClick={() => updateField("model_tier", "fast")}
          >
            {t("configModelTierFast")}
          </button>
          <button
            type="button"
            className={`flex-1 h-7 text-xs font-medium transition-colors border-l border-border focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${
              modelTier === "main"
                ? "bg-primary text-primary-foreground"
                : "bg-background text-muted-foreground hover:bg-muted"
            }`}
            onClick={() => updateField("model_tier", "main")}
          >
            {t("configModelTierMain")}
          </button>
        </div>
        <p className="text-[10px] text-muted-foreground/60">
          {t("configModelTierHint")}
        </p>
      </div>

      {/* Prompt section */}
      <SectionHeader label={t("configSectionPrompt")} />

      {/* System Prompt */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSystemPrompt")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configSystemPromptHint")}
          value={(data.system_prompt ?? "") as string}
          onChange={(e) => updateField("system_prompt", e.target.value)}
        />
      </div>

      {/* User Prompt template */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium">{t("configPromptTemplate")}</label>
        </div>
        <Textarea
          className="text-xs resize-none"
          rows={5}
          placeholder={t("configPromptHint")}
          value={(data.prompt_template ?? "") as string}
          onChange={(e) => updateField("prompt_template", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          t={t}
          onInsert={(ref) => {
            const current = ((data.prompt_template ?? "") as string)
            updateField("prompt_template", current + ref)
          }}
        />
      </div>

      {/* Parameters section */}
      <SectionHeader label={t("configSectionParameters")} />
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium">{t("configTemperature")}</label>
          <span className="text-[10px] font-mono text-muted-foreground tabular-nums">
            {((data.temperature ?? 0.7) as number).toFixed(1)}
          </span>
        </div>
        <Slider
          value={[(data.temperature ?? 0.7) as number]}
          onValueChange={([v]) => updateField("temperature", v)}
          min={0}
          max={2}
          step={0.1}
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configMaxTokens")}</label>
        <Input
          className="h-7 text-xs"
          type="number"
          placeholder="4096"
          value={(data.max_tokens ?? "") as string}
          onChange={(e) => updateField("max_tokens", e.target.value ? Number(e.target.value) : undefined)}
        />
      </div>

      {/* Output section */}
      <OutputVariableField data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    </div>
  )
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function ConditionConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const mode = (data.mode ?? "expression") as string
  const conditions = (data.conditions ?? []) as Array<{
    id: string
    label: string
    variable?: string
    operator?: string
    value?: string
    expression?: string
    llm_prompt?: string
  }>

  const addCondition = () => {
    const newId = `cond_${Date.now()}`
    updateField("conditions", [...conditions, { id: newId, label: "", variable: "", operator: "==", value: "" }])
  }

  const removeCondition = (idx: number) => {
    updateField("conditions", conditions.filter((_, i) => i !== idx))
  }

  const updateCondition = (idx: number, field: string, value: unknown) => {
    const updated = conditions.map((c, i) => (i === idx ? { ...c, [field]: value } : c))
    updateField("conditions", updated)
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configMode")}</label>
        <Select value={mode} onValueChange={(v) => updateField("mode", v)}>
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="expression">{t("configModeExpression")}</SelectItem>
            <SelectItem value="llm">{t("configModeLLM")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <SectionHeader label={t("configConditions")} />
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configConditions")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addCondition}>
          <Plus className="h-3 w-3" />
          {t("configAddCondition")}
        </Button>
      </div>
      {conditions.map((c, i) => (
        <div key={c.id ?? i} className="space-y-2 rounded-md border border-border p-2">
          <div className="flex items-center gap-2">
            <Input
              className="h-7 text-xs flex-1"
              placeholder={t("configConditionLabel")}
              value={c.label}
              onChange={(e) => updateCondition(i, "label", e.target.value)}
            />
            <Button variant="ghost" size="icon-sm" onClick={() => removeCondition(i)}>
              <Trash2 className="h-3 w-3 text-destructive" />
            </Button>
          </div>
          {mode === "expression" ? (
            <div className="space-y-1.5">
              {/* Variable selector */}
              <Input
                className="h-7 text-xs"
                placeholder={t("configConditionVariable")}
                value={c.variable ?? ""}
                onChange={(e) => updateCondition(i, "variable", e.target.value)}
              />
              {/* Operator selector */}
              <Select
                value={c.operator ?? "=="}
                onValueChange={(v) => updateCondition(i, "operator", v)}
              >
                <SelectTrigger className="w-full h-7 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="==">== (equals)</SelectItem>
                  <SelectItem value="!=">!= (not equals)</SelectItem>
                  <SelectItem value=">">&gt; (greater than)</SelectItem>
                  <SelectItem value="<">&lt; (less than)</SelectItem>
                  <SelectItem value="contains">contains</SelectItem>
                  <SelectItem value="not_contains">not contains</SelectItem>
                  <SelectItem value="is_empty">is empty</SelectItem>
                  <SelectItem value="is_not_empty">is not empty</SelectItem>
                </SelectContent>
              </Select>
              {/* Value input (hidden for is_empty/is_not_empty) */}
              {c.operator !== "is_empty" && c.operator !== "is_not_empty" && (
                <Input
                  className="h-7 text-xs"
                  placeholder={t("configConditionValue")}
                  value={c.value ?? ""}
                  onChange={(e) => updateCondition(i, "value", e.target.value)}
                />
              )}
            </div>
          ) : (
            <Textarea
              className="text-xs resize-none"
              rows={2}
              placeholder={t("configLLMPrompt")}
              value={c.llm_prompt ?? ""}
              onChange={(e) => updateCondition(i, "llm_prompt", e.target.value)}
            />
          )}
        </div>
      ))}

      {/* Default (else) branch */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configDefaultBranchLabel")}</label>
        <Input
          className="h-7 text-xs"
          placeholder={t("configDefaultBranchPlaceholder")}
          value={(data.default_branch_label ?? "") as string}
          onChange={(e) => updateField("default_branch_label", e.target.value)}
        />
      </div>
    </div>
  )
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function QuestionClassifierConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const classes = (data.classes ?? []) as Array<{ id: string; label: string; description?: string }>

  const addClass = () => {
    const newId = `cls_${Date.now()}`
    updateField("classes", [...classes, { id: newId, label: "" }])
  }

  const removeClass = (idx: number) => {
    updateField("classes", classes.filter((_, i) => i !== idx))
  }

  const updateClass = (idx: number, field: string, value: unknown) => {
    const updated = classes.map((c, i) => (i === idx ? { ...c, [field]: value } : c))
    updateField("classes", updated)
  }

  return (
    <div className="space-y-3">
      <SectionHeader label={t("configSectionModel")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configModel")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="gpt-4o"
          value={(data.model ?? "") as string}
          onChange={(e) => updateField("model", e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configPromptTemplate")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          value={(data.prompt ?? "") as string}
          onChange={(e) => updateField("prompt", e.target.value)}
        />
      </div>

      <SectionHeader label={t("configClasses")} />
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configClasses")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addClass}>
          <Plus className="h-3 w-3" />
          {t("configAddClass")}
        </Button>
      </div>
      {classes.map((c, i) => (
        <div key={c.id ?? i} className="space-y-2 rounded-md border border-border p-2">
          <div className="flex items-center gap-2">
            <Input
              className="h-7 text-xs flex-1"
              placeholder={t("configClassLabel")}
              value={c.label}
              onChange={(e) => updateClass(i, "label", e.target.value)}
            />
            <Button variant="ghost" size="icon-sm" onClick={() => removeClass(i)}>
              <Trash2 className="h-3 w-3 text-destructive" />
            </Button>
          </div>
          <Input
            className="h-7 text-xs"
            placeholder={t("configClassDescription")}
            value={c.description ?? ""}
            onChange={(e) => updateClass(i, "description", e.target.value)}
          />
        </div>
      ))}
    </div>
  )
}

function AgentConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSelectAgent")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="Agent ID"
          value={(data.agent_id ?? "") as string}
          onChange={(e) => updateField("agent_id", e.target.value)}
        />
      </div>

      <SectionHeader label={t("configSectionPrompt")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configPromptTemplate")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configPromptHint")}
          value={(data.prompt_template ?? "") as string}
          onChange={(e) => updateField("prompt_template", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          t={t}
          onInsert={(ref) => {
            const current = ((data.prompt_template ?? "") as string)
            updateField("prompt_template", current + ref)
          }}
        />
      </div>

      <OutputVariableField data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    </div>
  )
}

function KnowledgeRetrievalConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSelectKB")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="KB ID"
          value={(data.kb_id ?? "") as string}
          onChange={(e) => updateField("kb_id", e.target.value)}
        />
      </div>

      <SectionHeader label={t("configSectionPrompt")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configQueryTemplate")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configPromptHint")}
          value={(data.query_template ?? "") as string}
          onChange={(e) => updateField("query_template", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          t={t}
          onInsert={(ref) => {
            const current = ((data.query_template ?? "") as string)
            updateField("query_template", current + ref)
          }}
        />
      </div>

      <SectionHeader label={t("configSectionParameters")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configTopK")}</label>
        <Input
          className="h-7 text-xs"
          type="number"
          placeholder="5"
          value={(data.top_k ?? "") as string}
          onChange={(e) => updateField("top_k", e.target.value ? Number(e.target.value) : undefined)}
        />
      </div>

      <OutputVariableField data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    </div>
  )
}

function ConnectorConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const parameters = (data.parameters ?? {}) as Record<string, string>
  const paramEntries = Object.entries(parameters)

  const addParam = () => {
    updateField("parameters", { ...parameters, "": "" })
  }

  const removeParam = (key: string) => {
    const updated = { ...parameters }
    delete updated[key]
    updateField("parameters", updated)
  }

  const updateParam = (oldKey: string, newKey: string, value: string) => {
    const updated: Record<string, string> = {}
    for (const [k, v] of Object.entries(parameters)) {
      if (k === oldKey) {
        updated[newKey] = value
      } else {
        updated[k] = v
      }
    }
    updateField("parameters", updated)
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSelectConnector")}</label>
        <Select
          value={(data.connector_id ?? "__default__") as string}
          onValueChange={(v) => updateField("connector_id", v === "__default__" ? "" : v)}
        >
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue placeholder={t("configConnectorPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__default__">{t("configConnectorPlaceholder")}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSelectAction")}</label>
        <Select
          value={(data.action ?? "__default__") as string}
          onValueChange={(v) => updateField("action", v === "__default__" ? "" : v)}
        >
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue placeholder={t("configActionPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__default__">{t("configActionPlaceholder")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Parameter mapping */}
      <SectionHeader label={t("configParameters")} />
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configParameters")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addParam}>
          <Plus className="h-3 w-3" />
          {t("configAddParam")}
        </Button>
      </div>
      {paramEntries.map(([key, value], i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            className="h-7 text-xs flex-1"
            placeholder={t("configKey")}
            value={key}
            onChange={(e) => updateParam(key, e.target.value, value)}
          />
          <Input
            className="h-7 text-xs flex-1"
            placeholder={t("configValue")}
            value={value}
            onChange={(e) => updateParam(key, key, e.target.value)}
          />
          <Button variant="ghost" size="icon-sm" onClick={() => removeParam(key)}>
            <Trash2 className="h-3 w-3 text-destructive" />
          </Button>
        </div>
      ))}
      <InsertVariableBar
        otherNodes={otherNodes}
        t={t}
        onInsert={(ref) => {
          // For connector params, append to last param value if exists
          const paramEntries = Object.entries((data.parameters ?? {}) as Record<string, string>)
          if (paramEntries.length > 0) {
            const [lastKey] = paramEntries[paramEntries.length - 1]
            const updated: Record<string, string> = {}
            for (const [k, v] of paramEntries) {
              updated[k] = k === lastKey ? v + ref : v
            }
            updateField("parameters", updated)
          }
        }}
      />

      <OutputVariableField data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    </div>
  )
}

function HTTPRequestConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const headers = (data.headers ?? {}) as Record<string, string>
  const headerEntries = Object.entries(headers)

  const addHeader = () => {
    updateField("headers", { ...headers, "": "" })
  }

  const removeHeader = (key: string) => {
    const updated = { ...headers }
    delete updated[key]
    updateField("headers", updated)
  }

  const updateHeader = (oldKey: string, newKey: string, value: string) => {
    const updated: Record<string, string> = {}
    for (const [k, v] of Object.entries(headers)) {
      if (k === oldKey) {
        updated[newKey] = value
      } else {
        updated[k] = v
      }
    }
    updateField("headers", updated)
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configMethod")}</label>
        <Select value={(data.method ?? "GET") as string} onValueChange={(v) => updateField("method", v)}>
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="GET">GET</SelectItem>
            <SelectItem value="POST">POST</SelectItem>
            <SelectItem value="PUT">PUT</SelectItem>
            <SelectItem value="PATCH">PATCH</SelectItem>
            <SelectItem value="DELETE">DELETE</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configURL")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="https://api.example.com/..."
          value={(data.url ?? "") as string}
          onChange={(e) => updateField("url", e.target.value)}
        />
      </div>

      {/* Headers section */}
      <SectionHeader label={t("configSectionHeaders")} />
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configHeaders")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addHeader}>
          <Plus className="h-3 w-3" />
          {t("configAddHeader")}
        </Button>
      </div>
      {headerEntries.map(([key, value], i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input
            className="h-7 text-xs flex-1"
            placeholder={t("configHeaderKey")}
            value={key}
            onChange={(e) => updateHeader(key, e.target.value, value)}
          />
          <Input
            className="h-7 text-xs flex-1"
            placeholder={t("configHeaderValue")}
            value={value}
            onChange={(e) => updateHeader(key, key, e.target.value)}
          />
          <Button variant="ghost" size="icon-sm" className="h-7 w-7 shrink-0" onClick={() => removeHeader(key)}>
            <Trash2 className="h-3 w-3 text-destructive" />
          </Button>
        </div>
      ))}

      {/* Body section */}
      <SectionHeader label={t("configBody")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configBody")}</label>
        <Textarea
          className="text-xs resize-none font-mono"
          rows={3}
          placeholder="{}"
          value={(data.body ?? "") as string}
          onChange={(e) => updateField("body", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          t={t}
          onInsert={(ref) => {
            const current = ((data.body ?? "") as string)
            updateField("body", current + ref)
          }}
        />
      </div>

      <OutputVariableField data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    </div>
  )
}

function VariableAssignConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const assignments = (data.assignments ?? []) as Array<{ variable: string; expression: string }>

  const addAssignment = () => {
    updateField("assignments", [...assignments, { variable: "", expression: "" }])
  }

  const removeAssignment = (idx: number) => {
    updateField("assignments", assignments.filter((_, i) => i !== idx))
  }

  const updateAssignment = (idx: number, field: string, value: string) => {
    const updated = assignments.map((a, i) => (i === idx ? { ...a, [field]: value } : a))
    updateField("assignments", updated)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium">{t("configAssignments")}</label>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addAssignment}>
          <Plus className="h-3 w-3" />
          {t("configAddAssignment")}
        </Button>
      </div>
      {assignments.map((a, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            className="h-7 text-xs flex-1"
            placeholder={t("configVariable")}
            value={a.variable}
            onChange={(e) => updateAssignment(i, "variable", e.target.value)}
          />
          <Input
            className="h-7 text-xs flex-1"
            placeholder={t("configExpression")}
            value={a.expression}
            onChange={(e) => updateAssignment(i, "expression", e.target.value)}
          />
          <Button variant="ghost" size="icon-sm" onClick={() => removeAssignment(i)}>
            <Trash2 className="h-3 w-3 text-destructive" />
          </Button>
        </div>
      ))}
      <InsertVariableBar
        otherNodes={otherNodes}
        t={t}
        onInsert={(ref) => {
          // Append to last assignment's expression if exists
          if (assignments.length > 0) {
            const lastIdx = assignments.length - 1
            updateAssignment(lastIdx, "expression", assignments[lastIdx].expression + ref)
          }
        }}
      />
    </div>
  )
}

function TemplateTransformConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configTemplate")}</label>
        <Textarea
          className="text-xs resize-none font-mono"
          rows={6}
          value={(data.template ?? "") as string}
          onChange={(e) => updateField("template", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          t={t}
          onInsert={(ref) => {
            const current = ((data.template ?? "") as string)
            updateField("template", current + ref)
          }}
        />
      </div>

      <OutputVariableField data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    </div>
  )
}

function CodeExecutionConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const language = (data.language ?? "python") as string

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configLanguage")}</label>
        <Select value={language} onValueChange={(v) => updateField("language", v)}>
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="python">Python</SelectItem>
            <SelectItem value="javascript">JavaScript</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <SectionHeader label={t("configCode")} />
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium">{t("configCode")}</label>
          <span className="text-[10px] font-mono text-muted-foreground/50">
            {language === "python" ? ".py" : ".js"}
          </span>
        </div>
        <Textarea
          className="text-xs resize-none font-mono leading-relaxed bg-muted/30"
          rows={8}
          placeholder={t("configCodeHint")}
          value={(data.code ?? "") as string}
          onChange={(e) => updateField("code", e.target.value)}
          spellCheck={false}
        />
        <p className="text-[10px] text-muted-foreground/60 font-mono">
          {language === "python"
            ? t("configCodeOutputHint_python")
            : t("configCodeOutputHint_javascript")}
        </p>
      </div>

      <OutputVariableField data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    </div>
  )
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function IteratorConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  return (
    <div className="space-y-3">
      {/* List variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configListVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          placeholder={t("configListVariablePlaceholder")}
          value={(data.list_variable ?? "") as string}
          onChange={(e) => updateField("list_variable", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => updateField("list_variable", ((data.list_variable ?? "") as string) + ref)}
          t={t}
        />
      </div>

      {/* Iterator variable name */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configIteratorVariable")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="current_item"
          value={(data.iterator_variable ?? "current_item") as string}
          onChange={(e) => updateField("iterator_variable", e.target.value)}
        />
      </div>

      {/* Index variable name */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configIndexVariable")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="current_index"
          value={(data.index_variable ?? "current_index") as string}
          onChange={(e) => updateField("index_variable", e.target.value)}
        />
      </div>

      {/* Max iterations */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configMaxIterations")}</label>
        <Input
          type="number"
          className="h-7 text-xs"
          value={(data.max_iterations ?? 100) as number}
          min={1}
          max={10000}
          onChange={(e) => {
            const v = parseInt(e.target.value, 10)
            if (!isNaN(v) && v > 0) updateField("max_iterations", v)
          }}
        />
        <p className="text-[10px] text-muted-foreground/60">
          {t("configMaxIterationsHint")}
        </p>
      </div>
    </div>
  )
}

const AGGREGATE_MODES = ["list", "concat", "merge", "first_non_empty"] as const

function VariableAggregatorConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const variables = (data.variables ?? []) as string[]
  const mode = (data.mode ?? "list") as string

  const addVariable = () => {
    updateField("variables", [...variables, ""])
  }

  const removeVariable = (index: number) => {
    updateField("variables", variables.filter((_, i) => i !== index))
  }

  const updateVariable = (index: number, value: string) => {
    const updated = [...variables]
    updated[index] = value
    updateField("variables", updated)
  }

  return (
    <div className="space-y-3">
      {/* Aggregation mode */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configAggregateMode")}</label>
        <Select
          value={mode}
          onValueChange={(v) => updateField("mode", v)}
        >
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {AGGREGATE_MODES.map((m) => (
              <SelectItem key={m} value={m} className="text-xs">
                {t(`configAggregateMode_${m}` as Parameters<typeof t>[0])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Separator (only for concat mode) */}
      {mode === "concat" && (
        <div className="space-y-1.5">
          <label className="text-xs font-medium">{t("configSeparator")}</label>
          <Input
            className="h-7 text-xs"
            placeholder="\n"
            value={(data.separator ?? "\n") as string}
            onChange={(e) => updateField("separator", e.target.value)}
          />
        </div>
      )}

      {/* Input variables */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium">{t("configInputVariables")}</label>
          <Button variant="ghost" size="icon-sm" onClick={addVariable} className="h-5 w-5">
            <Plus className="h-3 w-3" />
          </Button>
        </div>
        {variables.length === 0 ? (
          <p className="text-[10px] text-muted-foreground/60">{t("variablePickerEmpty")}</p>
        ) : (
          <div className="space-y-1.5">
            {variables.map((v, i) => (
              <div key={i} className="flex items-center gap-1">
                <Input
                  className="h-7 text-xs font-mono flex-1"
                  placeholder="{{node_id.output}}"
                  value={v}
                  onChange={(e) => updateVariable(i, e.target.value)}
                />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => removeVariable(i)}
                  className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
            ))}
          </div>
        )}
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => updateField("variables", [...variables, ref])}
          t={t}
        />
      </div>
    </div>
  )
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function LoopConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  return (
    <div className="space-y-3">
      {/* Condition */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configLoopCondition")}</label>
        <Input
          className="h-7 text-xs font-mono"
          placeholder="loop_index < 10"
          value={(data.condition ?? "") as string}
          onChange={(e) => updateField("condition", e.target.value)}
        />
        <p className="text-[10px] text-muted-foreground/60">
          {t("configLoopConditionHint")}
        </p>
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => updateField("condition", ((data.condition ?? "") as string) + ref)}
          t={t}
        />
      </div>

      {/* Loop variable name */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configLoopVariable")}</label>
        <Input
          className="h-7 text-xs"
          placeholder="loop_index"
          value={(data.loop_variable ?? "loop_index") as string}
          onChange={(e) => updateField("loop_variable", e.target.value)}
        />
      </div>

      {/* Max iterations */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configLoopMaxIterations")}</label>
        <Input
          type="number"
          className="h-7 text-xs"
          value={(data.max_iterations ?? 50) as number}
          min={1}
          max={10000}
          onChange={(e) => {
            const v = parseInt(e.target.value, 10)
            if (!isNaN(v) && v > 0) updateField("max_iterations", v)
          }}
        />
        <p className="text-[10px] text-muted-foreground/60">
          {t("configMaxIterationsHint")}
        </p>
      </div>
    </div>
  )
}

function ParameterExtractorConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const params = (data.parameters ?? []) as Array<{
    name: string
    type: string
    description: string
    required?: boolean
  }>

  const addParam = () => {
    updateField("parameters", [
      ...params,
      { name: "", type: "string", description: "", required: true },
    ])
  }

  const removeParam = (index: number) => {
    updateField("parameters", params.filter((_, i) => i !== index))
  }

  const updateParam = (index: number, field: string, value: unknown) => {
    const updated = [...params]
    updated[index] = { ...updated[index], [field]: value }
    updateField("parameters", updated)
  }

  return (
    <div className="space-y-3">
      {/* Input text */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configInputText")}</label>
        <Input
          className="h-7 text-xs font-mono"
          placeholder={t("configInputTextPlaceholder")}
          value={(data.input_text ?? "") as string}
          onChange={(e) => updateField("input_text", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => updateField("input_text", ((data.input_text ?? "") as string) + ref)}
          t={t}
        />
      </div>

      {/* Parameters to extract */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium">{t("configExtractParams")}</label>
          <Button variant="ghost" size="icon-sm" onClick={addParam} className="h-5 w-5">
            <Plus className="h-3 w-3" />
          </Button>
        </div>
        {params.length === 0 ? (
          <p className="text-[10px] text-muted-foreground/60">{t("configAddParam")}</p>
        ) : (
          <div className="space-y-2">
            {params.map((p, i) => (
              <div key={i} className="rounded-md border border-border/50 p-2 space-y-1.5">
                <div className="flex items-center gap-1">
                  <Input
                    className="h-6 text-xs flex-1"
                    placeholder={t("configParamName")}
                    value={p.name}
                    onChange={(e) => updateParam(i, "name", e.target.value)}
                  />
                  <Select
                    value={p.type}
                    onValueChange={(v) => updateParam(i, "type", v)}
                  >
                    <SelectTrigger className="w-[90px] h-6 text-[10px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {["string", "number", "boolean", "array"].map((t2) => (
                        <SelectItem key={t2} value={t2} className="text-xs">{t2}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => removeParam(i)}
                    className="h-5 w-5 shrink-0 text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
                <Input
                  className="h-6 text-[10px]"
                  placeholder={t("configParamDescription")}
                  value={p.description}
                  onChange={(e) => updateParam(i, "description", e.target.value)}
                />
                <div className="flex items-center gap-1.5">
                  <Switch
                    checked={p.required !== false}
                    onCheckedChange={(v) => updateParam(i, "required", v)}
                    className="scale-75 origin-left"
                  />
                  <span className="text-[10px] text-muted-foreground">{t("configParamRequired")}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Additional instructions */}
      <SectionHeader label={t("configSectionPrompt")} />
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configExtractionPrompt")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configExtractionPromptHint")}
          value={(data.extraction_prompt ?? "") as string}
          onChange={(e) => updateField("extraction_prompt", e.target.value)}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 15. ListOperation
// ---------------------------------------------------------------------------

const LIST_OPERATIONS = ["filter", "map", "sort", "slice", "flatten", "unique", "reverse", "length"] as const

function ListOperationConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const operation = (data.operation ?? "filter") as string
  const needsExpression = ["filter", "map", "sort"].includes(operation)
  const isSlice = operation === "slice"

  return (
    <div className="space-y-3">
      {/* Input variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configInputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          placeholder="{{node_id.variable}}"
          value={(data.input_variable ?? "") as string}
          onChange={(e) => updateField("input_variable", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => updateField("input_variable", ((data.input_variable ?? "") as string) + ref)}
          t={t}
        />
      </div>

      {/* Operation select */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configListOperation")}</label>
        <Select value={operation} onValueChange={(v) => updateField("operation", v)}>
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {LIST_OPERATIONS.map((op) => (
              <SelectItem key={op} value={op} className="text-xs">
                {t(`listOp_${op}` as Parameters<typeof t>[0])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Expression (for filter/map/sort) */}
      {needsExpression && (
        <div className="space-y-1.5">
          <label className="text-xs font-medium">{t("configExpression")}</label>
          <Input
            className="h-7 text-xs font-mono"
            placeholder={operation === "sort" ? "item.name" : "item > 10"}
            value={(data.expression ?? "") as string}
            onChange={(e) => updateField("expression", e.target.value)}
          />
          <p className="text-[10px] text-muted-foreground/60">{t("configExpressionHint")}</p>
        </div>
      )}

      {/* Slice params */}
      {isSlice && (
        <div className="flex gap-2">
          <div className="flex-1 space-y-1">
            <label className="text-[10px] font-medium">{t("configSliceStart")}</label>
            <Input
              type="number"
              className="h-7 text-xs"
              value={(data.slice_start ?? 0) as number}
              onChange={(e) => updateField("slice_start", parseInt(e.target.value) || 0)}
            />
          </div>
          <div className="flex-1 space-y-1">
            <label className="text-[10px] font-medium">{t("configSliceEnd")}</label>
            <Input
              type="number"
              className="h-7 text-xs"
              placeholder="end"
              value={(data.slice_end ?? "") as string}
              onChange={(e) => updateField("slice_end", e.target.value ? parseInt(e.target.value) : null)}
            />
          </div>
        </div>
      )}

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={(data.output_variable ?? "list_result") as string}
          onChange={(e) => updateField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 16. Transform
// ---------------------------------------------------------------------------

const TRANSFORM_TYPES = ["json_path", "type_cast", "format", "regex_extract", "string_op", "math_op"] as const

function TransformConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const operations = (data.operations ?? []) as Array<{
    type: string
    config: Record<string, unknown>
  }>

  const addOperation = () => {
    updateField("operations", [
      ...operations,
      { type: "json_path", config: { path: "$.data" } },
    ])
  }

  const removeOperation = (index: number) => {
    updateField("operations", operations.filter((_, i) => i !== index))
  }

  const updateOperation = (index: number, field: string, value: unknown) => {
    const updated = [...operations]
    if (field === "type") {
      updated[index] = { type: value as string, config: {} }
    } else {
      updated[index] = { ...updated[index], config: { ...updated[index].config, [field]: value } }
    }
    updateField("operations", updated)
  }

  return (
    <div className="space-y-3">
      {/* Input variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configInputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          placeholder="{{node_id.variable}}"
          value={(data.input_variable ?? "") as string}
          onChange={(e) => updateField("input_variable", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => updateField("input_variable", ((data.input_variable ?? "") as string) + ref)}
          t={t}
        />
      </div>

      {/* Transform operations pipeline */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium">{t("configTransformOps")}</label>
          <Button variant="ghost" size="icon-sm" onClick={addOperation} className="h-5 w-5">
            <Plus className="h-3 w-3" />
          </Button>
        </div>
        {operations.length === 0 ? (
          <p className="text-[10px] text-muted-foreground/60">{t("configAddTransformOp")}</p>
        ) : (
          <div className="space-y-2">
            {operations.map((op, i) => (
              <div key={i} className="rounded-md border border-border/50 p-2 space-y-1.5">
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-muted-foreground/50 w-4 shrink-0">{i + 1}</span>
                  <Select value={op.type} onValueChange={(v) => updateOperation(i, "type", v)}>
                    <SelectTrigger className="flex-1 h-6 text-[10px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TRANSFORM_TYPES.map((tt) => (
                        <SelectItem key={tt} value={tt} className="text-xs">
                          {t(`transformType_${tt}` as Parameters<typeof t>[0])}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => removeOperation(i)}
                    className="h-5 w-5 shrink-0 text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
                {/* Config fields per type */}
                <TransformOpConfig op={op} index={i} updateOperation={updateOperation} t={t} />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={(data.output_variable ?? "transform_result") as string}
          onChange={(e) => updateField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}

function TransformOpConfig({
  op,
  index,
  updateOperation,
  t,
}: {
  op: { type: string; config: Record<string, unknown> }
  index: number
  updateOperation: (index: number, field: string, value: unknown) => void
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  const config = op.config ?? {}

  switch (op.type) {
    case "json_path":
      return (
        <Input
          className="h-6 text-[10px] font-mono"
          placeholder="$.data.items[0].name"
          value={(config.path ?? "") as string}
          onChange={(e) => updateOperation(index, "path", e.target.value)}
        />
      )
    case "type_cast":
      return (
        <Select value={(config.target_type ?? "string") as string} onValueChange={(v) => updateOperation(index, "target_type", v)}>
          <SelectTrigger className="h-6 text-[10px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["string", "integer", "float", "boolean", "json"].map((tt) => (
              <SelectItem key={tt} value={tt} className="text-xs">{tt}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
    case "format":
      return (
        <Input
          className="h-6 text-[10px] font-mono"
          placeholder="Hello {value}"
          value={(config.template ?? "") as string}
          onChange={(e) => updateOperation(index, "template", e.target.value)}
        />
      )
    case "regex_extract":
      return (
        <div className="flex gap-1">
          <Input
            className="h-6 text-[10px] font-mono flex-1"
            placeholder="\\d+"
            value={(config.pattern ?? "") as string}
            onChange={(e) => updateOperation(index, "pattern", e.target.value)}
          />
          <Input
            type="number"
            className="h-6 text-[10px] w-12"
            placeholder="0"
            value={(config.group ?? 0) as number}
            onChange={(e) => updateOperation(index, "group", parseInt(e.target.value) || 0)}
          />
        </div>
      )
    case "string_op":
      return (
        <Select value={(config.operation ?? "upper") as string} onValueChange={(v) => updateOperation(index, "operation", v)}>
          <SelectTrigger className="h-6 text-[10px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["upper", "lower", "strip", "split", "join", "replace"].map((s) => (
              <SelectItem key={s} value={s} className="text-xs">{s}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
    case "math_op":
      return (
        <div className="flex gap-1">
          <Select value={(config.operation ?? "add") as string} onValueChange={(v) => updateOperation(index, "operation", v)}>
            <SelectTrigger className="flex-1 h-6 text-[10px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {["add", "subtract", "multiply", "divide", "modulo", "round", "abs"].map((m) => (
                <SelectItem key={m} value={m} className="text-xs">{m}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {(config.operation !== "abs") && (
            <Input
              type="number"
              className="h-6 text-[10px] w-16"
              placeholder="0"
              value={(config.operand ?? 0) as number}
              onChange={(e) => updateOperation(index, "operand", parseFloat(e.target.value) || 0)}
            />
          )}
        </div>
      )
    default:
      return null
  }
}

// ---------------------------------------------------------------------------
// 17. DocumentExtractor
// ---------------------------------------------------------------------------

const INPUT_TYPES = ["text", "base64", "url"] as const
const EXTRACT_MODES = ["full_text", "pages", "metadata", "tables"] as const

function DocumentExtractorConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  const extractMode = (data.extract_mode ?? "full_text") as string

  return (
    <div className="space-y-3">
      {/* Input variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configInputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          placeholder="{{node_id.variable}}"
          value={(data.input_variable ?? "") as string}
          onChange={(e) => updateField("input_variable", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => updateField("input_variable", ((data.input_variable ?? "") as string) + ref)}
          t={t}
        />
      </div>

      {/* Input type */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configInputType")}</label>
        <Select value={(data.input_type ?? "text") as string} onValueChange={(v) => updateField("input_type", v)}>
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {INPUT_TYPES.map((it) => (
              <SelectItem key={it} value={it} className="text-xs">
                {t(`inputType_${it}` as Parameters<typeof t>[0])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Extract mode */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configExtractMode")}</label>
        <Select value={extractMode} onValueChange={(v) => updateField("extract_mode", v)}>
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {EXTRACT_MODES.map((em) => (
              <SelectItem key={em} value={em} className="text-xs">
                {t(`extractMode_${em}` as Parameters<typeof t>[0])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Page range (only for pages mode) */}
      {extractMode === "pages" && (
        <div className="space-y-1.5">
          <label className="text-xs font-medium">{t("configPageRange")}</label>
          <Input
            className="h-7 text-xs font-mono"
            placeholder="1-5"
            value={(data.page_range ?? "") as string}
            onChange={(e) => updateField("page_range", e.target.value)}
          />
          <p className="text-[10px] text-muted-foreground/60">{t("configPageRangeHint")}</p>
        </div>
      )}

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={(data.output_variable ?? "document_result") as string}
          onChange={(e) => updateField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 18. QuestionUnderstanding
// ---------------------------------------------------------------------------

const QUESTION_MODES = ["rewrite", "expand", "classify", "decompose"] as const

function QuestionUnderstandingConfig({ data, updateField, t, otherNodes }: ConfigProps) {
  return (
    <div className="space-y-3">
      {/* Input variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configInputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          placeholder="{{node_id.variable}}"
          value={(data.input_variable ?? "") as string}
          onChange={(e) => updateField("input_variable", e.target.value)}
        />
        <InsertVariableBar
          otherNodes={otherNodes}
          onInsert={(ref) => updateField("input_variable", ((data.input_variable ?? "") as string) + ref)}
          t={t}
        />
      </div>

      {/* Processing mode */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configQuestionMode")}</label>
        <Select value={(data.mode ?? "rewrite") as string} onValueChange={(v) => updateField("mode", v)}>
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {QUESTION_MODES.map((qm) => (
              <SelectItem key={qm} value={qm} className="text-xs">
                {t(`questionMode_${qm}` as Parameters<typeof t>[0])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Custom system prompt */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSystemPrompt")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configSystemPromptHint")}
          value={(data.system_prompt ?? "") as string}
          onChange={(e) => updateField("system_prompt", e.target.value)}
        />
      </div>

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={(data.output_variable ?? "question_result") as string}
          onChange={(e) => updateField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}

// --- Human Intervention config ---

function HumanInterventionConfig({ data, updateField, t }: ConfigProps) {
  return (
    <div className="space-y-3">
      {/* Prompt message */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configPromptMessage")}</label>
        <Textarea
          className="text-xs resize-none"
          rows={3}
          placeholder={t("configPromptMessagePlaceholder")}
          value={(data.prompt_message ?? "") as string}
          onChange={(e) => updateField("prompt_message", e.target.value)}
        />
      </div>

      {/* Assignee */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configAssignee")}</label>
        <Input
          className="h-7 text-xs"
          placeholder={t("configAssigneePlaceholder")}
          value={(data.assignee ?? "") as string}
          onChange={(e) => updateField("assignee", e.target.value)}
        />
      </div>

      {/* Timeout (hours) */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configTimeoutHours")}</label>
        <Input
          className="h-7 text-xs"
          type="number"
          min={1}
          value={(data.timeout_hours ?? 24) as number}
          onChange={(e) => updateField("timeout_hours", Number(e.target.value) || 24)}
        />
      </div>

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={(data.output_variable ?? "approval_result") as string}
          onChange={(e) => updateField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}

function MCPConfig({ data, updateField, t }: ConfigProps) {
  return (
    <div className="space-y-3">
      {/* Server ID */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configServerId")}</label>
        <Input
          className="h-7 text-xs"
          placeholder={t("configServerIdPlaceholder")}
          value={(data.server_id ?? "") as string}
          onChange={(e) => updateField("server_id", e.target.value)}
        />
      </div>

      {/* Tool Name */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configToolName")}</label>
        <Input
          className="h-7 text-xs"
          placeholder={t("configToolNamePlaceholder")}
          value={(data.tool_name ?? "") as string}
          onChange={(e) => updateField("tool_name", e.target.value)}
        />
      </div>

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={(data.output_variable ?? "mcp_result") as string}
          onChange={(e) => updateField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}

function BuiltinToolConfig({ data, updateField, t }: ConfigProps) {
  return (
    <div className="space-y-3">
      {/* Tool ID */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configToolId")}</label>
        <Input
          className="h-7 text-xs"
          placeholder={t("configToolIdPlaceholder")}
          value={(data.tool_id ?? "") as string}
          onChange={(e) => updateField("tool_id", e.target.value)}
        />
      </div>

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={(data.output_variable ?? "tool_result") as string}
          onChange={(e) => updateField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}
