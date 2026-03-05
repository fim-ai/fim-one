"use client"

import { useEffect, useState } from "react"
import { Bot, Brain, Edit2, Plus, Trash2, X, Zap } from "lucide-react"
import { toast } from "sonner"

import { modelApi } from "@/lib/api"
import type { ModelConfigCreate, ModelConfigResponse, ModelConfigUpdate } from "@/types/model_config"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"

// ─── Role Slot Card ───────────────────────────────────────────────────────────

interface RoleSlotProps {
  role: "general" | "fast"
  active: ModelConfigResponse | undefined
  onAssign: (role: "general" | "fast") => void
  onClear: (id: string) => void
}

function RoleSlot({ role, active, onAssign, onClear }: RoleSlotProps) {
  const isGeneral = role === "general"
  const Icon = isGeneral ? Brain : Zap
  const label = isGeneral ? "General Model" : "Fast Model"
  const envVar = isGeneral ? "LLM_MODEL" : "FAST_LLM_MODEL"
  const desc = isGeneral
    ? "Used for ReAct reasoning, DAG planning, and analysis"
    : "Used for DAG step execution (falls back to General if not set)"

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-start gap-3">
        <div
          className={`mt-0.5 rounded-md p-1.5 ${
            isGeneral
              ? "bg-blue-500/10 text-blue-500"
              : "bg-amber-500/10 text-amber-500"
          }`}
        >
          <Icon className="h-4 w-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <div>
              <p className="text-sm font-medium">{label}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{desc}</p>
            </div>
            {active ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs shrink-0 text-muted-foreground hover:text-foreground"
                onClick={() => onClear(active.id)}
              >
                <X className="h-3 w-3 mr-1" />
                Clear
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs shrink-0"
                onClick={() => onAssign(role)}
              >
                Assign
              </Button>
            )}
          </div>
          {active ? (
            <div className="mt-2 flex items-center gap-2 rounded-md bg-muted/50 px-3 py-2">
              <Bot className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0">
                <span className="text-sm font-medium">{active.name}</span>
                <span className="text-xs text-muted-foreground ml-2">
                  {active.provider} · {active.model_name}
                </span>
              </div>
            </div>
          ) : (
            <div className="mt-2 flex items-center gap-2 rounded-md border border-dashed px-3 py-2 text-muted-foreground">
              <span className="text-xs">ENV default ({envVar})</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Assign Role Dialog ───────────────────────────────────────────────────────

interface AssignRoleDialogProps {
  open: boolean
  role: "general" | "fast" | null
  models: ModelConfigResponse[]
  onAssign: (modelId: string) => void
  onClose: () => void
}

function AssignRoleDialog({ open, role, models, onAssign, onClose }: AssignRoleDialogProps) {
  const label = role === "general" ? "General Model" : "Fast Model"
  const available = models.filter((m) => m.role !== role)

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Assign {label}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2 py-2">
          {available.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              No providers available. Add a provider first.
            </p>
          ) : (
            available.map((m) => (
              <button
                key={m.id}
                className="w-full flex items-center gap-3 rounded-md border px-3 py-2.5 text-left hover:bg-accent transition-colors"
                onClick={() => onAssign(m.id)}
              >
                <Bot className="h-4 w-4 text-muted-foreground shrink-0" />
                <div>
                  <p className="text-sm font-medium">{m.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {m.provider} · {m.model_name}
                  </p>
                </div>
                {m.role && (
                  <Badge variant="secondary" className="ml-auto text-xs">
                    {m.role}
                  </Badge>
                )}
              </button>
            ))
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ─── Provider Form Dialog ─────────────────────────────────────────────────────

interface ProviderDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  editing?: ModelConfigResponse | null
  onSaved: () => void
}

function ProviderDialog({ open, onOpenChange, editing, onSaved }: ProviderDialogProps) {
  const [name, setName] = useState("")
  const [provider, setProvider] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [modelName, setModelName] = useState("")
  const [maxOutputTokens, setMaxOutputTokens] = useState("")
  const [contextSize, setContextSize] = useState("")
  const [temperature, setTemperature] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)

  // Populate form when editing
  useEffect(() => {
    if (editing) {
      setName(editing.name)
      setProvider(editing.provider)
      setBaseUrl(editing.base_url ?? "")
      setApiKey("") // never pre-fill api key
      setModelName(editing.model_name)
      setMaxOutputTokens(editing.max_output_tokens?.toString() ?? "")
      setContextSize(editing.context_size?.toString() ?? "")
      setTemperature(editing.temperature)
    } else {
      setName("")
      setProvider("")
      setBaseUrl("")
      setApiKey("")
      setModelName("")
      setMaxOutputTokens("")
      setContextSize("")
      setTemperature(null)
    }
    setShowCloseConfirm(false)
  }, [editing, open])

  const isDirty =
    !editing &&
    (name.trim().length > 0 ||
      provider.trim().length > 0 ||
      modelName.trim().length > 0 ||
      apiKey.trim().length > 0)

  const handleClose = (open: boolean) => {
    if (!open && isDirty) {
      setShowCloseConfirm(true)
      return
    }
    onOpenChange(open)
  }

  const handleSubmit = async () => {
    if (!name.trim() || !modelName.trim() || (!editing && !apiKey.trim())) {
      toast.error(!name.trim() || !modelName.trim() ? "Name and Model Name are required" : "API Key is required")
      return
    }
    setSaving(true)
    try {
      const body: ModelConfigCreate = {
        name: name.trim(),
        provider: provider.trim(),
        model_name: modelName.trim(),
        base_url: baseUrl.trim() || null,
        api_key: apiKey.trim() || null,
        category: "llm",
        temperature,
        max_output_tokens: maxOutputTokens ? parseInt(maxOutputTokens) : null,
        context_size: contextSize ? parseInt(contextSize) : null,
      }
      if (editing) {
        const updateBody: ModelConfigUpdate = { ...body }
        if (!apiKey.trim()) delete updateBody.api_key
        await modelApi.update(editing.id, updateBody)
        toast.success("Model updated")
      } else {
        await modelApi.create(body)
        toast.success("Model provider added")
      }
      onSaved()
      onOpenChange(false)
    } catch {
      toast.error(editing ? "Failed to update model" : "Failed to add model provider")
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={handleClose}>
        <DialogContent
          className="max-w-lg flex flex-col max-h-[90vh]"
          onInteractOutside={(e) => {
            if (isDirty) {
              e.preventDefault()
              setShowCloseConfirm(true)
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>
              {editing ? "Edit Model Provider" : "Add Model Provider"}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto">
          <div className="space-y-4 py-2">
            {/* Compatibility notice */}
            <div className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
              Only <strong>OpenAI-compatible</strong> APIs are supported. For Anthropic or other providers, use their OpenAI-compatible endpoint and API key.
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="mc-name">
                Name <span className="text-destructive">*</span>
              </Label>
              <Input
                id="mc-name"
                placeholder="e.g. Claude Haiku"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-model-name">
                Model Name <span className="text-destructive">*</span>
              </Label>
              <Input
                id="mc-model-name"
                placeholder="e.g. gpt-4o, claude-sonnet-4-6"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-api-key">
                API Key <span className="text-destructive">*</span>
              </Label>
              <Input
                id="mc-api-key"
                type="password"
                placeholder={editing ? "Leave blank to keep existing key" : "sk-..."}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="new-password"
              />
              {editing && (
                <p className="text-xs text-muted-foreground">
                  Leave blank to keep the existing key.
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-base-url">
                Base URL{" "}
                <span className="text-xs font-normal text-muted-foreground">(optional, defaults to OpenAI)</span>
              </Label>
              <Input
                id="mc-base-url"
                placeholder="https://api.openai.com/v1"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-provider">
                Provider Tag{" "}
                <span className="text-xs font-normal text-muted-foreground">(optional, for display only)</span>
              </Label>
              <Input
                id="mc-provider"
                placeholder="e.g. anthropic, openai, deepseek"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="mc-max-output">
                  Max Output Tokens{" "}
                  <span className="text-xs font-normal text-muted-foreground">(optional)</span>
                </Label>
                <Input
                  id="mc-max-output"
                  type="number"
                  placeholder="default: env LLM_MAX_OUTPUT_TOKENS"
                  value={maxOutputTokens}
                  onChange={(e) => setMaxOutputTokens(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mc-context">
                  Context Size{" "}
                  <span className="text-xs font-normal text-muted-foreground">(optional, reserved)</span>
                </Label>
                <Input
                  id="mc-context"
                  type="number"
                  placeholder="default: env LLM_CONTEXT_SIZE"
                  value={contextSize}
                  onChange={(e) => setContextSize(e.target.value)}
                />
              </div>
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="mc-temperature">
                  Temperature{" "}
                  <span className="text-xs font-normal text-muted-foreground">
                    {temperature !== null ? `(${temperature.toFixed(1)})` : "(optional)"}
                  </span>
                </Label>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-6 text-xs text-muted-foreground"
                  onClick={() => setTemperature(null)}
                >
                  Reset
                </Button>
              </div>
              <Slider
                id="mc-temperature"
                min={0}
                max={2}
                step={0.1}
                value={[temperature ?? 0.7]}
                onValueChange={([v]) => setTemperature(v)}
                className="w-full"
              />
            </div>
          </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => handleClose(false)}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={saving}>
              {saving ? "Saving..." : editing ? "Save Changes" : "Add Provider"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={showCloseConfirm} onOpenChange={setShowCloseConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>
              You have unsaved input. Are you sure you want to close?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => onOpenChange(false)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Discard &amp; close
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// ─── Main ModelSettings ───────────────────────────────────────────────────────

export function ModelSettings() {
  const [models, setModels] = useState<ModelConfigResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ModelConfigResponse | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ModelConfigResponse | null>(null)
  const [assignRole, setAssignRole] = useState<"general" | "fast" | null>(null)

  const load = async () => {
    try {
      const data = await modelApi.list("llm")
      setModels(data)
    } catch {
      toast.error("Failed to load model configurations")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const generalModel = models.find((m) => m.role === "general")
  const fastModel = models.find((m) => m.role === "fast")

  const handleAssignRole = async (modelId: string) => {
    if (!assignRole) return
    try {
      await modelApi.setRole(modelId, assignRole)
      toast.success(`Model assigned as ${assignRole} model`)
      setAssignRole(null)
      load()
    } catch {
      toast.error("Failed to assign role")
    }
  }

  const handleClearRole = async (modelId: string) => {
    try {
      await modelApi.setRole(modelId, null)
      toast.success("Role cleared")
      load()
    } catch {
      toast.error("Failed to clear role")
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await modelApi.delete(deleteTarget.id)
      toast.success(`"${deleteTarget.name}" deleted`)
      setDeleteTarget(null)
      load()
    } catch {
      toast.error("Failed to delete model")
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">Model Providers</h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            Configure LLM providers. General model is used for reasoning and planning; Fast model for DAG step execution.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => {
            setEditing(null)
            setDialogOpen(true)
          }}
        >
          <Plus className="h-4 w-4 mr-1.5" />
          Add Provider
        </Button>
      </div>

      {/* Role Slots */}
      <div className="space-y-3">
        <RoleSlot
          role="general"
          active={generalModel}
          onAssign={setAssignRole}
          onClear={handleClearRole}
        />
        <RoleSlot
          role="fast"
          active={fastModel}
          onAssign={setAssignRole}
          onClear={handleClearRole}
        />
      </div>

      {/* Provider List */}
      {loading ? (
        <div className="text-sm text-muted-foreground">Loading...</div>
      ) : models.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center">
          <Bot className="h-8 w-8 mx-auto text-muted-foreground mb-2" />
          <p className="text-sm font-medium">No model providers configured</p>
          <p className="text-xs text-muted-foreground mt-1">
            Add a provider to override the environment variable defaults.
          </p>
          <Button
            size="sm"
            variant="outline"
            className="mt-4"
            onClick={() => {
              setEditing(null)
              setDialogOpen(true)
            }}
          >
            <Plus className="h-4 w-4 mr-1.5" />
            Add Provider
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Configured Providers
          </p>
          {models.map((model) => (
            <div
              key={model.id}
              className="flex items-center justify-between rounded-lg border bg-card px-4 py-3"
            >
              <div className="flex items-center gap-3 min-w-0">
                <Bot className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium">{model.name}</span>
                    {model.role && (
                      <Badge variant="secondary" className="text-xs gap-1">
                        {model.role === "general" ? (
                          <>
                            <Brain className="h-3 w-3" />
                            general
                          </>
                        ) : (
                          <>
                            <Zap className="h-3 w-3" />
                            fast
                          </>
                        )}
                      </Badge>
                    )}
                    {!model.is_active && (
                      <Badge variant="outline" className="text-xs text-muted-foreground">
                        Inactive
                      </Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground truncate">
                    {model.provider} · {model.model_name}
                    {model.base_url && ` · ${model.base_url}`}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0 ml-3">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => {
                    setEditing(model)
                    setDialogOpen(true)
                  }}
                >
                  <Edit2 className="h-3.5 w-3.5" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-destructive hover:text-destructive"
                  onClick={() => setDeleteTarget(model)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <ProviderDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        editing={editing}
        onSaved={load}
      />

      <AssignRoleDialog
        open={!!assignRole}
        role={assignRole}
        models={models}
        onAssign={handleAssignRole}
        onClose={() => setAssignRole(null)}
      />

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete model provider?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete &ldquo;{deleteTarget?.name}&rdquo;. This action cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
