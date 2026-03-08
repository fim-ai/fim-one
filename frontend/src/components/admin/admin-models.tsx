"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Plus, Pencil, Trash2, Loader2, Brain, Zap, Info, Bot, Power, MoreHorizontal } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Slider } from "@/components/ui/slider"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import type { ModelConfigResponse } from "@/types/model_config"
import type { EnvFallbackInfo } from "@/types/admin"

// ---- Role Slot Card ----

interface RoleSlotProps {
  role: "general" | "fast"
  model: ModelConfigResponse | undefined
  envFallback: EnvFallbackInfo | null
}

function RoleSlotCard({ role, model, envFallback }: RoleSlotProps) {
  const t = useTranslations("admin.models")
  const isGeneral = role === "general"
  const Icon = isGeneral ? Brain : Zap
  const label = isGeneral ? t("generalModel") : t("fastModel")
  const desc = isGeneral ? t("generalModelDesc") : t("fastModelDesc")
  const envModel = isGeneral
    ? envFallback?.llm_model
    : envFallback?.fast_llm_model

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
          </div>
          {model ? (
            <div className="mt-2 flex items-center gap-2 rounded-md bg-muted/50 px-3 py-2">
              <Bot className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0">
                <span className="text-sm font-medium">{model.name}</span>
                <span className="text-xs text-muted-foreground ml-2">
                  {model.provider} · {model.model_name}
                </span>
              </div>
              {!model.is_active && (
                <Badge variant="outline" className="text-xs text-muted-foreground ml-auto">
                  {t("inactive")}
                </Badge>
              )}
            </div>
          ) : (
            <div className="mt-2 flex items-center gap-2 rounded-md border border-dashed px-3 py-2 text-muted-foreground">
              <span className="text-xs">{t("usingEnv", { model: envModel ?? "gpt-4o" })}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ---- Model Form Dialog ----

interface ModelFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  model?: ModelConfigResponse | null
  onSuccess: () => void
}

function ModelFormDialog({ open, onOpenChange, model, onSuccess }: ModelFormDialogProps) {
  const isEdit = !!model
  const t = useTranslations("admin.models")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [name, setName] = useState("")
  const [provider, setProvider] = useState("")
  const [modelName, setModelName] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [temperature, setTemperature] = useState<number | null>(null)
  const [maxOutputTokens, setMaxOutputTokens] = useState("")
  const [contextSize, setContextSize] = useState("")
  const [isSaving, setIsSaving] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<{ name?: string; modelName?: string; apiKey?: string }>({})

  useEffect(() => {
    if (open) {
      if (model) {
        setName(model.name)
        setProvider(model.provider)
        setModelName(model.model_name)
        setBaseUrl(model.base_url ?? "")
        setApiKey("")
        setTemperature(model.temperature)
        setMaxOutputTokens(model.max_output_tokens?.toString() ?? "")
        setContextSize(model.context_size?.toString() ?? "")
      } else {
        setName("")
        setProvider("")
        setModelName("")
        setBaseUrl("")
        setApiKey("")
        setTemperature(null)
        setMaxOutputTokens("")
        setContextSize("")
      }
      setShowCloseConfirm(false)
      setFieldErrors({})
    }
  }, [open, model])

  const isDirty =
    !isEdit &&
    (name.trim().length > 0 ||
      provider.trim().length > 0 ||
      modelName.trim().length > 0 ||
      apiKey.trim().length > 0)

  const handleClose = (nextOpen: boolean) => {
    if (!nextOpen && isDirty) {
      setShowCloseConfirm(true)
      return
    }
    onOpenChange(nextOpen)
  }

  const handleSubmit = async () => {
    const errors: { name?: string; modelName?: string; apiKey?: string } = {}
    if (!name.trim()) errors.name = t("nameRequired")
    if (!modelName.trim()) errors.modelName = t("modelNameRequired")
    if (!isEdit && !apiKey.trim()) errors.apiKey = t("apiKeyRequired")
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setIsSaving(true)
    try {
      const body = {
        name: name.trim(),
        provider: provider.trim(),
        model_name: modelName.trim(),
        base_url: baseUrl.trim() || null,
        api_key: apiKey.trim() || null,
        category: "llm" as const,
        temperature,
        max_output_tokens: maxOutputTokens ? parseInt(maxOutputTokens) : null,
        context_size: contextSize ? parseInt(contextSize) : null,
      }
      if (isEdit && model) {
        const { api_key: _ak, ...rest } = body
        const updateBody = apiKey.trim() ? body : rest
        await adminApi.updateModel(model.id, updateBody)
        toast.success(t("updated"))
      } else {
        await adminApi.createModel(body)
        toast.success(t("created"))
      }
      onSuccess()
      onOpenChange(false)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSaving(false)
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
              {isEdit ? t("editModel") : t("addModel")}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto">
            <div className="space-y-4 py-2">
              <div className="space-y-1.5">
                <Label htmlFor="am-name">
                  {t("name")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="am-name"
                  placeholder="e.g. GPT-4o Production"
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value)
                    if (fieldErrors.name) setFieldErrors((prev) => ({ ...prev, name: undefined }))
                  }}
                  aria-invalid={!!fieldErrors.name}
                />
                {fieldErrors.name && (
                  <p className="text-xs text-destructive">{fieldErrors.name}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="am-model-name">
                  {t("modelName")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="am-model-name"
                  placeholder="e.g. gpt-4o"
                  value={modelName}
                  onChange={(e) => {
                    setModelName(e.target.value)
                    if (fieldErrors.modelName) setFieldErrors((prev) => ({ ...prev, modelName: undefined }))
                  }}
                  aria-invalid={!!fieldErrors.modelName}
                />
                {fieldErrors.modelName && (
                  <p className="text-xs text-destructive">{fieldErrors.modelName}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="am-api-key">
                  {t("apiKey")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="am-api-key"
                  type="password"
                  placeholder={isEdit ? t("apiKeyEditHint") : t("apiKeyPlaceholder")}
                  value={apiKey}
                  onChange={(e) => {
                    setApiKey(e.target.value)
                    if (fieldErrors.apiKey) setFieldErrors((prev) => ({ ...prev, apiKey: undefined }))
                  }}
                  autoComplete="new-password"
                  aria-invalid={!!fieldErrors.apiKey}
                />
                {fieldErrors.apiKey ? (
                  <p className="text-xs text-destructive">{fieldErrors.apiKey}</p>
                ) : isEdit ? (
                  <p className="text-xs text-muted-foreground">
                    {t("apiKeyEditHint")}
                  </p>
                ) : null}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="am-base-url">
                  {t("baseUrl")}{" "}
                  <span className="text-xs font-normal text-muted-foreground">({tc("optional")})</span>
                </Label>
                <Input
                  id="am-base-url"
                  placeholder="https://api.openai.com/v1"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="am-provider">
                  {t("provider")}{" "}
                  <span className="text-xs font-normal text-muted-foreground">({tc("optional")})</span>
                </Label>
                <Input
                  id="am-provider"
                  placeholder="e.g. OpenAI, Anthropic, DeepSeek"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="am-max-output">
                    {t("maxOutputTokens")}{" "}
                    <span className="text-xs font-normal text-muted-foreground">({tc("optional")})</span>
                  </Label>
                  <Input
                    id="am-max-output"
                    type="number"
                    placeholder="64000"
                    value={maxOutputTokens}
                    onChange={(e) => setMaxOutputTokens(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="am-context">
                    {t("contextSize")}{" "}
                    <span className="text-xs font-normal text-muted-foreground">({tc("optional")})</span>
                  </Label>
                  <Input
                    id="am-context"
                    type="number"
                    placeholder="128000"
                    value={contextSize}
                    onChange={(e) => setContextSize(e.target.value)}
                  />
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="am-temperature">
                    {t("temperature")}{" "}
                    <span className="text-xs font-normal text-muted-foreground">
                      {temperature !== null ? temperature.toFixed(1) : `(${tc("optional")})`}
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
                  id="am-temperature"
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
            <Button onClick={handleSubmit} disabled={isSaving}>
              {isSaving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
              {isEdit ? tc("save") : tc("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={showCloseConfirm} onOpenChange={setShowCloseConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{tc("unsavedChangesTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {tc("unsavedChanges")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("keepEditing")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => onOpenChange(false)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {tc("discardChanges")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// ---- Main AdminModels Component ----

export function AdminModels() {
  const t = useTranslations("admin.models")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [models, setModels] = useState<ModelConfigResponse[]>([])
  const [envFallback, setEnvFallback] = useState<EnvFallbackInfo | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [editTarget, setEditTarget] = useState<ModelConfigResponse | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ModelConfigResponse | null>(null)

  const load = async () => {
    setIsLoading(true)
    try {
      const data = await adminApi.listModels()
      setModels(data.models)
      setEnvFallback(data.env_fallback)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [])

  const generalModel = models.find((m) => m.role === "general")
  const fastModel = models.find((m) => m.role === "fast")

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await adminApi.deleteModel(deleteTarget.id)
      toast.success(t("deleted"))
      setDeleteTarget(null)
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const handleToggleActive = async (model: ModelConfigResponse) => {
    try {
      await adminApi.toggleModelActive(model.id, !model.is_active)
      toast.success(t("statusUpdated"))
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const handleSetRole = async (model: ModelConfigResponse, role: string | null) => {
    try {
      await adminApi.setModelRole(model.id, role)
      toast.success(t("roleUpdated"))
      load()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("description")}</p>
        </div>
        <Button size="sm" onClick={() => setShowCreate(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("addModel")}
        </Button>
      </div>

      {/* Resolution chain banner */}
      <div className="flex items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        <Info className="h-3.5 w-3.5 shrink-0" />
        {t("resolutionChain")}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          {/* Role slots */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <RoleSlotCard role="general" model={generalModel} envFallback={envFallback} />
            <RoleSlotCard role="fast" model={fastModel} envFallback={envFallback} />
          </div>

          {/* Model table */}
          {models.length === 0 ? (
            <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              {t("noModels")}
            </div>
          ) : (
            <div className="rounded-md border border-border overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40">
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("name")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("provider")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("modelName")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("role")}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{tc("status")}</th>
                    <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{tc("actions")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {models.map((m) => (
                    <tr key={m.id} className="hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3">
                        <p className="font-medium text-foreground">{m.name}</p>
                        {m.base_url && (
                          <p className="text-xs text-muted-foreground truncate max-w-[200px]">{m.base_url}</p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{m.provider || "-"}</td>
                      <td className="px-4 py-3 font-mono text-xs">{m.model_name}</td>
                      <td className="px-4 py-3">
                        {m.role === "general" ? (
                          <Badge variant="secondary" className="gap-1">
                            <Brain className="h-3 w-3" />
                            {t("roleGeneral")}
                          </Badge>
                        ) : m.role === "fast" ? (
                          <Badge variant="secondary" className="gap-1">
                            <Zap className="h-3 w-3" />
                            {t("roleFast")}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground text-xs">-</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={m.is_active ? "default" : "secondary"}>
                          {m.is_active ? t("active") : t("inactive")}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            {/* Edit */}
                            <DropdownMenuItem onClick={() => setEditTarget(m)}>
                              <Pencil className="mr-2 h-4 w-4" />
                              {tc("edit")}
                            </DropdownMenuItem>
                            {/* Enable/Disable */}
                            <DropdownMenuItem onClick={() => handleToggleActive(m)}>
                              <Power className="mr-2 h-4 w-4" />
                              {m.is_active ? tc("disable") : tc("enable")}
                            </DropdownMenuItem>
                            {/* Set Role items */}
                            <DropdownMenuSeparator />
                            <DropdownMenuItem onClick={() => handleSetRole(m, "general")} disabled={m.role === "general"}>
                              <Brain className="mr-2 h-3.5 w-3.5" />
                              {t("roleGeneral")}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleSetRole(m, "fast")} disabled={m.role === "fast"}>
                              <Zap className="mr-2 h-3.5 w-3.5" />
                              {t("roleFast")}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleSetRole(m, null)} disabled={!m.role}>
                              {t("clearRole")}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            {/* Delete */}
                            <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(m)}>
                              <Trash2 className="mr-2 h-4 w-4" />
                              {tc("delete")}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* ENV fallback info */}
          {envFallback && (
            <div className="rounded-md border border-dashed p-4 space-y-2">
              <div className="flex items-center gap-2">
                <Info className="h-4 w-4 text-muted-foreground" />
                <p className="text-sm font-medium text-muted-foreground">{t("envFallback")}</p>
              </div>
              <p className="text-xs text-muted-foreground">{t("envFallbackDescription")}</p>
              <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-muted-foreground mt-1">
                <div>LLM_MODEL: <span className="font-mono">{envFallback.llm_model}</span></div>
                <div>FAST_LLM_MODEL: <span className="font-mono">{envFallback.fast_llm_model}</span></div>
                <div>LLM_BASE_URL: <span className="font-mono truncate">{envFallback.llm_base_url}</span></div>
                <div>LLM_API_KEY: <span className="font-mono">{envFallback.has_api_key ? "***" : "(not set)"}</span></div>
              </div>
            </div>
          )}
        </>
      )}

      {/* Delete confirm */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteModel")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteConfirm", { name: deleteTarget?.name ?? "" })}
              <br />
              <span className="text-xs">{t("deleteWarning")}</span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Create dialog */}
      <ModelFormDialog
        open={showCreate}
        onOpenChange={setShowCreate}
        onSuccess={() => { setShowCreate(false); load() }}
      />

      {/* Edit dialog */}
      <ModelFormDialog
        open={!!editTarget}
        onOpenChange={(open) => { if (!open) setEditTarget(null) }}
        model={editTarget}
        onSuccess={() => { setEditTarget(null); load() }}
      />
    </div>
  )
}
