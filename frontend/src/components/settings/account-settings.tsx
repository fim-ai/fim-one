"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useTranslations } from "next-intl"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
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
import { apiFetch, authApi, ApiError } from "@/lib/api"
import { toast } from "sonner"
import { useAuth } from "@/contexts/auth-context"
import { ACCESS_TOKEN_KEY, getApiDirectUrl } from "@/lib/constants"
import type { UserInfo } from "@/types/auth"

const MIN_PASSWORD_LENGTH = 8
const SUPPORTED_PROVIDERS = ["github", "google"] as const

function ProviderIcon({ provider }: { provider: string }) {
  if (provider === "github") {
    return (
      <svg className="h-5 w-5 shrink-0" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
      </svg>
    )
  }
  if (provider === "google") {
    return (
      <svg className="h-5 w-5 shrink-0" viewBox="0 0 24 24">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
      </svg>
    )
  }
  return null
}

function formatProviderName(provider: string): string {
  if (provider === "github") return "GitHub"
  if (provider === "google") return "Google"
  return provider.charAt(0).toUpperCase() + provider.slice(1)
}

function OAuthBindingsSection({ user, onUnbind, onConnect }: { user: UserInfo; onUnbind: (provider: string) => void; onConnect: (provider: string) => void }) {
  const t = useTranslations("settings.account")
  const tc = useTranslations("common")
  const bindings = user.oauth_bindings ?? []
  const bindingMap = new Map(bindings.map((b) => [b.provider, b]))
  const hasPassword = user.has_password ?? false
  const canUnbindAny = hasPassword || bindings.length > 1

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-medium">{t("connectedAccountsTitle")}</h3>
        <p className="text-sm text-muted-foreground">
          {t("connectedAccountsDescription")}
        </p>
      </div>
      <div className="space-y-3 max-w-md">
        {SUPPORTED_PROVIDERS.map((provider) => {
          const binding = bindingMap.get(provider)
          const isBound = !!binding
          // Disable unbind when: no password AND this is the only binding left
          const unbindDisabled = !canUnbindAny

          return (
            <div
              key={provider}
              className="flex items-center gap-3 rounded-md border p-3"
            >
              <ProviderIcon provider={provider} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">
                    {formatProviderName(provider)}
                  </span>
                  {isBound ? (
                    <Badge
                      variant="secondary"
                      className="text-[10px] bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                    >
                      {t("connected")}
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-[10px] text-muted-foreground">
                      {t("notConnected")}
                    </Badge>
                  )}
                </div>
                {isBound && binding.email && (
                  <p className="text-xs text-muted-foreground truncate">
                    {binding.email}
                  </p>
                )}
                {isBound && binding.bound_at && (
                  <p className="text-xs text-muted-foreground">
                    {t("bound", { date: new Date(binding.bound_at).toLocaleDateString() })}
                  </p>
                )}
              </div>
              {isBound ? (
                unbindDisabled ? (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="inline-flex">
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-xs"
                            disabled
                          >
                            {t("disconnect")}
                          </Button>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>
                        {t("disconnectTooltip")}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="outline" size="sm" className="text-xs">
                        {t("disconnect")}
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>
                          {t("disconnectConfirmTitle", { provider: formatProviderName(provider) })}
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                          {t("disconnectConfirmDescription", { provider: formatProviderName(provider) })}
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
                        <AlertDialogAction onClick={() => onUnbind(provider)}>
                          {t("disconnect")}
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs"
                  onClick={() => onConnect(provider)}
                >
                  {t("connect")}
                </Button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function AccountSettings() {
  const { user, updateUser } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const t = useTranslations("settings.account")
  const tc = useTranslations("common")

  const BIND_ERROR_MESSAGES: Record<string, string> = {
    email_mismatch: t("bindErrorEmailMismatch"),
    already_bound: t("bindErrorAlreadyBound"),
    already_connected: t("bindErrorAlreadyConnected"),
  }

  const refreshUser = useCallback(async () => {
    try {
      const freshUser = await authApi.me()
      updateUser(freshUser)
    } catch {
      // Silently ignore — user data will be stale until next refresh
    }
  }, [updateUser])

  useEffect(() => {
    const bindSuccess = searchParams.get("bind_success")
    const bindError = searchParams.get("bind_error")

    if (bindSuccess) {
      const providerName = formatProviderName(bindSuccess)
      toast.success(t("providerConnected", { provider: providerName }))
      refreshUser()
      router.replace("/settings?tab=account", { scroll: false })
    } else if (bindError) {
      const text =
        BIND_ERROR_MESSAGES[bindError] ??
        t("bindErrorGeneric", { error: bindError })
      toast.error(text)
      router.replace("/settings?tab=account", { scroll: false })
    }
  }, [searchParams, router, refreshUser])

  const handleConnect = async (provider: string) => {
    const token = localStorage.getItem(ACCESS_TOKEN_KEY)
    if (!token) return
    try {
      // Fetch a one-time bind ticket instead of passing the raw JWT in the URL
      const res = await apiFetch<{ ticket: string }>("/api/auth/oauth/bind-ticket", {
        method: "POST",
      })
      window.location.href = `${getApiDirectUrl()}/api/auth/oauth/${provider}/authorize?action=bind&ticket=${res.ticket}`
    } catch {
      toast.error(t("connectFailed"))
    }
  }

  // Email state
  const [email, setEmail] = useState(user?.email ?? "")
  const [emailSaving, setEmailSaving] = useState(false)
  const [emailError, setEmailError] = useState("")

  const emailChanged = email !== (user?.email ?? "")
  const emailEmpty = email.trim() === ""
  const emailValid = !emailEmpty && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
  const canSaveEmail = emailChanged && emailValid && !emailSaving

  const handleEmailSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSaveEmail) return

    setEmailSaving(true)
    setEmailError("")

    try {
      const updated = await authApi.updateProfile({
        email: email.trim(),
      })
      updateUser({ email: updated.email })
      toast.success(t("emailUpdated"))
    } catch (err) {
      if (err instanceof ApiError) {
        setEmailError(err.message)
      } else {
        setEmailError(t("emailUpdateFailed"))
      }
    } finally {
      setEmailSaving(false)
    }
  }

  // OAuth unbind state
  const [unbindingProvider, setUnbindingProvider] = useState<string | null>(null)

  const handleUnbind = async (provider: string) => {
    if (unbindingProvider) return

    setUnbindingProvider(provider)

    try {
      const updatedUser = await authApi.unbindOAuth(provider)
      updateUser({
        oauth_bindings: updatedUser.oauth_bindings,
        oauth_provider: updatedUser.oauth_provider,
        has_password: updatedUser.has_password,
      })
      toast.success(t("providerDisconnected", { provider: formatProviderName(provider) }))
    } catch (err) {
      if (err instanceof ApiError) {
        toast.error(err.message)
      } else {
        toast.error(t("disconnectFailed"))
      }
    } finally {
      setUnbindingProvider(null)
    }
  }

  // Password state (change password — for users who already have a password)
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  const hasPassword = user?.has_password ?? !user?.oauth_provider

  const confirmMismatch =
    confirmPassword.length > 0 && newPassword !== confirmPassword
  const newTooShort =
    newPassword.length > 0 && newPassword.length < MIN_PASSWORD_LENGTH
  const canSubmit =
    currentPassword.length > 0 &&
    newPassword.length >= MIN_PASSWORD_LENGTH &&
    newPassword === confirmPassword &&
    !saving

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return

    setSaving(true)
    setError("")

    try {
      await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
      toast.success(t("passwordChanged"))
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError(t("passwordChangeFailed"))
      }
    } finally {
      setSaving(false)
    }
  }

  // Set password state (for OAuth-only users who have no password yet)
  const [setNewPw, setSetNewPw] = useState("")
  const [setConfirmPw, setSetConfirmPw] = useState("")
  const [setPwSaving, setSetPwSaving] = useState(false)
  const [setPwError, setSetPwError] = useState("")

  const setConfirmMismatch =
    setConfirmPw.length > 0 && setNewPw !== setConfirmPw
  const setNewTooShort =
    setNewPw.length > 0 && setNewPw.length < MIN_PASSWORD_LENGTH
  const canSetPw =
    setNewPw.length >= MIN_PASSWORD_LENGTH &&
    setNewPw === setConfirmPw &&
    !setPwSaving

  const handleSetPassword = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSetPw) return

    setSetPwSaving(true)
    setSetPwError("")

    try {
      const updatedUser = await authApi.setPassword({
        new_password: setNewPw,
      })
      updateUser({ has_password: updatedUser.has_password })
      setSetNewPw("")
      setSetConfirmPw("")
      toast.success(t("passwordSet"))
    } catch (err) {
      if (err instanceof ApiError) {
        setSetPwError(err.message)
      } else {
        setSetPwError(t("passwordSetFailed"))
      }
    } finally {
      setSetPwSaving(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Connected Accounts -- always shown for all users */}
      {user && (
        <OAuthBindingsSection
          user={user}
          onUnbind={handleUnbind}
          onConnect={handleConnect}
        />
      )}

      <Separator />

      {/* Email Section */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("emailTitle")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("emailDescription")}
          </p>
        </div>

        <form onSubmit={handleEmailSubmit} className="space-y-4 max-w-sm">
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("emailLabel")} <span className="text-destructive">*</span></label>
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={t("emailPlaceholder")}
            />
            {emailEmpty && emailChanged && (
              <p className="text-xs text-destructive">
                {t("emailRequired")}
              </p>
            )}
            {!emailEmpty && !emailValid && (
              <p className="text-xs text-destructive">
                {t("emailInvalid")}
              </p>
            )}
          </div>

          {emailError && (
            <p className="text-sm text-destructive">{emailError}</p>
          )}

          <Button type="submit" size="sm" disabled={!canSaveEmail}>
            {emailSaving ? tc("saving") : t("updateEmail")}
          </Button>
        </form>
      </div>

      <Separator />

      {/* Password Section */}
      {hasPassword ? (
        <div className="space-y-4">
          <div>
            <h3 className="text-base font-medium">{t("changePasswordTitle")}</h3>
            <p className="text-sm text-muted-foreground">
              {t("changePasswordDescription")}
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4 max-w-sm">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("currentPasswordLabel")} <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                placeholder={t("currentPasswordPlaceholder")}
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">{t("newPasswordLabel")} <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder={t("newPasswordPlaceholder")}
              />
              {newTooShort && (
                <p className="text-xs text-destructive">
                  {t("passwordMinLength", { min: MIN_PASSWORD_LENGTH })}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">{t("confirmPasswordLabel")} <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder={t("confirmPasswordPlaceholder")}
              />
              {confirmMismatch && (
                <p className="text-xs text-destructive">
                  {t("passwordMismatch")}
                </p>
              )}
            </div>

            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}

            <Button type="submit" size="sm" disabled={!canSubmit}>
              {saving ? t("changing") : t("changePassword")}
            </Button>
          </form>
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <h3 className="text-base font-medium">{t("setPasswordTitle")}</h3>
            <p className="text-sm text-muted-foreground">
              {t("setPasswordDescription")}
            </p>
          </div>

          <form onSubmit={handleSetPassword} className="space-y-4 max-w-sm">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("newPasswordLabel")} <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={setNewPw}
                onChange={(e) => setSetNewPw(e.target.value)}
                placeholder={t("newPasswordPlaceholder")}
              />
              {setNewTooShort && (
                <p className="text-xs text-destructive">
                  {t("passwordMinLength", { min: MIN_PASSWORD_LENGTH })}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">{t("confirmSetPasswordLabel")} <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={setConfirmPw}
                onChange={(e) => setSetConfirmPw(e.target.value)}
                placeholder={t("confirmSetPasswordPlaceholder")}
              />
              {setConfirmMismatch && (
                <p className="text-xs text-destructive">
                  {t("passwordMismatch")}
                </p>
              )}
            </div>

            {setPwError && (
              <p className="text-sm text-destructive">{setPwError}</p>
            )}

            <Button type="submit" size="sm" disabled={!canSetPw}>
              {setPwSaving ? t("setting") : t("setPassword")}
            </Button>
          </form>
        </div>
      )}

      <Separator />

      {/* Danger Zone */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium text-destructive">
            {t("dangerZoneTitle")}
          </h3>
          <p className="text-sm text-muted-foreground">
            {t("dangerZoneDescription")}
          </p>
        </div>

        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-muted-foreground">
            {t("deleteAccountUnavailable")}
          </p>
        </div>
      </div>
    </div>
  )
}
