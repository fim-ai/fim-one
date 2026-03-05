"use client"

import { useState, useEffect } from "react"
import { Loader2, Plug } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { EmojiPickerPopover } from "@/components/ui/emoji-picker-popover"
import { connectorApi } from "@/lib/api"
import type { ConnectorCreate, ConnectorResponse } from "@/types/connector"

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

  const [isSubmitting, setIsSubmitting] = useState(false)

  // Pre-fill when connector prop changes (full sync)
  useEffect(() => {
    if (connector) {
      setName(connector.name)
      setIcon(connector.icon || null)
      setDescription(connector.description || "")
      setBaseUrl(connector.base_url)
      setAuthType(connector.auth_type)
      const cfg = connector.auth_config || {}
      setTokenPrefix(typeof cfg.token_prefix === "string" ? cfg.token_prefix : "Bearer")
      setHeaderName(typeof cfg.header_name === "string" ? cfg.header_name : "X-API-Key")
      setDefaultToken(typeof cfg.default_token === "string" ? cfg.default_token : "")
      setDefaultApiKey(typeof cfg.default_api_key === "string" ? cfg.default_api_key : "")
      setDefaultUsername(typeof cfg.default_username === "string" ? cfg.default_username : "")
      setDefaultPassword(typeof cfg.default_password === "string" ? cfg.default_password : "")
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
      defaultPassword !== (typeof cfg.default_password === "string" ? cfg.default_password : "")
    onDirtyChange(dirty)
  }, [connector, name, icon, description, baseUrl, authType, tokenPrefix, headerName, defaultToken, defaultApiKey, defaultUsername, defaultPassword, onDirtyChange])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    const trimmedUrl = baseUrl.trim()
    if (!trimmedName || !trimmedUrl) return

    setIsSubmitting(true)
    try {
      let authConfig: Record<string, unknown> | null = null
      if (authType === "bearer") {
        authConfig = {
          token_prefix: tokenPrefix.trim() || "Bearer",
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
        result = await connectorApi.update(connector.id, data)
      } else {
        result = await connectorApi.create(data)
      }

      onSaved(result)
      toast.success(connector ? "Connector updated" : "Connector created")
    } catch (err) {
      console.error("Failed to save connector:", err)
      const message =
        err instanceof Error ? err.message : "Unknown error"
      toast.error(`Failed to save connector: ${message}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  const inputClass =
    "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"

  return (
    <form onSubmit={handleSubmit} className="flex flex-col h-full overflow-hidden">
      <ScrollArea className="flex-1">
        <div className="space-y-4 pr-4">
          {/* Name + Icon */}
          <div className="space-y-1.5">
            <label htmlFor="connector-name" className="text-sm font-medium">
              Name <span className="text-destructive">*</span>
            </label>
            <div className="flex items-center gap-2">
              <EmojiPickerPopover
                value={icon}
                onChange={setIcon}
                fallbackIcon={<Plug className="h-5 w-5" />}
              />
              <input
                id="connector-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="GitHub API"
                required
                className={inputClass}
              />
            </div>
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <label htmlFor="connector-description" className="text-sm font-medium">
              Description
            </label>
            <textarea
              id="connector-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="A brief description of this connector..."
              rows={2}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none"
            />
          </div>

          {/* Base URL */}
          <div className="space-y-1.5">
            <label htmlFor="connector-base-url" className="text-sm font-medium">
              Base URL <span className="text-destructive">*</span>
            </label>
            <input
              id="connector-base-url"
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.example.com"
              required
              className={inputClass}
            />
          </div>

          {/* Auth Type */}
          <div className="space-y-1.5">
            <label htmlFor="connector-auth-type" className="text-sm font-medium">
              Auth Type
            </label>
            <select
              id="connector-auth-type"
              value={authType}
              onChange={(e) => setAuthType(e.target.value)}
              className={inputClass}
            >
              <option value="none">None</option>
              <option value="bearer">Bearer Token</option>
              <option value="api_key">API Key (Custom Header)</option>
              <option value="basic">Basic Auth (Username/Password)</option>
            </select>
          </div>

          {/* Bearer Token config */}
          {authType === "bearer" && (
            <div className="space-y-3 rounded-md border border-border p-3">
              <div className="space-y-1.5">
                <label htmlFor="connector-token-prefix" className="text-sm font-medium">
                  Token Prefix
                </label>
                <input
                  id="connector-token-prefix"
                  type="text"
                  value={tokenPrefix}
                  onChange={(e) => setTokenPrefix(e.target.value)}
                  placeholder="Bearer"
                  className={inputClass}
                />
                <p className="text-xs text-muted-foreground">
                  Prefix before the token in Authorization header. Default: Bearer.
                </p>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="connector-default-token" className="text-sm font-medium">
                  Default Token
                </label>
                <input
                  id="connector-default-token"
                  type="password"
                  value={defaultToken}
                  onChange={(e) => setDefaultToken(e.target.value)}
                  placeholder="ghp_xxxxxxxxxxxx"
                  className={inputClass}
                />
                <p className="text-xs text-muted-foreground">
                  Used for testing. Per-user credentials will override this in a future version.
                </p>
              </div>
            </div>
          )}

          {/* API Key config */}
          {authType === "api_key" && (
            <div className="space-y-3 rounded-md border border-border p-3">
              <div className="space-y-1.5">
                <label htmlFor="connector-header-name" className="text-sm font-medium">
                  Header Name
                </label>
                <input
                  id="connector-header-name"
                  type="text"
                  value={headerName}
                  onChange={(e) => setHeaderName(e.target.value)}
                  placeholder="X-API-Key"
                  className={inputClass}
                />
                <p className="text-xs text-muted-foreground">
                  The HTTP header used to send the API key. Default: X-API-Key.
                </p>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="connector-default-api-key" className="text-sm font-medium">
                  Default API Key
                </label>
                <input
                  id="connector-default-api-key"
                  type="password"
                  value={defaultApiKey}
                  onChange={(e) => setDefaultApiKey(e.target.value)}
                  placeholder="sk-xxxxxxxxxxxx"
                  className={inputClass}
                />
                <p className="text-xs text-muted-foreground">
                  Used for testing. Per-user credentials will override this in a future version.
                </p>
              </div>
            </div>
          )}

          {/* Basic Auth config */}
          {authType === "basic" && (
            <div className="space-y-3 rounded-md border border-border p-3">
              <div className="space-y-1.5">
                <label htmlFor="connector-default-username" className="text-sm font-medium">
                  Username
                </label>
                <input
                  id="connector-default-username"
                  type="text"
                  value={defaultUsername}
                  onChange={(e) => setDefaultUsername(e.target.value)}
                  placeholder="admin"
                  className={inputClass}
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="connector-default-password" className="text-sm font-medium">
                  Password
                </label>
                <input
                  id="connector-default-password"
                  type="password"
                  value={defaultPassword}
                  onChange={(e) => setDefaultPassword(e.target.value)}
                  placeholder="********"
                  className={inputClass}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                Used for testing. Per-user credentials will override this in a future version.
              </p>
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Save button outside scroll area */}
      <div className="flex justify-end pt-4">
        <Button type="submit" disabled={isSubmitting || !name.trim() || !baseUrl.trim()}>
          {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
          Save
        </Button>
      </div>
    </form>
  )
}
