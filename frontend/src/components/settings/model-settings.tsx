"use client"

import { useEffect, useState } from "react"
import { Bot, Brain, Edit2, Plus, Trash2, X, Zap } from "lucide-react"
import { toast } from "sonner"
import { useTranslations } from "next-intl"

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
  const t = useTranslations("settings.models")
  const isGeneral = role === "general"
  const Icon = isGeneral ? Brain : Zap
  const label = isGeneral ? t("generalModel") : t("fastModel")
  const envVar = isGeneral ? "LLM_MODEL" : "FAST_LLM_MODEL"
  const desc = isGeneral
    ? t("generalModelDesc")
    : t("fastModelDesc")

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
                {t("clear")}
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs shrink-0"
                onClick={() => onAssign(role)}
              >
                {t("assign")}
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
              <span className="text-xs">{t("envDefault", { envVar })}</span>
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
  const t = useTranslations("settings.models")
  const tc = useTranslations("common")
  const roleLabel = role === "general" ? t("generalModel") : t("fastModel")
  const available = models.filter((m) => m.role !== role)

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("assignDialogTitle", { role: roleLabel })}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2 py-2">
          {available.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              {t("noProvidersAvailable")}
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
            {tc("cancel")}
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
  const t = useTranslations("settings.models")
  const tc = useTranslations("common")

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
      toast.error(!name.trim() || !modelName.trim() ? t("nameAndModelRequired") : t("apiKeyRequired"))
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
        toast.success(t("modelUpdated"))
      } else {
        await modelApi.create(body)
        toast.success(t("modelAdded"))
      }
      onSaved()
      onOpenChange(false)
    } catch {
      toast.error(editing ? t("updateFailed") : t("addFailed"))
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
              {editing ? t("providerDialogEdit") : t("providerDialogAdd")}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto">
          <div className="space-y-4 py-2">
            {/* Compatibility notice */}
            <div
              className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground"
              dangerouslySetInnerHTML={{ __html: t("compatibilityNotice") }}
            />

            <div className="space-y-1.5">
              <Label htmlFor="mc-name">
                {t("nameLabel")} <span className="text-destructive">*</span>
              </Label>
              <Input
                id="mc-name"
                placeholder={t("namePlaceholder")}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-model-name">
                {t("modelNameLabel")} <span className="text-destructive">*</span>
              </Label>
              <Input
                id="mc-model-name"
                placeholder={t("modelNamePlaceholder")}
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-api-key">
                {t("apiKeyLabel")} <span className="text-destructive">*</span>
              </Label>
              <Input
                id="mc-api-key"
                type="password"
                placeholder={editing ? t("apiKeyEditPlaceholder") : t("apiKeyPlaceholder")}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="new-password"
              />
              {editing && (
                <p className="text-xs text-muted-foreground">
                  {t("apiKeyEditHint")}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-base-url">
                {t("baseUrlLabel")}{" "}
                <span className="text-xs font-normal text-muted-foreground">({t("baseUrlOptional")})</span>
              </Label>
              <Input
                id="mc-base-url"
                placeholder={t("baseUrlPlaceholder")}
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mc-provider">
                {t("providerTagLabel")}{" "}
                <span className="text-xs font-normal text-muted-foreground">({t("providerTagOptional")})</span>
              </Label>
              <Input
                id="mc-provider"
                placeholder={t("providerTagPlaceholder")}
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="mc-max-output">
                  {t("maxOutputTokensLabel")}{" "}
                  <span className="text-xs font-normal text-muted-foreground">({tc("optional")})</span>
                </Label>
                <Input
                  id="mc-max-output"
                  type="number"
                  placeholder={t("maxOutputTokensPlaceholder")}
                  value={maxOutputTokens}
                  onChange={(e) => setMaxOutputTokens(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mc-context">
                  {t("contextSizeLabel")}{" "}
                  <span className="text-xs font-normal text-muted-foreground">({t("contextSizeOptional")})</span>
                </Label>
                <Input
                  id="mc-context"
                  type="number"
                  placeholder={t("contextSizePlaceholder")}
                  value={contextSize}
                  onChange={(e) => setContextSize(e.target.value)}
                />
              </div>
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="mc-temperature">
                  {t("temperatureLabel")}{" "}
                  <span className="text-xs font-normal text-muted-foreground">
                    {temperature !== null ? t("temperatureValue", { value: temperature.toFixed(1) }) : `(${tc("optional")})`}
                  </span>
                </Label>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-6 text-xs text-muted-foreground"
                  onClick={() => setTemperature(null)}
                >
                  {tc("reset")}
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
              {tc("cancel")}
            </Button>
            <Button onClick={handleSubmit} disabled={saving}>
              {saving ? tc("saving") : editing ? t("saveChanges") : t("addProvider")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={showCloseConfirm} onOpenChange={setShowCloseConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("discardTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("discardDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("keepEditing")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => onOpenChange(false)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {t("discardConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// ─── Main ModelSettings ───────────────────────────────────────────────────────

export function ModelSettings() {
  const t = useTranslations("settings.models")
  const tc = useTranslations("common")

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
      toast.error(t("loadFailed"))
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
      const roleLabel = assignRole === "general" ? t("generalModel") : t("fastModel")
      toast.success(t("roleAssigned", { role: roleLabel }))
      setAssignRole(null)
      load()
    } catch {
      toast.error(t("roleAssignFailed"))
    }
  }

  const handleClearRole = async (modelId: string) => {
    try {
      await modelApi.setRole(modelId, null)
      toast.success(t("roleCleared"))
      load()
    } catch {
      toast.error(t("roleClearFailed"))
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await modelApi.delete(deleteTarget.id)
      toast.success(t("modelDeleted", { name: deleteTarget.name }))
      setDeleteTarget(null)
      load()
    } catch {
      toast.error(t("deleteFailed"))
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            {t("description")}
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
          {t("addProvider")}
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
        <div className="text-sm text-muted-foreground">{tc("loading")}</div>
      ) : models.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center">
          <Bot className="h-8 w-8 mx-auto text-muted-foreground mb-2" />
          <p className="text-sm font-medium">{t("noProviders")}</p>
          <p className="text-xs text-muted-foreground mt-1">
            {t("noProvidersHint")}
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
            {t("addProvider")}
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            {t("configuredProviders")}
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
                            {t("generalModel").toLowerCase()}
                          </>
                        ) : (
                          <>
                            <Zap className="h-3 w-3" />
                            {t("fastModel").toLowerCase()}
                          </>
                        )}
                      </Badge>
                    )}
                    {!model.is_active && (
                      <Badge variant="outline" className="text-xs text-muted-foreground">
                        {tc("inactive")}
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
            <AlertDialogTitle>{t("deleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteDescription", { name: deleteTarget?.name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
