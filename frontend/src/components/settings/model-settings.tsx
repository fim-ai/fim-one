"use client"

import { useEffect, useState } from "react"
import { Bot, Check, Edit2, Plus, Star, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { modelApi } from "@/lib/api"
import type { ModelConfigCreate, ModelConfigResponse, ModelConfigUpdate } from "@/types/model_config"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"

// --- Provider Form Dialog ---

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
  const [isDefault, setIsDefault] = useState(false)
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
      setIsDefault(editing.is_default)
    } else {
      setName("")
      setProvider("")
      setBaseUrl("")
      setApiKey("")
      setModelName("")
      setMaxOutputTokens("")
      setContextSize("")
      setTemperature(null)
      setIsDefault(false)
    }
    setShowCloseConfirm(false)
  }, [editing, open])

  const isDirty =
    name.trim().length > 0 ||
    provider.trim().length > 0 ||
    modelName.trim().length > 0 ||
    apiKey.trim().length > 0

  const handleClose = (open: boolean) => {
    if (!open && isDirty && !editing) {
      setShowCloseConfirm(true)
      return
    }
    onOpenChange(open)
  }

  const handleSubmit = async () => {
    if (!name.trim() || !provider.trim() || !modelName.trim()) {
      toast.error("Name, Provider, and Model Name are required")
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
        is_default: isDefault,
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
    } catch (err) {
      toast.error(editing ? "Failed to update model" : "Failed to add model provider")
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={handleClose}>
        <DialogContent
          className="max-w-lg max-h-[90vh] overflow-y-auto"
          onInteractOutside={(e) => {
            if (isDirty && !editing) {
              e.preventDefault()
              setShowCloseConfirm(true)
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>{editing ? "Edit Model Provider" : "Add Model Provider"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="mc-name">Name</Label>
              <Input
                id="mc-name"
                placeholder="e.g. Claude Haiku"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-provider">Provider</Label>
              <Input
                id="mc-provider"
                placeholder="e.g. anthropic, openai, deepseek"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-base-url">Base URL</Label>
              <Input
                id="mc-base-url"
                placeholder="https://api.openai.com/v1"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-api-key">API Key</Label>
              <Input
                id="mc-api-key"
                type="password"
                placeholder={editing ? "Leave blank to keep existing key" : "sk-..."}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="new-password"
              />
              {editing && (
                <p className="text-xs text-muted-foreground">Key is stored securely. Leave blank to keep the existing key.</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-model-name">Model Name</Label>
              <Input
                id="mc-model-name"
                placeholder="e.g. gpt-4o, claude-sonnet-4-6"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="mc-max-output">Max Output Tokens</Label>
                <Input
                  id="mc-max-output"
                  type="number"
                  placeholder="e.g. 64000"
                  value={maxOutputTokens}
                  onChange={(e) => setMaxOutputTokens(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mc-context">Context Size</Label>
                <Input
                  id="mc-context"
                  type="number"
                  placeholder="e.g. 128000"
                  value={contextSize}
                  onChange={(e) => setContextSize(e.target.value)}
                />
              </div>
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="mc-temperature">
                  Temperature
                  {temperature !== null ? ` (${temperature.toFixed(1)})` : " (default)"}
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
            <div className="flex items-center justify-between rounded-md border px-3 py-2.5">
              <div>
                <p className="text-sm font-medium">Set as default</p>
                <p className="text-xs text-muted-foreground">Use this model for all agents by default</p>
              </div>
              <Switch
                checked={isDefault}
                onCheckedChange={setIsDefault}
              />
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
              Discard & close
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// --- Main ModelSettings Component ---

export function ModelSettings() {
  const [models, setModels] = useState<ModelConfigResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ModelConfigResponse | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ModelConfigResponse | null>(null)

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

  const handleSetDefault = async (model: ModelConfigResponse) => {
    try {
      await modelApi.setDefault(model.id)
      toast.success(`"${model.name}" is now the default model`)
      load()
    } catch {
      toast.error("Failed to set default model")
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

  const openAdd = () => {
    setEditing(null)
    setDialogOpen(true)
  }

  const openEdit = (model: ModelConfigResponse) => {
    setEditing(model)
    setDialogOpen(true)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">Model Providers</h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            Configure LLM providers. The default model is used by all agents unless overridden.
          </p>
        </div>
        <Button size="sm" onClick={openAdd}>
          <Plus className="h-4 w-4 mr-1.5" />
          Add Provider
        </Button>
      </div>

      {loading ? (
        <div className="text-sm text-muted-foreground">Loading...</div>
      ) : models.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center">
          <Bot className="h-8 w-8 mx-auto text-muted-foreground mb-2" />
          <p className="text-sm font-medium">No model providers configured</p>
          <p className="text-xs text-muted-foreground mt-1">
            Add a provider to use a custom LLM. Falls back to environment variables when none is set.
          </p>
          <Button size="sm" variant="outline" className="mt-4" onClick={openAdd}>
            <Plus className="h-4 w-4 mr-1.5" />
            Add Provider
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {models.map((model) => (
            <div
              key={model.id}
              className="flex items-center justify-between rounded-lg border bg-card px-4 py-3"
            >
              <div className="flex items-center gap-3 min-w-0">
                <Bot className="h-5 w-5 text-muted-foreground shrink-0" />
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium">{model.name}</span>
                    {model.is_default && (
                      <Badge variant="secondary" className="text-xs gap-1">
                        <Star className="h-3 w-3" />
                        Default
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
                {!model.is_default && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs gap-1"
                    onClick={() => handleSetDefault(model)}
                  >
                    <Check className="h-3 w-3" />
                    Set default
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => openEdit(model)}
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

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete model provider?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete &ldquo;{deleteTarget?.name}&rdquo;. This action cannot be undone.
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
