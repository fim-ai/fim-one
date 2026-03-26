"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations } from "next-intl"
import {
  Plus,
  Pencil,
  Trash2,
  Loader2,
  Brain,
  Zap,
  Lightbulb,
  Info,
  Power,
  MoreHorizontal,
  ChevronDown,
  ChevronRight,
  Globe,
  Server,
  Layers,
  CircleDot,
  Circle,
  AlertTriangle,
  Download,
  Upload,
  Check,
  ChevronsUpDown,
  HelpCircle,
} from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { cn } from "@/lib/utils"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import type {
  ModelProviderResponse,
  ModelProviderModelResponse,
  ModelGroupResponse,
  ModelActiveConfig,
  EnvFallbackInfo,
  ModelSlotInfo,
} from "@/types/model_provider"

// ============================================================
// Effective Model Slot Card (used in Active Configuration)
// ============================================================

interface EffectiveSlotProps {
  role: "general" | "fast" | "reasoning"
  modelName: string
  providerName: string | null
  source: "group" | "env"
}

function EffectiveSlotCard({ role, modelName, providerName, source }: EffectiveSlotProps) {
  const t = useTranslations("admin.models")
  const Icon = role === "general" ? Brain : role === "fast" ? Zap : Lightbulb
  const label = role === "general" ? t("generalModel") : role === "fast" ? t("fastModel") : t("reasoningModel")
  const colorClass = role === "general"
    ? "bg-blue-500/10 text-blue-500"
    : role === "fast"
      ? "bg-amber-500/10 text-amber-500"
      : "bg-purple-500/10 text-purple-500"

  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="flex items-center gap-2 mb-2">
        <div className={`rounded-md p-1 ${colorClass}`}>
          <Icon className="h-3.5 w-3.5" />
        </div>
        <span className="text-xs font-medium">{label}</span>
      </div>
      <p className="text-sm font-medium truncate">{modelName}</p>
      <div className="flex items-center gap-1.5 mt-1">
        {providerName && (
          <span className="text-xs text-muted-foreground truncate">{providerName}</span>
        )}
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
          {source === "env" ? t("sourceEnv") : t("sourceGroup")}
        </Badge>
      </div>
    </div>
  )
}

// ============================================================
// Provider Form Dialog
// ============================================================

