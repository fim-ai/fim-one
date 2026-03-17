"use client"

import { useState, useEffect } from "react"
import { Loader2, Plug } from "lucide-react"
import { toast } from "sonner"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { EmojiPickerPopover } from "@/components/ui/emoji-picker-popover"
import { connectorApi } from "@/lib/api"
import type { ConnectorCreate, ConnectorResponse } from "@/types/connector"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

interface ConnectorSettingsFormProps {
  connector: ConnectorResponse | null // null = create mode
  onSaved: (connector: ConnectorResponse) => void
  onDirtyChange?: (dirty: boolean) => void
}

export function ConnectorSettingsForm({
  connector,
  onSaved,
  onDirtyChange,
}: ConnectorSettingsFormProps) {
  const t = useTranslations("connectors")
  const tc = useTranslations("common")

  const [name, setName] = useState("")
  const [icon, setIcon] = useState<string | null>(null)
  const [description, setDescription] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [authType, setAuthType] = useState("none")
  // Auth config fields
  const [tokenPrefix, setTokenPrefix] = useState("Bearer")
  const [headerName, setHeaderName] = useState("X-API-Key")
  // Default credentials
  const [defaultToken, setDefaultToken] = useState("")
  const [defaultApiKey, setDefaultApiKey] = useState("")
  const [defaultUsername, setDefaultUsername] = useState("")
  const [defaultPassword, setDefaultPassword] = useState("")
  // Allow fallback setting
  const [allowFallback, setAllowFallback] = useState(true)

  const [isSubmitting, setIsSubmitting] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<{ baseUrl?: string }>({})

  // Pre-fill when connector prop changes (full sync)
  useEffect(() => {
    if (connector) {
      setName(connector.name)
      setIcon(connector.icon || null)
      setDescription(connector.description || "")
      setBaseUrl(connector.base_url || "")
      setAuthType(connector.auth_type)
      const cfg = connector.auth_config || {}
      setTokenPrefix(typeof cfg.token_prefix === "string" ? cfg.token_prefix : "Bearer")
      setHeaderName(typeof cfg.header_name === "string" ? cfg.header_name : "X-API-Key")
      // After encryption migration, default credentials are no longer stored in auth_config.
      // Fields stay empty; placeholder text indicates stored values when has_default_credentials is true.
      setDefaultToken(typeof cfg.default_token === "string" ? cfg.default_token : "")
      setDefaultApiKey(typeof cfg.default_api_key === "string" ? cfg.default_api_key : "")
      setDefaultUsername(typeof cfg.default_username === "string" ? cfg.default_username : "")
      setDefaultPassword(typeof cfg.default_password === "string" ? cfg.default_password : "")
      setAllowFallback(connector.allow_fallback ?? true)
    } else {
      setName("")
      setIcon(null)
      setDescription("")
      setBaseUrl("")
      setAuthType("none")
      setTokenPrefix("Bearer")
      setHeaderName("X-API-Key")
      setDefaultToken("")
      setDefaultApiKey("")
      setDefaultUsername("")
      setDefaultPassword("")
      setAllowFallback(true)
    }
  }, [connector])

  // Compute and notify dirty state
  useEffect(() => {
    if (!onDirtyChange) return
    if (!connector) {
      // Create mode: dirty if user typed anything required
      onDirtyChange(name.trim() !== "" || baseUrl.trim() !== "")
      return
    }
    const cfg = connector.auth_config || {}
    const dirty =
      name !== connector.name ||
      icon !== (connector.icon || null) ||
      description !== (connector.description || "") ||
      baseUrl !== connector.base_url ||
      authType !== connector.auth_type ||
      tokenPrefix !== (typeof cfg.token_prefix === "string" ? cfg.token_prefix : "Bearer") ||
      headerName !== (typeof cfg.header_name === "string" ? cfg.header_name : "X-API-Key") ||
      defaultToken !== (typeof cfg.default_token === "string" ? cfg.default_token : "") ||
      defaultApiKey !== (typeof cfg.default_api_key === "string" ? cfg.default_api_key : "") ||
      defaultUsername !== (typeof cfg.default_username === "string" ? cfg.default_username : "") ||
      defaultPassword !== (typeof cfg.default_password === "string" ? cfg.default_password : "") ||
      allowFallback !== (connector.allow_fallback ?? true)
    onDirtyChange(dirty)
  }, [connector, name, icon, description, baseUrl, authType, tokenPrefix, headerName, defaultToken, defaultApiKey, defaultUsername, defaultPassword, allowFallback, onDirtyChange])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    const trimmedUrl = baseUrl.trim()
    if (!trimmedName || !trimmedUrl) return

    // Validate URL format before hitting the API
    try {
      const u = new URL(trimmedUrl)
      if (u.protocol !== "http:" && u.protocol !== "https:") {
        setFieldErrors({ baseUrl: t("baseUrlInvalid") })
        return
      }
    } catch {
      setFieldErrors({ baseUrl: t("baseUrlInvalid") })
      return
    }
    setFieldErrors({})

    setIsSubmitting(true)
    try {
      let authConfig: Record<string, unknown> | null = null
      if (authType === "bearer") {
        authConfig = {
          token_prefix: tokenPrefix.trim() || "Bearer",
          // Only include default_token if user typed a value.
          // If the field is empty and has_default_credentials is true, omit so backend keeps existing encrypted credential.
          ...(defaultToken.trim() && { default_token: defaultToken.trim() }),
        }
      } else if (authType === "api_key") {
        authConfig = {
          header_name: headerName.trim() || "X-API-Key",
          ...(defaultApiKey.trim() && { default_api_key: defaultApiKey.trim() }),
        }
      } else if (authType === "basic") {
        authConfig = {
          ...(defaultUsername.trim() && { default_username: defaultUsername.trim() }),
          ...(defaultPassword.trim() && { default_password: defaultPassword.trim() }),
        }
        if (Object.keys(authConfig).length === 0) authConfig = null
      }

      const data: ConnectorCreate = {
        name: trimmedName,
        icon: icon || null,
        description: description.trim() || null,
        type: "api",
        base_url: trimmedUrl,
        auth_type: authType,
        ...(authConfig && { auth_config: authConfig }),
      }

      let result: ConnectorResponse
      if (connector) {
        result = await connectorApi.update(connector.id, {
          ...data,
          ...(authType !== "none" && { allow_fallback: allowFallback }),
        })
      } else {
        result = await connectorApi.create(data)
      }

      onSaved(result)
      toast.success(connector ? t("connectorUpdated") : t("connectorCreated"))
    } catch (err) {
      console.error("Failed to save connector:", err)
      const message =
        err instanceof Error ? err.message : "Unknown error"
      toast.error(t("connectorSaveFailed", { message }))
    } finally {
      setIsSubmitting(false)
    }
  }

  // Whether credentials are already stored (edit mode only)
  const hasStoredCredentials = !!(connector?.has_default_credentials)
  const credentialPlaceholder = hasStoredCredentials ? "••••••••" : undefined
  const credentialHint = hasStoredCredentials ? t("credentialsHelpOwner") : undefined

  return (
    <form onSubmit={handleSubmit} className="flex flex-col flex-1 min-h-0 overflow-hidden">
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-4">
          {/* Name + Icon */}
          <div className="space-y-1.5">
            <label htmlFor="connector-name" className="text-sm font-medium">
              {tc("name")} <span className="text-destructive">*</span>
            </label>
            <div className="flex items-center gap-2">
              <EmojiPickerPopover
                value={icon}
                onChange={setIcon}
                fallbackIcon={<Plug className="h-5 w-5" />}
              />
              <Input
                id="connector-name"
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
            <label htmlFor="connector-description" className="text-sm font-medium">
              {tc("description")}
            </label>
            <Textarea
              id="connector-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("descriptionPlaceholder")}
              rows={2}
              className="resize-none"
            />
          </div>

          {/* Base URL */}
          <div className="space-y-1.5">
            <label htmlFor="connector-base-url" className="text-sm font-medium">
              {t("baseUrl")} <span className="text-destructive">*</span>
            </label>
            <Input
              id="connector-base-url"
              type="text"
              value={baseUrl}
              onChange={(e) => {
                setBaseUrl(e.target.value)
                if (fieldErrors.baseUrl) setFieldErrors({})
              }}
              placeholder={t("baseUrlPlaceholder")}
              aria-invalid={!!fieldErrors.baseUrl}
              required
            />
            {fieldErrors.baseUrl && (
              <p className="text-xs text-destructive">{fieldErrors.baseUrl}</p>
            )}
          </div>

          {/* Auth Type */}
          <div className="space-y-1.5">
            <label htmlFor="connector-auth-type" className="text-sm font-medium">
              {t("authType")}
            </label>
            <Select value={authType} onValueChange={setAuthType}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">{t("authTypeNone")}</SelectItem>
                <SelectItem value="bearer">{t("authTypeBearer")}</SelectItem>
                <SelectItem value="api_key">{t("authTypeApiKey")}</SelectItem>
                <SelectItem value="basic">{t("authTypeBasic")}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Bearer Token config */}
          {authType === "bearer" && (
            <div className="space-y-3 rounded-md border border-border p-3">
              <div className="space-y-1.5">
                <label htmlFor="connector-token-prefix" className="text-sm font-medium">
                  {t("tokenPrefix")}
                </label>
                <Input
                  id="connector-token-prefix"
                  type="text"
                  value={tokenPrefix}
                  onChange={(e) => setTokenPrefix(e.target.value)}
                  placeholder="Bearer"
                />
                <p className="text-xs text-muted-foreground">
                  {t("tokenPrefixHelp")}
                </p>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="connector-default-token" className="text-sm font-medium">
                  {t("defaultToken")}
                </label>
                <Input
                  id="connector-default-token"
                  type="password"
                  value={defaultToken}
                  onChange={(e) => setDefaultToken(e.target.value)}
                  placeholder={credentialPlaceholder ?? "ghp_xxxxxxxxxxxx"}
                />
                <p className="text-xs text-muted-foreground">
                  {credentialHint ?? t("defaultTokenHelp")}
                </p>
              </div>
            </div>
          )}

          {/* API Key config */}
          {authType === "api_key" && (
            <div className="space-y-3 rounded-md border border-border p-3">
              <div className="space-y-1.5">
                <label htmlFor="connector-header-name" className="text-sm font-medium">
                  {t("headerName")}
                </label>
                <Input
                  id="connector-header-name"
                  type="text"
                  value={headerName}
                  onChange={(e) => setHeaderName(e.target.value)}
                  placeholder="X-API-Key"
                />
                <p className="text-xs text-muted-foreground">
                  {t("headerNameHelp")}
                </p>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="connector-default-api-key" className="text-sm font-medium">
                  {t("defaultApiKey")}
                </label>
                <Input
                  id="connector-default-api-key"
                  type="password"
                  value={defaultApiKey}
                  onChange={(e) => setDefaultApiKey(e.target.value)}
                  placeholder={credentialPlaceholder ?? "sk-xxxxxxxxxxxx"}
                />
                <p className="text-xs text-muted-foreground">
                  {credentialHint ?? t("defaultApiKeyHelp")}
                </p>
              </div>
            </div>
          )}

          {/* Basic Auth config */}
          {authType === "basic" && (
            <div className="space-y-3 rounded-md border border-border p-3">
              <div className="space-y-1.5">
                <label htmlFor="connector-default-username" className="text-sm font-medium">
                  {t("username")}
                </label>
                <Input
                  id="connector-default-username"
                  type="text"
                  value={defaultUsername}
                  onChange={(e) => setDefaultUsername(e.target.value)}
                  placeholder="admin"
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="connector-default-password" className="text-sm font-medium">
                  {t("password")}
                </label>
                <Input
                  id="connector-default-password"
                  type="password"
                  value={defaultPassword}
                  onChange={(e) => setDefaultPassword(e.target.value)}
                  placeholder={credentialPlaceholder ?? "********"}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {credentialHint ?? t("basicAuthHelp")}
              </p>
            </div>
          )}

          {/* Allow Fallback — shown in edit mode for connectors with auth (regardless of visibility) */}
          {connector && authType !== "none" && (
            <div className="flex items-center justify-between rounded-md border border-border p-3">
              <div className="space-y-0.5">
                <p className="text-sm font-medium">{t("allowFallback")}</p>
                <p className="text-xs text-muted-foreground">{t("allowFallbackHelp")}</p>
              </div>
              <Switch
                checked={allowFallback}
                onCheckedChange={setAllowFallback}
              />
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Save button outside scroll area */}
      <div className="flex justify-end pt-4">
        <Button type="submit" disabled={isSubmitting || !name.trim() || !baseUrl.trim()}>
          {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
          {tc("save")}
        </Button>
      </div>
    </form>
  )
}
