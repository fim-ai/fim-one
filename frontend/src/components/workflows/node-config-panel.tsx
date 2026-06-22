"use client"

import { useState, useCallback, useMemo } from "react"
import { useTranslations } from "next-intl"
import { Beaker, ChevronDown, MessageSquare, Plus, Trash2, X } from "lucide-react"
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
import { TestNodeDialog } from "./test-node-dialog"
import type { Node } from "@xyflow/react"
import type { WorkflowNodeType, ErrorStrategy } from "@/types/workflow"

interface NodeConfigPanelProps {
  workflowId: string
  node: Node | null
  allNodes: Node[]
  onUpdate: (nodeId: string, data: Record<string, unknown>) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}

export function NodeConfigPanel({ workflowId, node, allNodes, onUpdate, onDelete, onClose }: NodeConfigPanelProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")
  const [testDialogOpen, setTestDialogOpen] = useState(false)

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
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setTestDialogOpen(true)}
            title={t("testNode")}
          >
            <Beaker className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
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
              retryCount={(node.data.retry_count as number) ?? 0}
              retryDelayMs={(node.data.retry_delay_ms as number) ?? 1000}
              onChangeErrorStrategy={(v) => updateField("error_strategy", v)}
              onChangeTimeout={(v) => updateField("timeout_ms", v)}
              onChangeRetryCount={(v) => updateField("retry_count", v)}
              onChangeRetryDelay={(v) => updateField("retry_delay_ms", v)}
            />
          )}

          {/* Comment / annotation section — all node types */}
          <CommentSection
            comment={(node.data.comment as string) ?? ""}
            onChange={(v) => updateField("comment", v || undefined)}
          />

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

      {/* Test Node Dialog */}
      <TestNodeDialog
        workflowId={workflowId}
        nodeId={node.id}
        nodeType={nodeType}
        nodeLabel={t(`nodeType_${nodeType}` as Parameters<typeof t>[0])}
        open={testDialogOpen}
        onOpenChange={setTestDialogOpen}
      />
    </div>
  )
}

// --- Advanced Section (error strategy + timeout) ---

const ERROR_STRATEGIES: ErrorStrategy[] = ["stop_workflow", "continue", "fail_branch"]

function AdvancedSection({
  errorStrategy,
  timeoutMs,
  retryCount,
  retryDelayMs,
  onChangeErrorStrategy,
  onChangeTimeout,
  onChangeRetryCount,
  onChangeRetryDelay,
}: {
  errorStrategy: ErrorStrategy
  timeoutMs: number
  retryCount: number
  retryDelayMs: number
  onChangeErrorStrategy: (v: ErrorStrategy) => void
  onChangeTimeout: (v: number) => void
  onChangeRetryCount: (v: number) => void
  onChangeRetryDelay: (v: number) => void
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
          {/* Retry */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t("configRetryCount")}</label>
            <Input
              type="number"
              className="h-7 text-xs"
              value={retryCount}
              min={0}
              max={10}
              step={1}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10)
                if (!isNaN(v) && v >= 0) onChangeRetryCount(v)
              }}
            />
            <p className="text-[10px] text-muted-foreground">
              {t("configRetryCountHint")}
            </p>
          </div>
          {retryCount > 0 && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">{t("configRetryDelay")}</label>
              <Input
                type="number"
                className="h-7 text-xs"
                value={retryDelayMs}
                min={100}
                max={60000}
                step={500}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10)
                  if (!isNaN(v) && v >= 100) onChangeRetryDelay(v)
                }}
              />
              <p className="text-[10px] text-muted-foreground">
                {t("configRetryDelayHint")}
              </p>
            </div>
          )}
        </div>
      )}
    </>
  )
}

// --- Comment / annotation section ---

function CommentSection({
  comment,
  onChange,
}: {
  comment: string
  onChange: (value: string) => void
}) {
  const t = useTranslations("workflows")
  const [expanded, setExpanded] = useState(false)

  const firstLine = comment ? comment.split("\n")[0] : ""
  const hasComment = comment.length > 0

  return (
    <>
      <Separator className="my-1" />
      <button
        type="button"
        className="flex items-center justify-between w-full text-xs font-medium text-muted-foreground hover:text-foreground transition-colors py-1"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="flex items-center gap-1.5">
          <MessageSquare className="h-3 w-3" />
          {t("configCommentSection")}
        </span>
        <span className="flex items-center gap-1.5">
          {!expanded && hasComment && (
            <span className="text-[10px] text-muted-foreground/70 truncate max-w-[120px]">
              {firstLine}
            </span>
          )}
          <ChevronDown
            className={cn(
              "h-3 w-3 transition-transform duration-200",
              expanded && "rotate-180",
            )}
          />
        </span>
      </button>
      {expanded && (
        <div className="pb-1">
          <Textarea
            value={comment}
            onChange={(e) => onChange(e.target.value)}
            placeholder={t("configCommentPlaceholder")}
            className="min-h-[60px] text-xs resize-none"
            rows={3}
            onInput={(e) => {
              const target = e.target as HTMLTextAreaElement
              target.style.height = "auto"
              target.style.height = `${target.scrollHeight}px`
            }}
          />
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
    case "agent":
      return <AgentConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "knowledgeRetrieval":
      return <KnowledgeRetrievalConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "connector":
      return <ConnectorConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "humanIntervention":
      return <HumanInterventionConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
    case "mcp":
      return <MCPConfig data={data} updateField={updateField} t={t} otherNodes={otherNodes} />
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
  return (
    <div className="space-y-3">
      {/* Model section — the model is fully system-managed: it resolves to the
          configured Model Provider (else .env), exactly like the playground.
          The node exposes no model knob on purpose. */}
      <SectionHeader label={t("configSectionModel")} />
      <p className="text-[10px] text-muted-foreground/60">
        {t("configModelManagedHint")}
      </p>

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