const PROVIDER_PRESETS = [
  { id: "openai", name: "OpenAI", baseUrl: "https://api.openai.com/v1" },
  { id: "anthropic", name: "Anthropic (Claude)", baseUrl: "https://api.anthropic.com/v1" },
  { id: "gemini", name: "Google Gemini", baseUrl: "https://generativelanguage.googleapis.com/v1beta" },
  { id: "deepseek", name: "DeepSeek", baseUrl: "https://api.deepseek.com/v1" },
  { id: "mistral", name: "Mistral AI", baseUrl: "https://api.mistral.ai/v1" },
  { id: "xai", name: "xAI (Grok)", baseUrl: "https://api.x.ai/v1" },
  { id: "openrouter", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1" },
  { id: "ollama", name: "Ollama", baseUrl: "http://localhost:11434/v1" },
  { id: "qwen", name: "Qwen China", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { id: "qwen-intl", name: "Qwen Global", baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" },
  { id: "glm", name: "GLM China", baseUrl: "https://open.bigmodel.cn/api/paas/v4" },
  { id: "glm-intl", name: "GLM Global", baseUrl: "https://api.z.ai/api/paas/v4" },
  { id: "moonshot", name: "Moonshot China", baseUrl: "https://api.moonshot.cn/v1" },
  { id: "moonshot-intl", name: "Moonshot Global", baseUrl: "https://api.moonshot.ai/v1" },
  { id: "minimax", name: "MiniMax Global", baseUrl: "https://api.minimax.io/v1" },
  { id: "minimax-cn", name: "MiniMax China", baseUrl: "https://api.minimaxi.com/v1" },
  { id: "openai-compatible", name: null, baseUrl: null },
] as const

interface ProviderFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  provider?: ModelProviderResponse | null
  onSuccess: () => void
}

function ProviderFormDialog({ open, onOpenChange, provider, onSuccess }: ProviderFormDialogProps) {
  const isEdit = !!provider
  const t = useTranslations("admin.models")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [preset, setPreset] = useState<string>("openai-compatible")
  const [name, setName] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [isActive, setIsActive] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<{ name?: string; baseUrl?: string; apiKey?: string }>({})

  useEffect(() => {
    if (open) {
      if (provider) {
        setName(provider.name)
        setBaseUrl(provider.base_url ?? "")
        setApiKey("")
        setIsActive(provider.is_active)
      } else {
        setPreset("openai-compatible")
        setName("")
        setBaseUrl("")
        setApiKey("")
        setIsActive(true)
      }
      setShowCloseConfirm(false)
      setFieldErrors({})
    }
  }, [open, provider])

  const isDirty =
    !isEdit &&
    (name.trim().length > 0 || baseUrl.trim().length > 0 || apiKey.trim().length > 0)

  const handleClose = (nextOpen: boolean) => {
    if (!nextOpen && isDirty) {
      setShowCloseConfirm(true)
      return
    }
    onOpenChange(nextOpen)
  }

  const apiKeyRequired = !isEdit && preset !== "ollama"

  const handleSubmit = async () => {
    const errors: { name?: string; baseUrl?: string; apiKey?: string } = {}
    if (!name.trim()) errors.name = t("providerNameRequired")
    if (!baseUrl.trim()) {
      errors.baseUrl = t("baseUrlRequired")
    } else {
      try {
        const u = new URL(baseUrl.trim())
        if (u.protocol !== "http:" && u.protocol !== "https:") errors.baseUrl = t("baseUrlInvalid")
      } catch {
        errors.baseUrl = t("baseUrlInvalid")
      }
    }
    if (apiKeyRequired && !apiKey.trim()) errors.apiKey = t("apiKeyRequired")
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setIsSaving(true)
    try {
      if (isEdit && provider) {
        const body: Record<string, unknown> = {
          name: name.trim(),
          base_url: baseUrl.trim() || undefined,
          is_active: isActive,
        }
        if (apiKey.trim()) body.api_key = apiKey.trim()
        await adminApi.updateModelProvider(provider.id, body)
        toast.success(t("providerUpdated"))
      } else {
        await adminApi.createModelProvider({
          name: name.trim(),
          base_url: baseUrl.trim() || undefined,
          api_key: apiKey.trim() || undefined,
          is_active: isActive,
        })
        toast.success(t("providerCreated"))
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
            <DialogTitle>{isEdit ? t("editProvider") : t("addProvider")}</DialogTitle>
            {!isEdit && (
              <p className="text-sm text-muted-foreground">
                {t.rich("addProviderHint", {
                  link: (chunks) => (
                    <a
                      href="https://docs.fim.ai/architecture/llm-provider-guide"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary underline underline-offset-4 hover:text-primary/80"
                    >
                      {chunks}
                    </a>
                  ),
                })}
              </p>
            )}
          </DialogHeader>
          <div className="flex-1 overflow-y-auto">
            <div className="space-y-4 py-2">
              {!isEdit && (
                <div className="space-y-1.5">
                  <Label>{t("providerPreset")}</Label>
                  <div className="flex flex-wrap gap-2">
                    {PROVIDER_PRESETS.map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        className={cn(
                          "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                          preset === p.id
                            ? "border-transparent bg-primary text-primary-foreground"
                            : "border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                        )}
                        onClick={() => {
                          setPreset(p.id)
                          if (p.name) {
                            setName(p.name)
                            setBaseUrl(p.baseUrl!)
                          } else {
                            setName("")
                            setBaseUrl("")
                          }
                          setFieldErrors({})
                        }}
                      >
                        {p.name ?? t("presetCustom")}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="pf-name">
                  {t("providerName")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="pf-name"
                  placeholder={t("providerNamePlaceholder")}
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value)
                    if (fieldErrors.name) setFieldErrors((prev) => ({ ...prev, name: undefined }))
                  }}
                  aria-invalid={!!fieldErrors.name}
                />
                {fieldErrors.name && (
                  <p className="text-sm text-destructive">{fieldErrors.name}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pf-base-url">
                  {t("baseUrl")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="pf-base-url"
                  placeholder={t("baseUrlPlaceholder")}
                  value={baseUrl}
                  onChange={(e) => {
                    setBaseUrl(e.target.value)
                    if (fieldErrors.baseUrl) setFieldErrors((prev) => ({ ...prev, baseUrl: undefined }))
                  }}
                  aria-invalid={!!fieldErrors.baseUrl}
                />
                <p className="text-xs text-muted-foreground">{t("baseUrlHint")}</p>
                {fieldErrors.baseUrl && (
                  <p className="text-sm text-destructive">{fieldErrors.baseUrl}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pf-api-key">
                  {t("apiKey")} {apiKeyRequired && <span className="text-destructive">*</span>}
                </Label>
                <Input
                  id="pf-api-key"
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
                {isEdit && (
                  <p className="text-xs text-muted-foreground">{t("apiKeyEditHint")}</p>
                )}
                {fieldErrors.apiKey && (
                  <p className="text-sm text-destructive">{fieldErrors.apiKey}</p>
                )}
              </div>
              <div className="flex items-center justify-between gap-3 rounded-md border p-3">
                <div className="space-y-0.5 flex-1">
                  <Label htmlFor="pf-active" className="cursor-pointer">{t("active")}</Label>
                </div>
                <Switch
                  id="pf-active"
                  checked={isActive}
                  onCheckedChange={setIsActive}
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
            <AlertDialogDescription>{tc("unsavedChanges")}</AlertDialogDescription>
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

// ============================================================
// Model Form Dialog (under a provider)
// ============================================================

interface ModelFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  providerId: string
  model?: ModelProviderModelResponse | null
  onSuccess: () => void
}

function ModelFormDialog({ open, onOpenChange, providerId, model, onSuccess }: ModelFormDialogProps) {
  const isEdit = !!model
  const t = useTranslations("admin.models")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [displayName, setDisplayName] = useState("")
  const [modelName, setModelName] = useState("")
  const [temperature, setTemperature] = useState<number | null>(null)
  const [maxOutputTokens, setMaxOutputTokens] = useState("")
  const [contextSize, setContextSize] = useState("")
  const [jsonModeEnabled, setJsonModeEnabled] = useState(true)
  const [toolChoiceEnabled, setToolChoiceEnabled] = useState(true)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<{ displayName?: string; modelName?: string }>({})

  useEffect(() => {
    if (open) {
      if (model) {
        setDisplayName(model.name)
        setModelName(model.model_name)
        setTemperature(model.temperature)
        setMaxOutputTokens(model.max_output_tokens ? (model.max_output_tokens / 1000).toString() : "")
        setContextSize(model.context_size ? (model.context_size / 1000).toString() : "")
        setJsonModeEnabled(model.json_mode_enabled)
        setToolChoiceEnabled(model.tool_choice_enabled)
      } else {
        setDisplayName("")
        setModelName("")
        setTemperature(null)
        setMaxOutputTokens("")
        setContextSize("")
        setJsonModeEnabled(true)
        setToolChoiceEnabled(true)
      }
      setShowAdvanced(false)
      setShowCloseConfirm(false)
      setFieldErrors({})
    }
  }, [open, model])

  const isDirty =
    !isEdit &&
    (displayName.trim().length > 0 || modelName.trim().length > 0)

  const handleClose = (nextOpen: boolean) => {
    if (!nextOpen && isDirty) {
      setShowCloseConfirm(true)
      return
    }
    onOpenChange(nextOpen)
  }

  const handleSubmit = async () => {
    const errors: { displayName?: string; modelName?: string } = {}
    if (!displayName.trim()) errors.displayName = t("nameRequired")
    if (!modelName.trim()) errors.modelName = t("modelNameRequired")
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setIsSaving(true)
    try {
      const body = {
        name: displayName.trim(),
        model_name: modelName.trim(),
        temperature: temperature ?? undefined,
        max_output_tokens: maxOutputTokens ? parseInt(maxOutputTokens) * 1000 : undefined,
        context_size: contextSize ? parseInt(contextSize) * 1000 : undefined,
        json_mode_enabled: jsonModeEnabled,
        tool_choice_enabled: toolChoiceEnabled,
      }
      if (isEdit && model) {
        await adminApi.updateProviderModel(model.id, body)
        toast.success(t("modelUpdated"))
      } else {
        await adminApi.createProviderModel(providerId, body)
        toast.success(t("modelCreated"))
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
            <DialogTitle>{isEdit ? t("editModelUnder") : t("addModelUnder")}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto">
            <div className="space-y-4 py-2">
              <div className="space-y-1.5">
                <Label htmlFor="mf-display-name">
                  {t("modelDisplayName")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="mf-display-name"
                  placeholder={t("modelDisplayNamePlaceholder")}
                  value={displayName}
                  onChange={(e) => {
                    setDisplayName(e.target.value)
                    if (fieldErrors.displayName) setFieldErrors((prev) => ({ ...prev, displayName: undefined }))
                  }}
                  aria-invalid={!!fieldErrors.displayName}
                />
                {fieldErrors.displayName && (
                  <p className="text-sm text-destructive">{fieldErrors.displayName}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mf-model-name">
                  {t("modelApiName")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="mf-model-name"
                  placeholder={t("modelApiNamePlaceholder")}
                  value={modelName}
                  onChange={(e) => {
                    setModelName(e.target.value)
                    if (fieldErrors.modelName) setFieldErrors((prev) => ({ ...prev, modelName: undefined }))
                  }}
                  className="font-mono"
                  aria-invalid={!!fieldErrors.modelName}
                />
                {fieldErrors.modelName && (
                  <p className="text-sm text-destructive">{fieldErrors.modelName}</p>
                )}
              </div>

              {/* Collapsible advanced section */}
              <div>
                <button
                  type="button"
                  onClick={() => setShowAdvanced((v) => !v)}
                  className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors w-full py-1"
                >
                  {showAdvanced ? (
                    <ChevronDown className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5" />
                  )}
                  {tc("advanced")}
                </button>
                {showAdvanced && (
                  <div className="mt-3 space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label htmlFor="mf-context">{t("contextSize")}</Label>
                        <div className="relative">
                          <Input
                            id="mf-context"
                            type="number"
                            placeholder="128"
                            className="pr-8"
                            value={contextSize}
                            onChange={(e) => setContextSize(e.target.value)}
                          />
                          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">K</span>
                        </div>
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="mf-max-output">{t("maxOutputTokens")}</Label>
                        <div className="relative">
                          <Input
                            id="mf-max-output"
                            type="number"
                            placeholder="64"
                            className="pr-8"
                            value={maxOutputTokens}
                            onChange={(e) => setMaxOutputTokens(e.target.value)}
                          />
                          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">K</span>
                        </div>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-1.5">
                          <Label htmlFor="mf-temperature">
                            {t("temperature")}{" "}
                            <span className="text-xs font-normal text-muted-foreground">
                              {temperature !== null ? temperature.toFixed(1) : ""}
                            </span>
                          </Label>
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <HelpCircle className="h-3.5 w-3.5 text-muted-foreground cursor-help" />
                              </TooltipTrigger>
                              <TooltipContent side="right" className="max-w-xs">
                                <p className="text-xs">{t("temperatureReasoningHint")}</p>
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        </div>
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
                        id="mf-temperature"
                        min={0}
                        max={2}
                        step={0.1}
                        value={[temperature ?? 0.7]}
                        onValueChange={([v]) => setTemperature(v)}
                        className="w-full"
                      />
                    </div>
                    {/* Native Function Calling toggle */}
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <Label htmlFor="tool-choice" className="text-sm font-medium">
                          {t("toolChoiceEnabled")}
                        </Label>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <HelpCircle className="h-3.5 w-3.5 text-muted-foreground cursor-help" />
                            </TooltipTrigger>
                            <TooltipContent side="right" className="max-w-xs">
                              <p className="text-xs">{t("toolChoiceEnabledDesc")}</p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                      <Switch
                        id="tool-choice"
                        checked={toolChoiceEnabled}
                        onCheckedChange={setToolChoiceEnabled}
                      />
                    </div>
                    {/* JSON Mode toggle */}
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <Label htmlFor="json-mode" className="text-sm font-medium">
                          {t("jsonModeEnabled")}
                        </Label>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <HelpCircle className="h-3.5 w-3.5 text-muted-foreground cursor-help" />
                            </TooltipTrigger>
                            <TooltipContent side="right" className="max-w-xs">
                              <p className="text-xs">{t("jsonModeEnabledDesc")}</p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                      <Switch
                        id="json-mode"
                        checked={jsonModeEnabled}
                        onCheckedChange={setJsonModeEnabled}
                      />
                    </div>
                  </div>
                )}
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
            <AlertDialogDescription>{tc("unsavedChanges")}</AlertDialogDescription>
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

// ============================================================
// Group Form Dialog
// ============================================================

interface GroupFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  group?: ModelGroupResponse | null
  providers: ModelProviderResponse[]
  onSuccess: () => void
}

function GroupFormDialog({ open, onOpenChange, group, providers, onSuccess }: GroupFormDialogProps) {
  const isEdit = !!group
  const t = useTranslations("admin.models")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [generalModelId, setGeneralModelId] = useState<string>("__default__")
  const [fastModelId, setFastModelId] = useState<string>("__default__")
  const [reasoningModelId, setReasoningModelId] = useState<string>("__default__")
  const [isSaving, setIsSaving] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<{ name?: string }>({})
  const [openCombobox, setOpenCombobox] = useState<string | null>(null)

  // Build grouped model options: only active models from active providers
  const modelOptions = providers
    .filter((p) => p.is_active)
    .map((p) => ({
      providerName: p.name,
      models: p.models.filter((m) => m.is_active),
    }))
    .filter((g) => g.models.length > 0)

  useEffect(() => {
    if (open) {
      if (group) {
        setName(group.name)
        setDescription(group.description ?? "")
        setGeneralModelId(group.general_model_id ?? "__default__")
        setFastModelId(group.fast_model_id ?? "__default__")
        setReasoningModelId(group.reasoning_model_id ?? "__default__")
      } else {
        setName("")
        setDescription("")
        setGeneralModelId("__default__")
        setFastModelId("__default__")
        setReasoningModelId("__default__")
      }
      setShowCloseConfirm(false)
      setFieldErrors({})
    }
  }, [open, group])

  const isDirty = !isEdit && name.trim().length > 0

  const handleClose = (nextOpen: boolean) => {
    if (!nextOpen && isDirty) {
      setShowCloseConfirm(true)
      return
    }
    onOpenChange(nextOpen)
  }

  const handleSubmit = async () => {
    const errors: { name?: string } = {}
    if (!name.trim()) errors.name = t("groupNameRequired")
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setIsSaving(true)
    try {
      const body = {
        name: name.trim(),
        description: description.trim() || undefined,
        general_model_id: generalModelId === "__default__" ? undefined : generalModelId,
        fast_model_id: fastModelId === "__default__" ? undefined : fastModelId,
        reasoning_model_id: reasoningModelId === "__default__" ? undefined : reasoningModelId,
      }
      if (isEdit && group) {
        await adminApi.updateModelGroup(group.id, body)
        toast.success(t("groupUpdated"))
      } else {
        await adminApi.createModelGroup(body)
        toast.success(t("groupCreated"))
      }
      onSuccess()
      onOpenChange(false)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSaving(false)
    }
  }

  const getModelLabel = (modelId: string) => {
    if (modelId === "__default__") return null
    for (const group of modelOptions) {
      for (const m of group.models) {
        if (m.id === modelId) return `${m.name} (${m.model_name})`
      }
    }
    return null
  }

  const renderModelSelect = (
    label: string,
    value: string,
    onChange: (v: string) => void,
    id: string,
  ) => (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Popover modal open={openCombobox === id} onOpenChange={(open) => setOpenCombobox(open ? id : null)}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={openCombobox === id}
            className="w-full justify-between font-normal"
            id={id}
          >
            <span className="truncate">
              {getModelLabel(value) ?? t("selectModel")}
            </span>
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
          <Command>
            <CommandInput placeholder={tc("searchPlaceholder")} />
            <CommandList>
              <CommandEmpty>{tc("noResults")}</CommandEmpty>
              <CommandItem
                value="__clear__"
                onSelect={() => {
                  onChange("__default__")
                  setOpenCombobox(null)
                }}
              >
                <Check
                  className={cn(
                    "mr-2 h-4 w-4",
                    value === "__default__" ? "opacity-100" : "opacity-0"
                  )}
                />
                {t("clearSelection")}
              </CommandItem>
              {modelOptions.map((group) => (
                <CommandGroup key={group.providerName} heading={group.providerName}>
                  {group.models.map((m) => (
                    <CommandItem
                      key={m.id}
                      value={`${group.providerName} ${m.name} ${m.model_name}`}
                      onSelect={() => {
                        onChange(m.id)
                        setOpenCombobox(null)
                      }}
                    >
                      <Check
                        className={cn(
                          "mr-2 h-4 w-4",
                          value === m.id ? "opacity-100" : "opacity-0"
                        )}
                      />
                      <span>{m.name}</span>
                      <span className="text-xs text-muted-foreground ml-1">({m.model_name})</span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              ))}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  )

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
            <DialogTitle>{isEdit ? t("editGroup") : t("addGroup")}</DialogTitle>
            <DialogDescription>{t("groupsDesc")}</DialogDescription>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto">
            <div className="space-y-4 py-2">
              <div className="space-y-1.5">
                <Label htmlFor="gf-name">
                  {t("groupName")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="gf-name"
                  placeholder={t("groupNamePlaceholder")}
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value)
                    if (fieldErrors.name) setFieldErrors((prev) => ({ ...prev, name: undefined }))
                  }}
                  aria-invalid={!!fieldErrors.name}
                />
                {fieldErrors.name && (
                  <p className="text-sm text-destructive">{fieldErrors.name}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="gf-desc">{t("groupDescription")}</Label>
                <Input
                  id="gf-desc"
                  placeholder={t("groupDescriptionPlaceholder")}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              <div className="space-y-3 pt-2">
                {renderModelSelect(t("generalSlot"), generalModelId, setGeneralModelId, "gf-general")}
                {renderModelSelect(t("fastSlot"), fastModelId, setFastModelId, "gf-fast")}
                {renderModelSelect(t("reasoningSlot"), reasoningModelId, setReasoningModelId, "gf-reasoning")}
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
            <AlertDialogDescription>{tc("unsavedChanges")}</AlertDialogDescription>
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

// ============================================================
// Provider Card (with expandable model table)
// ============================================================

interface ProviderCardProps {
  provider: ModelProviderResponse
  onEdit: () => void
  onToggleActive: () => void
  onDelete: () => void
  onAddModel: () => void
  onEditModel: (model: ModelProviderModelResponse) => void
  onToggleModelActive: (model: ModelProviderModelResponse) => void
  onDeleteModel: (model: ModelProviderModelResponse) => void
}

function ProviderCard({
  provider,
  onEdit,
  onToggleActive,
  onDelete,
  onAddModel,
  onEditModel,
  onToggleModelActive,
  onDeleteModel,
}: ProviderCardProps) {
  const t = useTranslations("admin.models")
  const tc = useTranslations("common")
  const [isExpanded, setIsExpanded] = useState(false)

  return (
    <div className="rounded-lg border bg-card">
      {/* Provider header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border/50">
        <div className="rounded-md bg-muted p-1.5">
          <Server className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold">{provider.name}</p>
            <Badge variant="outline" className={`text-[10px] ${provider.is_active ? "border-green-500/40 text-green-600 dark:text-green-400" : "text-muted-foreground"}`}>
              {provider.is_active ? t("active") : t("inactive")}
            </Badge>
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-xs text-muted-foreground">
            {provider.base_url && (
              <span className="flex items-center gap-1 truncate max-w-[300px]">
                <Globe className="h-3 w-3 shrink-0" />
                {provider.base_url}
              </span>
            )}
          </div>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={onEdit}>
              <Pencil className="mr-2 h-4 w-4" />
              {tc("edit")}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onToggleActive}>
              <Power className="mr-2 h-4 w-4" />
              {provider.is_active ? tc("disable") : tc("enable")}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={onDelete}>
              <Trash2 className="mr-2 h-4 w-4" />
              {tc("delete")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Models section */}
      <Collapsible open={isExpanded} onOpenChange={setIsExpanded}>
        <div className="flex items-center justify-between px-4 py-2 bg-muted/20">
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {isExpanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              {t("models")} ({provider.models.length})
            </button>
          </CollapsibleTrigger>
          <Button size="sm" variant="ghost" onClick={onAddModel} className="h-7 gap-1 text-xs">
            <Plus className="h-3.5 w-3.5" />
            {t("addModelUnder")}
          </Button>
        </div>
        <CollapsibleContent>
          {provider.models.length === 0 ? (
            <div className="px-4 py-4 text-xs text-muted-foreground">
              {t("noModelsInProvider")}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-max text-sm">
                <thead>
                  <tr className="border-b border-border/50 bg-muted/10">
                    <th className="px-4 py-2 text-left font-medium text-muted-foreground text-xs">{t("name")}</th>
                    <th className="px-4 py-2 text-left font-medium text-muted-foreground text-xs">{t("modelName")}</th>
                    <th className="px-4 py-2 text-right font-medium text-muted-foreground text-xs">{t("contextSize")}</th>
                    <th className="px-4 py-2 text-right font-medium text-muted-foreground text-xs">{t("maxOutputTokens")}</th>
                    <th className="px-4 py-2 text-left font-medium text-muted-foreground text-xs">{tc("status")}</th>
                    <th className="px-4 py-2 text-right font-medium text-muted-foreground text-xs">{tc("actions")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/50">
                  {provider.models.map((m) => (
                    <tr key={m.id} className="hover:bg-muted/10 transition-colors">
                      <td className="px-4 py-2.5 font-medium">{m.name}</td>
                      <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">{m.model_name}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-muted-foreground">{m.context_size ? `${m.context_size / 1000}K` : "—"}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-muted-foreground">{m.max_output_tokens ? `${m.max_output_tokens / 1000}K` : "—"}</td>
                      <td className="px-4 py-2.5">
                        <Badge variant="outline" className={`text-[10px] ${m.is_active ? "border-green-500/40 text-green-600 dark:text-green-400" : "text-muted-foreground"}`}>
                          {m.is_active ? t("active") : t("inactive")}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => onEditModel(m)}>
                              <Pencil className="mr-2 h-4 w-4" />
                              {tc("edit")}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => onToggleModelActive(m)}>
                              <Power className="mr-2 h-4 w-4" />
                              {m.is_active ? tc("disable") : tc("enable")}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive" onClick={() => onDeleteModel(m)}>
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
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}

// ============================================================
// Group Card
// ============================================================

interface GroupCardProps {
  group: ModelGroupResponse
  isActiveGroup: boolean
  onEdit: () => void
  onActivate: () => void
  onDeactivate: () => void
  onDelete: () => void
}

function GroupCard({ group, isActiveGroup, onEdit, onActivate, onDeactivate, onDelete }: GroupCardProps) {
  const t = useTranslations("admin.models")
  const tc = useTranslations("common")

  const renderSlot = (label: string, slotInfo: ModelSlotInfo | null) => (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground w-20 shrink-0">{label}:</span>
      {slotInfo ? (
        <span className="flex items-center gap-1.5 truncate">
          {!slotInfo.is_available && (
            <span title={t("slotUnavailable")}><AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" /></span>
          )}
          <span className={!slotInfo.is_available ? "line-through text-muted-foreground/60" : ""}>
            <span className="text-muted-foreground">{slotInfo.provider_name}</span>
            <span className="mx-1 text-muted-foreground/50">/</span>
            <span className="font-medium">{slotInfo.name}</span>
          </span>
          {!slotInfo.is_available && (
            <span className="text-xs text-amber-500">({t("slotFallbackEnv")})</span>
          )}
        </span>
      ) : (
        <span className="text-xs text-muted-foreground italic">{t("slotEmpty")}</span>
      )}
    </div>
  )

  return (
    <div className={`rounded-lg border bg-card ${isActiveGroup ? "border-green-500/40" : ""}`}>
      <div className="flex items-center gap-3 px-4 py-3">
        {isActiveGroup ? (
          <CircleDot className="h-4 w-4 text-green-500 shrink-0" />
        ) : (
          <Circle className="h-4 w-4 text-muted-foreground/40 shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold">{group.name}</p>
            <Badge variant="outline" className={`text-[10px] ${isActiveGroup ? "border-green-500/40 text-green-600 dark:text-green-400" : "text-muted-foreground"}`}>
              {isActiveGroup ? t("activeGroup") : t("inactive")}
            </Badge>
          </div>
          {group.description && (
            <p className="text-xs text-muted-foreground mt-0.5">{group.description}</p>
          )}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={onEdit}>
              <Pencil className="mr-2 h-4 w-4" />
              {tc("edit")}
            </DropdownMenuItem>
            {isActiveGroup ? (
              <DropdownMenuItem onClick={onDeactivate}>
                <Power className="mr-2 h-4 w-4" />
                {t("deactivate")}
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem onClick={onActivate}>
                <Power className="mr-2 h-4 w-4" />
                {t("activate")}
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={onDelete}>
              <Trash2 className="mr-2 h-4 w-4" />
              {tc("delete")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="px-4 pb-3 space-y-1.5">
        {renderSlot(t("generalModel"), group.general_model)}
        {renderSlot(t("fastModel"), group.fast_model)}
        {renderSlot(t("reasoningModel"), group.reasoning_model)}
      </div>
    </div>
  )
}

// ============================================================
// Import Model Config Dialog
// ============================================================

function ImportModelConfigDialog({ open, onOpenChange, onSuccess }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const t = useTranslations("admin.models")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const [fileData, setFileData] = useState<Record<string, unknown> | null>(null)
  const [fileName, setFileName] = useState<string>("")
  const [providerNames, setProviderNames] = useState<string[]>([])
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
  const [isImporting, setIsImporting] = useState(false)
  const [clearExisting, setClearExisting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setFileName(file.name)
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const json = JSON.parse(ev.target?.result as string)
        setFileData(json)
        // Extract provider names for API key inputs
        const config = json.fim_model_config_v1 as Record<string, unknown> | undefined
        const providers = (config?.providers as Array<{ name: string }>) ?? []
        const names = providers.map((p) => p.name)
        setProviderNames(names)
        setApiKeys({})
      } catch {
        toast.error("Invalid JSON file")
        setFileData(null)
        setProviderNames([])
      }
    }
    reader.readAsText(file)
  }

  const handleImport = async () => {
    if (!fileData) {
      toast.error(t("noFileSelected"))
      return
    }
    setIsImporting(true)
    try {
      // Filter out empty API keys
      const filteredKeys: Record<string, string> = {}
      for (const [k, v] of Object.entries(apiKeys)) {
        if (v.trim()) filteredKeys[k] = v.trim()
      }
      const payload = { ...fileData, api_keys: filteredKeys, clear_existing: clearExisting }
      const result = await adminApi.importModelConfig(payload)
      const data = result.data

      const descParts = [
        t("importCreated", { providers: data.created.providers, models: data.created.models, groups: data.created.groups }),
        t("importSkipped", { providers: data.skipped.providers, models: data.skipped.models, groups: data.skipped.groups }),
      ]
      if (data.deleted && (data.deleted.providers > 0 || data.deleted.models > 0 || data.deleted.groups > 0)) {
        descParts.unshift(t("importDeleted", { providers: data.deleted.providers, models: data.deleted.models, groups: data.deleted.groups }))
      }

      toast.success(t("importSuccess"), {
        description: descParts.join(" | "),
      })

      if (data.warnings?.length > 0) {
        data.warnings.forEach((w: string) => toast.warning(w))
      }

      onOpenChange(false)
      onSuccess()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsImporting(false)
    }
  }

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setFileData(null)
      setFileName("")
      setProviderNames([])
      setApiKeys({})
      setClearExisting(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("importTitle")}</DialogTitle>
          <DialogDescription className="inline-flex items-center gap-1">
            {t("importDescription")}
            <Tooltip>
              <TooltipTrigger asChild>
                <Info className="h-3.5 w-3.5 shrink-0 text-muted-foreground cursor-help" />
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-xs text-xs">
                {t("importRules")}
              </TooltipContent>
            </Tooltip>
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {/* Clear existing toggle */}
          <div className="flex items-center justify-between rounded-lg border border-destructive/30 bg-destructive/5 p-3">
            <div className="space-y-0.5">
              <Label className="text-sm font-medium">{t("importClearExisting")}</Label>
              <p className="text-xs text-muted-foreground">{t("importClearExistingDesc")}</p>
            </div>
            <Switch checked={clearExisting} onCheckedChange={setClearExisting} />
          </div>

          {/* File input */}
          <div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleFileChange}
              className="block w-full text-sm text-muted-foreground file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-medium file:bg-primary file:text-primary-foreground hover:file:bg-primary/90 file:cursor-pointer"
            />
            {fileName && (
              <p className="mt-1 text-xs text-muted-foreground">{fileName}</p>
            )}
          </div>

          {/* Provider API key inputs */}
          {providerNames.length > 0 && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">{t("importApiKeysHint")}</p>
              {providerNames.map((name) => (
                <div key={name} className="space-y-1">
                  <Label className="text-xs">{name}</Label>
                  <Input
                    type="password"
                    placeholder={t("importApiKeyPlaceholder", { provider: name })}
                    value={apiKeys[name] || ""}
                    onChange={(e) => setApiKeys(prev => ({ ...prev, [name]: e.target.value }))}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{tc("cancel")}</Button>
          <Button onClick={handleImport} disabled={!fileData || isImporting}>
            {isImporting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {tc("import")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ============================================================
// Main AdminModels Component
// ============================================================

export function AdminModels() {
  const t = useTranslations("admin.models")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  // Data state
  const [activeConfig, setActiveConfig] = useState<ModelActiveConfig | null>(null)
  const [providers, setProviders] = useState<ModelProviderResponse[]>([])
  const [groups, setGroups] = useState<ModelGroupResponse[]>([])
  const [envFallback, setEnvFallback] = useState<EnvFallbackInfo | null>(null) // kept for API compat, not displayed
  const [activeGroupId, setActiveGroupId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSwitching, setIsSwitching] = useState(false)

  // Provider dialogs
  const [showCreateProvider, setShowCreateProvider] = useState(false)
  const [editProviderTarget, setEditProviderTarget] = useState<ModelProviderResponse | null>(null)
  const [deleteProviderTarget, setDeleteProviderTarget] = useState<ModelProviderResponse | null>(null)

  // Model dialogs
  const [addModelProviderId, setAddModelProviderId] = useState<string | null>(null)
  const [editModelTarget, setEditModelTarget] = useState<{ providerId: string; model: ModelProviderModelResponse } | null>(null)
  const [deleteModelTarget, setDeleteModelTarget] = useState<ModelProviderModelResponse | null>(null)

  // Group dialogs
  const [showCreateGroup, setShowCreateGroup] = useState(false)
  const [editGroupTarget, setEditGroupTarget] = useState<ModelGroupResponse | null>(null)
  const [deleteGroupTarget, setDeleteGroupTarget] = useState<ModelGroupResponse | null>(null)

  // ENV fallback collapsible
  const [envExpanded, setEnvExpanded] = useState(false)

  // Import/Export
  const [isExporting, setIsExporting] = useState(false)
  const [showImportDialog, setShowImportDialog] = useState(false)

  // Active profile combobox
  const [profileComboboxOpen, setProfileComboboxOpen] = useState(false)

  const handleExport = async () => {
    setIsExporting(true)
    try {
      await adminApi.exportModelConfig()
      toast.success(t("exportSuccess"))
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsExporting(false)
    }
  }

  // Data loading
  const loadAll = useCallback(async () => {
    setIsLoading(true)
    try {
      const [configData, providerData, groupData] = await Promise.all([
        adminApi.getModelActiveConfig(),
        adminApi.listModelProviders(),
        adminApi.listModelGroups(),
      ])
      setActiveConfig(configData)
      setProviders(providerData.providers)
      setGroups(groupData.groups)
      setEnvFallback(groupData.env_fallback)
      setActiveGroupId(groupData.active_group_id)
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { loadAll() }, [loadAll])

  // Profile switch handler
  const handleProfileSwitch = useCallback(async (value: string) => {
    setIsSwitching(true)
    try {
      if (value === "__env__") {
        await adminApi.deactivateModelGroups()
        toast.success(t("profileDeactivated"))
      } else {
        await adminApi.activateModelGroup(value)
        toast.success(t("profileSwitched"))
      }
      await loadAll()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSwitching(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadAll])

  // Provider actions
  const handleToggleProviderActive = async (provider: ModelProviderResponse) => {
    try {
      await adminApi.updateModelProvider(provider.id, { is_active: !provider.is_active })
      toast.success(t("providerStatusUpdated"))
      await loadAll()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const handleDeleteProvider = async () => {
    if (!deleteProviderTarget) return
    try {
      await adminApi.deleteModelProvider(deleteProviderTarget.id)
      toast.success(t("providerDeleted"))
      setDeleteProviderTarget(null)
      await loadAll()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  // Model actions
  const handleToggleModelActive = async (model: ModelProviderModelResponse) => {
    try {
      await adminApi.updateProviderModel(model.id, { is_active: !model.is_active })
      toast.success(t("modelStatusUpdated"))
      await loadAll()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  const handleDeleteModel = async () => {
    if (!deleteModelTarget) return
    try {
      await adminApi.deleteProviderModel(deleteModelTarget.id)
      toast.success(t("modelDeleted"))
      setDeleteModelTarget(null)
      await loadAll()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  // Group actions
  const handleActivateGroup = async (group: ModelGroupResponse) => {
    setIsSwitching(true)
    try {
      await adminApi.activateModelGroup(group.id)
      toast.success(t("groupActivated"))
      await loadAll()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSwitching(false)
    }
  }

  const handleDeactivateGroup = async () => {
    setIsSwitching(true)
    try {
      await adminApi.deactivateModelGroups()
      toast.success(t("groupDeactivated"))
      await loadAll()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsSwitching(false)
    }
  }

  const handleDeleteGroup = async () => {
    if (!deleteGroupTarget) return
    try {
      await adminApi.deleteModelGroup(deleteGroupTarget.id)
      toast.success(t("groupDeleted"))
      setDeleteGroupTarget(null)
      await loadAll()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  // ---- Render ----

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("description")}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleExport} disabled={isExporting} className="gap-1.5">
            {isExporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            {t("exportConfig")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShowImportDialog(true)} className="gap-1.5">
            <Upload className="h-4 w-4" />
            {t("importConfig")}
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          {/* ── Active Configuration Card ── */}
          {activeConfig && (
            <div className="rounded-lg border bg-card p-4 space-y-4">
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-semibold">{t("activeConfig")}</h3>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <Label className="text-sm text-muted-foreground shrink-0">{t("currentProfile")}:</Label>
                <Popover open={profileComboboxOpen} onOpenChange={setProfileComboboxOpen}>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      role="combobox"
                      aria-expanded={profileComboboxOpen}
                      disabled={isSwitching}
                      className="w-full max-w-[260px] justify-between font-normal"
                    >
                      <span className="truncate">
                        {activeGroupId
                          ? groups.find((g) => g.id === activeGroupId)?.name ?? t("defaultEnv")
                          : t("defaultEnv")}
                      </span>
                      <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
                    <Command>
                      <CommandInput placeholder={tc("searchPlaceholder")} />
                      <CommandList>
                        <CommandEmpty>{tc("noResults")}</CommandEmpty>
                        <CommandGroup>
                          <CommandItem
                            value={t("defaultEnv")}
                            onSelect={() => {
                              handleProfileSwitch("__env__")
                              setProfileComboboxOpen(false)
                            }}
                          >
                            <Check
                              className={cn(
                                "mr-2 h-4 w-4",
                                !activeGroupId ? "opacity-100" : "opacity-0"
                              )}
                            />
                            {t("defaultEnv")}
                          </CommandItem>
                          {groups.map((g) => (
                            <CommandItem
                              key={g.id}
                              value={g.name}
                              onSelect={() => {
                                handleProfileSwitch(g.id)
                                setProfileComboboxOpen(false)
                              }}
                            >
                              <Check
                                className={cn(
                                  "mr-2 h-4 w-4",
                                  activeGroupId === g.id ? "opacity-100" : "opacity-0"
                                )}
                              />
                              {g.name}
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </CommandList>
                    </Command>
                  </PopoverContent>
                </Popover>
                {isSwitching && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
              </div>
              <div>
                <p className="text-xs text-muted-foreground mb-2">{t("effectiveModels")}</p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <EffectiveSlotCard
                    role="general"
                    modelName={activeConfig.effective.general.model_name}
                    providerName={activeConfig.effective.general.provider_name}
                    source={activeConfig.effective.general.source}
                  />
                  <EffectiveSlotCard
                    role="fast"
                    modelName={activeConfig.effective.fast.model_name}
                    providerName={activeConfig.effective.fast.provider_name}
                    source={activeConfig.effective.fast.source}
                  />
                  <EffectiveSlotCard
                    role="reasoning"
                    modelName={activeConfig.effective.reasoning.model_name}
                    providerName={activeConfig.effective.reasoning.provider_name}
                    source={activeConfig.effective.reasoning.source}
                  />
                </div>
              </div>
            </div>
          )}

          {/* ── Providers Section ── */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Server className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-semibold">{t("providers")}</h3>
              </div>
              <Button size="sm" onClick={() => setShowCreateProvider(true)} className="gap-1.5">
                <Plus className="h-4 w-4" />
                {t("addProvider")}
              </Button>
            </div>
            {providers.length === 0 ? (
              <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                {t("noProviders")}
              </div>
            ) : (
              <div className="space-y-3">
                {providers.map((p) => (
                  <ProviderCard
                    key={p.id}
                    provider={p}
                    onEdit={() => setEditProviderTarget(p)}
                    onToggleActive={() => handleToggleProviderActive(p)}
                    onDelete={() => setDeleteProviderTarget(p)}
                    onAddModel={() => setAddModelProviderId(p.id)}
                    onEditModel={(m) => setEditModelTarget({ providerId: p.id, model: m })}
                    onToggleModelActive={handleToggleModelActive}
                    onDeleteModel={(m) => setDeleteModelTarget(m)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* ── Model Groups Section ── */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-semibold">{t("groups")}</h3>
              </div>
              <Button size="sm" onClick={() => setShowCreateGroup(true)} className="gap-1.5">
                <Plus className="h-4 w-4" />
                {t("addGroup")}
              </Button>
            </div>
            {groups.length === 0 ? (
              <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                {t("noGroups")}
              </div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {groups.map((g) => (
                  <GroupCard
                    key={g.id}
                    group={g}
                    isActiveGroup={g.id === activeGroupId}
                    onEdit={() => setEditGroupTarget(g)}
                    onActivate={() => handleActivateGroup(g)}
                    onDeactivate={handleDeactivateGroup}
                    onDelete={() => setDeleteGroupTarget(g)}
                  />
                ))}
              </div>
            )}
          </div>

        </>
      )}

      {/* ── Dialogs ── */}

      {/* Provider create */}
      <ProviderFormDialog
        open={showCreateProvider}
        onOpenChange={setShowCreateProvider}
        onSuccess={loadAll}
      />

      {/* Provider edit */}
      <ProviderFormDialog
        open={!!editProviderTarget}
        onOpenChange={(open) => { if (!open) setEditProviderTarget(null) }}
        provider={editProviderTarget}
        onSuccess={loadAll}
      />

      {/* Provider delete confirm */}
      <AlertDialog open={!!deleteProviderTarget} onOpenChange={(open) => !open && setDeleteProviderTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteProvider")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteProviderConfirm", { name: deleteProviderTarget?.name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteProvider} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Model create (under a provider) */}
      <ModelFormDialog
        open={!!addModelProviderId}
        onOpenChange={(open) => { if (!open) setAddModelProviderId(null) }}
        providerId={addModelProviderId ?? ""}
        onSuccess={loadAll}
      />

      {/* Model edit */}
      <ModelFormDialog
        open={!!editModelTarget}
        onOpenChange={(open) => { if (!open) setEditModelTarget(null) }}
        providerId={editModelTarget?.providerId ?? ""}
        model={editModelTarget?.model}
        onSuccess={loadAll}
      />

      {/* Model delete confirm */}
      <AlertDialog open={!!deleteModelTarget} onOpenChange={(open) => !open && setDeleteModelTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteModel")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteModelConfirm", { name: deleteModelTarget?.name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteModel} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Group create */}
      <GroupFormDialog
        open={showCreateGroup}
        onOpenChange={setShowCreateGroup}
        providers={providers}
        onSuccess={loadAll}
      />

      {/* Group edit */}
      <GroupFormDialog
        open={!!editGroupTarget}
        onOpenChange={(open) => { if (!open) setEditGroupTarget(null) }}
        group={editGroupTarget}
        providers={providers}
        onSuccess={loadAll}
      />

      {/* Group delete confirm */}
      <AlertDialog open={!!deleteGroupTarget} onOpenChange={(open) => !open && setDeleteGroupTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteGroup")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteGroupConfirm", { name: deleteGroupTarget?.name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteGroup} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              {tc("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Import model config */}
      <ImportModelConfigDialog
        open={showImportDialog}
        onOpenChange={setShowImportDialog}
        onSuccess={loadAll}
      />
    </div>
  )
}
