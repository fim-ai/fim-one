"use client"

import { useState, useEffect, useCallback, useRef, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useTranslations, useLocale } from "next-intl"
import { useAuth } from "@/contexts/auth-context"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { InputOTP, InputOTPGroup, InputOTPSlot, InputOTPSeparator } from "@/components/ui/input-otp"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Loader2, Globe, Sun, Moon } from "lucide-react"
import { APP_NAME, getApiBaseUrl, getApiDirectUrl } from "@/lib/constants"
import { authApi, ApiError } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { toast } from "sonner"
import { AnimatedLogo } from "@/components/layout/animated-logo"
import { useTheme } from "next-themes"

function LoginPageInner() {
  const t = useTranslations("auth")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()
  const { user, isLoading: authLoading, login, loginWithCode, register } = useAuth()
  const { resolvedTheme, setTheme } = useTheme()
  const router = useRouter()
  const searchParams = useSearchParams()

  // Login form state
  const [loginEmail, setLoginEmail] = useState("")
  const [loginPassword, setLoginPassword] = useState("")
  const [loginLoading, setLoginLoading] = useState(false)

  // OTP login state
  const [otpLoginMode, setOtpLoginMode] = useState(false)
  const [otpEmail, setOtpEmail] = useState("")
  const [otpStep, setOtpStep] = useState<"email" | "code">("email")
  const [otpCode, setOtpCode] = useState("")
  const [otpSending, setOtpSending] = useState(false)
  const [otpVerifying, setOtpVerifying] = useState(false)
  const [otpResendCountdown, setOtpResendCountdown] = useState(0)
  const [smtpConfigured, setSmtpConfigured] = useState(false)

  // Forgot password state
  const [forgotMode, setForgotMode] = useState(false)
  const [forgotStep, setForgotStep] = useState<"email" | "code" | "password">("email")
  const [forgotEmail, setForgotEmail] = useState("")
  const [forgotCode, setForgotCode] = useState("")
  const [forgotNewPassword, setForgotNewPassword] = useState("")
  const [forgotConfirmPassword, setForgotConfirmPassword] = useState("")
  const [forgotSending, setForgotSending] = useState(false)
  const [forgotSubmitting, setForgotSubmitting] = useState(false)
  const [forgotResendCountdown, setForgotResendCountdown] = useState(0)
  const [forgotResetToken, setForgotResetToken] = useState("")
  const [forgotVerifying, setForgotVerifying] = useState(false)

  // Register form state
  const [regEmail, setRegEmail] = useState("")
  const [regPassword, setRegPassword] = useState("")
  const [regConfirm, setRegConfirm] = useState("")
  const [regInviteCode, setRegInviteCode] = useState("")
  const [regLoading, setRegLoading] = useState(false)

  // Inline field errors
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  // Clear a field error when user starts typing
  const clearFieldError = (field: string) => {
    setFieldErrors(prev => {
      if (!prev[field]) return prev
      const next = { ...prev }
      delete next[field]
      return next
    })
  }

  // Email verification state
  const [emailVerificationEnabled, setEmailVerificationEnabled] = useState(false)
  const [verificationStep, setVerificationStep] = useState(false)
  const [verificationCode, setVerificationCode] = useState("")
  const [sendingCode, setSendingCode] = useState(false)
  const [resendCountdown, setResendCountdown] = useState(0)

  // Setup status check
  const [setupChecking, setSetupChecking] = useState(true)

  // Registration status
  const [registrationEnabled, setRegistrationEnabled] = useState(true)
  const [registrationMode, setRegistrationMode] = useState<"open" | "invite" | "disabled">("open")

  // OAuth state
  const [oauthProviders, setOauthProviders] = useState<string[]>([])

  // Redirect if already logged in (also fires after login/register when user state updates)
  useEffect(() => {
    if (!authLoading && user) {
      if (!user.onboarding_completed) {
        router.replace("/onboarding")
      } else {
        router.replace("/")
      }
    }
  }, [authLoading, user, router])

  // Check if system needs first-time setup, and fetch registration status
  useEffect(() => {
    authApi
      .setupStatus()
      .then((res) => {
        if (!res.initialized) {
          router.replace("/setup")
        } else {
          setSetupChecking(false)
          // Fetch registration status after confirming system is initialized
          fetch(`${getApiBaseUrl()}/api/auth/registration-status`)
            .then((r) => (r.ok ? r.json() : null))
            .then((data) => {
              if (data) {
                if (typeof data.registration_enabled === "boolean") {
                  setRegistrationEnabled(data.registration_enabled)
                }
                if (data.registration_mode) {
                  setRegistrationMode(data.registration_mode as "open" | "invite" | "disabled")
                }
                if (typeof data.email_verification_enabled === "boolean") {
                  setEmailVerificationEnabled(data.email_verification_enabled)
                }
                if (typeof data.smtp_configured === "boolean") {
                  setSmtpConfigured(data.smtp_configured)
                }
              }
            })
            .catch(() => {
              // Silently ignore — default to showing registration
            })
        }
      })
      .catch(() => {
        // If API call fails (old backend, network error), stay on login for backward compatibility
        setSetupChecking(false)
      })
  }, [router])

  // Check for OAuth error in URL params — fire exactly once (useRef guards against StrictMode double-invoke)
  const _oauthErrorHandled = useRef(false)
  useEffect(() => {
    if (_oauthErrorHandled.current) return
    const error = searchParams.get("error")
    if (!error) return
    _oauthErrorHandled.current = true
    if (error === "oauth_failed") {
      toast.error(t("oauthFailed"))
    } else if (error === "registration_disabled") {
      toast.error(t("oauthRegistrationDisabled"))
    } else if (error === "feishu_email_required") {
      toast.error(t("feishuEmailRequired"))
    } else {
      toast.error(error)
    }
    // Clear the error param so refreshing doesn't re-fire the toast
    router.replace("/login")
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Fetch available OAuth providers
  useEffect(() => {
    fetch(`${getApiBaseUrl()}/api/auth/oauth/providers`)
      .then((res) => {
        if (res.ok) return res.json()
        return { providers: [] }
      })
      .then((data) => {
        if (Array.isArray(data.providers)) {
          setOauthProviders(data.providers)
        }
      })
      .catch(() => {
        // Silently ignore — OAuth buttons simply won't appear
      })
  }, [])

  // Resend countdown timer (registration)
  useEffect(() => {
    if (resendCountdown <= 0) return
    const timer = setTimeout(() => setResendCountdown(resendCountdown - 1), 1000)
    return () => clearTimeout(timer)
  }, [resendCountdown])

  // Resend countdown timer (OTP login)
  useEffect(() => {
    if (otpResendCountdown <= 0) return
    const timer = setTimeout(() => setOtpResendCountdown(otpResendCountdown - 1), 1000)
    return () => clearTimeout(timer)
  }, [otpResendCountdown])

  // Resend countdown timer (forgot password)
  useEffect(() => {
    if (forgotResendCountdown <= 0) return
    const timer = setTimeout(() => setForgotResendCountdown(forgotResendCountdown - 1), 1000)
    return () => clearTimeout(timer)
  }, [forgotResendCountdown])

  const handleSendCode = async () => {
    if (!regEmail.trim()) {
      setFieldErrors(prev => ({ ...prev, regEmail: t("emailRequired") }))
      return
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(regEmail)) {
      setFieldErrors(prev => ({ ...prev, regEmail: t("emailInvalid") }))
      return
    }
    setSendingCode(true)
    try {
      await authApi.sendVerificationCode(regEmail, locale)
      setVerificationStep(true)
      setResendCountdown(60)
      setVerificationCode("")
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setSendingCode(false)
    }
  }

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoginLoading(true)
    try {
      await login({ email: loginEmail, password: loginPassword })
      // Redirect is handled by the useEffect that watches user state
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setFieldErrors(prev => ({ ...prev, login: getErrorMessage(err, tError) }))
      } else {
        toast.error(getErrorMessage(err, tError))
      }
    } finally {
      setLoginLoading(false)
    }
  }

  const handleSendLoginCode = async () => {
    if (!otpEmail.trim()) return
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(otpEmail)) return
    setOtpSending(true)
    try {
      await authApi.sendLoginCode(otpEmail, locale)
      setOtpStep("code")
      setOtpResendCountdown(60)
      setOtpCode("")
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setOtpSending(false)
    }
  }

  const doLoginWithCode = useCallback(async (codeOverride?: string) => {
    setOtpVerifying(true)
    try {
      await loginWithCode({ email: otpEmail, code: codeOverride || otpCode })
      // Redirect is handled by the useEffect that watches user state
    } catch (err) {
      setFieldErrors(prev => ({ ...prev, otpCode: getErrorMessage(err, tError) }))
      setOtpCode("")
    } finally {
      setOtpVerifying(false)
    }
  }, [loginWithCode, otpEmail, otpCode, tError])

  // Accept optional codeOverride for auto-submit from OTP onComplete
  const doRegister = useCallback(async (codeOverride?: string) => {
    setRegLoading(true)
    try {
      await register({
        password: regPassword,
        email: regEmail,
        invite_code: regInviteCode.trim() || undefined,
        verification_code: codeOverride || verificationCode.trim() || undefined,
      })
      // Redirect is handled by the useEffect that watches user state
    } catch (err) {
      // Show inline error for field-level issues like "email already registered"
      if (err instanceof ApiError && err.errorCode === "email_already_registered") {
        setFieldErrors(prev => ({ ...prev, regEmail: getErrorMessage(err, tError) }))
        // Go back to form view so user can see the inline error
        setVerificationStep(false)
      } else {
        toast.error(getErrorMessage(err, tError))
      }
      // Clear OTP so user can re-enter
      setVerificationCode("")
    } finally {
      setRegLoading(false)
    }
  }, [register, regPassword, regEmail, regInviteCode, verificationCode, tError])

  const handleSendForgotCode = async () => {
    if (!forgotEmail.trim()) return
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(forgotEmail)) return
    setForgotSending(true)
    try {
      await authApi.sendForgotCode(forgotEmail, locale)
      setForgotStep("code")
      setForgotResendCountdown(60)
      setForgotCode("")
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setForgotSending(false)
    }
  }

  const doVerifyForgotCode = useCallback(async (codeOverride?: string) => {
    setForgotVerifying(true)
    try {
      const result = await authApi.verifyForgotCode({
        email: forgotEmail,
        code: codeOverride || forgotCode,
      })
      setForgotResetToken(result.data.reset_token)
      setForgotStep("password")
    } catch (err) {
      setFieldErrors(prev => ({ ...prev, forgotCode: getErrorMessage(err, tError) }))
      setForgotCode("")
    } finally {
      setForgotVerifying(false)
    }
  }, [forgotEmail, forgotCode, tError])

  const handleForgotPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (forgotNewPassword.length < 8 || forgotNewPassword !== forgotConfirmPassword) return
    setForgotSubmitting(true)
    try {
      await authApi.forgotPassword({
        email: forgotEmail,
        reset_token: forgotResetToken,
        new_password: forgotNewPassword,
      })
      toast.success(t("passwordResetSuccess"))
      // Return to login form
      setForgotMode(false)
      setForgotStep("email")
      setForgotCode("")
      setForgotNewPassword("")
      setForgotConfirmPassword("")
      setForgotEmail("")
      setForgotResetToken("")
    } catch (err) {
      setFieldErrors(prev => ({ ...prev, forgotPassword: getErrorMessage(err, tError) }))
    } finally {
      setForgotSubmitting(false)
    }
  }

  const handleCancelForgot = () => {
    setForgotMode(false)
    setForgotStep("email")
    setForgotEmail("")
    setForgotCode("")
    setForgotNewPassword("")
    setForgotConfirmPassword("")
    setForgotResetToken("")
  }

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()

    // Validate fields
    const errors: Record<string, string> = {}
    if (!regEmail.trim()) {
      errors.regEmail = t("emailRequired")
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(regEmail)) {
      errors.regEmail = t("emailInvalid")
    }
    if (regPassword.length < 8) {
      errors.regPassword = t("passwordMinLength")
    }
    if (regPassword !== regConfirm) {
      errors.regConfirm = t("passwordsDoNotMatch")
    }
    if (Object.keys(errors).length > 0) {
      setFieldErrors(prev => ({ ...prev, ...errors }))
      return
    }

    // If email verification is enabled and we haven't sent code yet, send it first
    if (emailVerificationEnabled && !verificationStep) {
      await handleSendCode()
      return
    }

    await doRegister()
  }

  const handleLanguageSwitch = (lang: string) => {
    const cookieValue = lang === "auto" ? "" : lang
    document.cookie = cookieValue
      ? `NEXT_LOCALE=${cookieValue}; path=/; max-age=${60 * 60 * 24 * 365}`
      : "NEXT_LOCALE=; path=/; max-age=0"
    router.refresh()
  }

  if (authLoading || setupChecking) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (user) return null // Will redirect

  return (
    <div className="flex min-h-screen bg-background">
      {/* Left panel — brand / hero, always dark, hidden on mobile */}
      <div className="login-brand-panel hidden lg:flex w-[45%] shrink-0 flex-col justify-between px-14 py-10 text-white">
        {/* Mesh gradient background */}
        <div className="login-mesh-bg" aria-hidden="true">
          <div className="mesh-orb" />
        </div>

        {/* Top — logo */}
        <AnimatedLogo appName={APP_NAME} />

        {/* Middle-lower — tagline */}
        <div className="relative z-10 -mt-8">
          <h1
            className="text-[2.75rem] font-bold leading-[1.1] tracking-tight text-white"
            style={{ fontFamily: 'var(--font-cabinet), sans-serif' }}
          >
            {t("brandTagline").split("\n").map((line, i) => (
              <span key={i} className="hero-title-line">{line}</span>
            ))}
          </h1>
          <p className="mt-4 text-base leading-relaxed text-white/55">
            <span className="hero-line">{t("brandLine1")}</span>
            <span className="hero-line">{t("brandLine2")}</span>
            <span className="hero-line">{t("brandLine3")}</span>
          </p>
        </div>

        {/* Bottom — copyright */}
        <div className="relative z-10">
          <p className="text-xs text-white/35">&copy; {new Date().getFullYear()} {APP_NAME}</p>
        </div>
      </div>

      {/* Right panel — form, follows light/dark theme */}
      <div className="relative flex flex-1 flex-col items-center justify-center bg-background px-6 py-12">
        {/* Theme & Language switcher */}
        <div className="absolute top-4 right-4 flex items-center gap-1">
          <button
            type="button"
            className="flex items-center justify-center rounded-md p-1.5 text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
          >
            {resolvedTheme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            onClick={() => handleLanguageSwitch(locale === "zh" ? "en" : "zh")}
          >
            <Globe className="h-3.5 w-3.5" />
            {locale === "zh" ? "English" : "中文"}
          </button>
        </div>

        <div className="w-full max-w-sm">
          {/* Mobile-only logo (< lg) */}
          <div className="mb-8 flex items-center justify-center gap-2 lg:hidden">
            <img src="/fim-mark-light.svg" alt="FIM" className="h-8 w-auto dark:hidden" />
            <img src="/fim-mark.svg" alt="FIM" className="h-8 w-auto hidden dark:block" />
            <span
              className="text-lg font-bold"
              style={{ fontFamily: 'var(--font-cabinet), sans-serif' }}
            >
              {APP_NAME}
            </span>
          </div>

          {/* Heading */}
          <div className="mb-6 text-center lg:text-left">
            <h2 className="text-xl font-semibold tracking-tight">{t("loginWelcome")}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("loginSubtitle")}
            </p>
          </div>

          {/* OAuth Buttons — only in open registration mode; invite/disabled hides them to prevent bypassing invite codes */}
          {oauthProviders.length > 0 && registrationMode === "open" && (
            <>
              <div className="space-y-2">
                {oauthProviders.includes("github") && (
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={() => {
                      window.location.href = `${getApiDirectUrl()}/api/auth/oauth/github/authorize`
                    }}
                  >
                    <svg className="mr-2 h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
                    </svg>
                    {t("continueWithGithub")}
                  </Button>
                )}
                {oauthProviders.includes("google") && (
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={() => {
                      window.location.href = `${getApiDirectUrl()}/api/auth/oauth/google/authorize`
                    }}
                  >
                    <svg className="mr-2 h-4 w-4" viewBox="0 0 24 24">
                      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
                      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
                      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
                      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
                    </svg>
                    {t("continueWithGoogle")}
                  </Button>
                )}
                {oauthProviders.includes("discord") && (
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={() => {
                      window.location.href = `${getApiDirectUrl()}/api/auth/oauth/discord/authorize`
                    }}
                  >
                    <svg className="mr-2 h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M20.317 4.37a19.791 19.791 0 00-4.885-1.515.074.074 0 00-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 00-5.487 0 12.64 12.64 0 00-.617-1.25.077.077 0 00-.079-.037A19.736 19.736 0 003.677 4.37a.07.07 0 00-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 00.031.057 19.9 19.9 0 005.993 3.03.078.078 0 00.084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 00-.041-.106 13.107 13.107 0 01-1.872-.892.077.077 0 01-.008-.128 10.2 10.2 0 00.372-.292.074.074 0 01.077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 01.078.01c.12.098.246.198.373.292a.077.077 0 01-.006.127 12.299 12.299 0 01-1.873.892.077.077 0 00-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 00.084.028 19.839 19.839 0 006.002-3.03.077.077 0 00.032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 00-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" />
                    </svg>
                    {t("continueWithDiscord")}
                  </Button>
                )}
                {oauthProviders.includes("feishu") && (
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={() => {
                      window.location.href = `${getApiDirectUrl()}/api/auth/oauth/feishu/authorize`
                    }}
                  >
                    <svg className="mr-2 h-4 w-4" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
                      <path d="M770.91584 373.312c-2.688 0.128-3.392-1.088-3.648-3.648-0.32-2.688-0.128-5.952-1.472-8.064-2.56-4.032-1.856-8.768-4.032-12.928a23.04 23.04 0 0 1-2.56-7.04c-0.128-1.536 0.256-3.584-0.832-4.352-2.304-1.664-1.408-4.416-2.88-6.592-1.6-2.368-1.728-5.76-2.944-8.64-1.28-3.008-2.752-6.208-3.072-9.536-0.128-1.6-1.92-1.664-1.92-3.264 0.192-2.688-1.792-4.928-2.432-7.488-0.896-3.392-2.816-6.72-4.096-10.048a78.528 78.528 0 0 0-3.392-7.936c-1.856-3.648-3.392-7.36-5.504-10.944-2.048-3.2-2.56-7.232-4.8-10.688a59.2 59.2 0 0 1-4.672-8.96c-2.112-4.992-5.248-9.344-7.68-14.08a196.48 196.48 0 0 0-7.488-13.824c-2.56-4.288-4.8-8.832-7.808-12.992-1.216-1.728-3.072-3.392-3.392-5.312-0.448-2.752-2.56-4.736-3.84-6.4-1.856-2.112-1.92-6.016-5.504-6.848 0 0-0.128-0.32-0.064-0.512 0.256-2.88-3.84-3.712-3.456-6.72-3.2-1.088-2.24-5.696-5.824-6.592 0 0-0.192-0.256-0.128-0.384 0.32-2.944-2.56-4.544-3.776-6.464a48.96 48.96 0 0 0-5.504-6.848c-0.96-1.024-2.56-1.92-2.88-3.2-1.28-4.352-5.44-6.528-7.552-10.368-1.984-3.52-5.44-5.824-8.128-8.768a28.16 28.16 0 0 0-9.536-7.296c-3.392-1.28-6.144-4.096-10.24-4.352-4.224-0.192-8.256-2.688-12.672-2.56C617.18784 128.704 616.86784 128 615.71584 128H172.38784c-1.216 0-1.472 0.704-1.472 1.664-2.944-0.192-4.544 2.24-5.504 3.968-2.176 3.84 0.96 10.944 5.184 12.672 1.792 0.64 2.752 1.92 3.968 3.072 0.64 0.64 1.28 1.664 2.176 1.536 1.792-0.192 2.56 1.088 3.072 2.112 0.64 1.408 1.92 1.92 2.944 2.432a37.12 37.12 0 0 1 8.512 6.016c3.776 3.52 8 6.4 12.16 9.408 3.392 2.496 6.144 5.76 9.92 7.808 2.816 1.472 4.352 4.736 7.04 6.08 3.84 1.92 5.952 5.568 9.6 7.424a19.328 19.328 0 0 1 5.632 4.992c1.472 1.664 4.096 1.792 5.312 3.456 3.392 5.12 9.28 7.424 13.312 12.032 3.904 4.48 9.472 7.168 13.568 11.904 4.864 5.632 11.392 9.6 16.64 14.912 3.712 3.648 7.424 7.04 11.136 10.56 2.944 2.752 5.888 5.44 8.64 8.32 2.048 2.048 4.672 3.648 6.592 5.44 4.736 4.608 10.24 8.704 13.568 14.656l0.704 0.896c3.648 3.392 7.296 6.848 10.88 10.368 4.352 4.352 8.576 9.088 13.312 13.248a17.92 17.92 0 0 1 4.864 5.376c0.96 2.24 2.56 3.648 4.096 5.12 4.032 3.84 8.384 7.552 11.264 12.544 1.152 2.048 3.584 2.752 4.8 4.736 1.6 2.496 4.032 4.224 5.632 6.912 2.368 3.84 6.4 6.528 9.088 10.56 2.624 3.968 6.144 7.744 9.536 11.136 3.776 3.84 6.4 8.448 10.432 11.904 2.112 1.792 2.304 5.12 5.056 6.208 1.344 0.64 1.792 1.792 2.176 2.752 1.472 3.136 4.16 5.312 5.888 8.256 1.664 2.88 4.48 4.736 6.4 7.616 3.52 5.376 7.552 10.56 11.904 15.36 1.408 1.6 1.28 4.096 3.328 4.864 3.072 1.28 3.584 4.544 5.12 6.72 2.112 2.88 4.864 5.312 6.4 8.448 1.664 3.328 4.416 5.824 6.144 8.96 1.664 2.752 3.712 5.248 5.376 8 1.28 2.112 2.432 4.48 4.224 6.4 1.92 1.92 2.56 4.928 4.864 6.784 2.048 1.664 2.432 4.352 4.352 6.272 1.984 2.176 2.816 5.44 4.992 7.552 1.92 1.792 2.816 4.288 4.16 6.4 0.96 1.472 2.944 2.368 3.2 4.352 0.192 2.176 1.856 3.52 2.944 5.12 1.664 2.496 3.84 4.928 4.8 7.296 1.792 4.416 4.8 7.744 6.912 11.712 2.496 4.352 5.568 8.768 8.064 13.312 1.92 3.264 3.776 6.592 5.824 9.6 0.896 1.6 0.704 3.648 2.432 4.8 2.048 1.472 2.496 3.968 3.648 6.016 1.024 1.792 1.728 4.032 3.2 5.248 1.152 1.024 1.152 1.92 1.408 3.2 1.728 0.64 2.624-0.704 3.648-1.664l8.192-8.128 16.896-16.64c4.8-4.8 9.28-9.856 14.272-14.464 8.768-8 16.832-16.64 25.6-24.768 5.696-5.312 10.816-11.2 17.088-16 5.504-4.16 10.112-9.472 15.104-14.336l11.008-11.008c3.84-3.84 8.768-6.272 12.288-10.368a3.072 3.072 0 0 1 1.088-0.96c3.712-1.472 6.272-4.48 9.344-6.72 3.584-2.56 7.168-5.12 10.624-7.808 1.792-1.408 4.032-2.048 5.568-3.584 4.416-4.416 10.24-6.4 15.36-9.472 5.44-3.2 11.008-6.4 16.64-9.088 4.096-1.92 7.68-4.928 12.032-5.76 5.12-1.088 8.768-4.48 13.568-6.016 1.472-0.384 3.264-0.576 4.608-1.6 2.752-2.176 6.528-2.176 9.6-3.648 1.472-0.704 3.136-1.664 4.8-1.92 3.84-0.448 7.168-2.24 10.88-3.2 0.96-0.192 1.792-0.768 0.896-2.048" fill="#00D6B9"/>
                      <path d="M876.19584 641.28c-1.024-0.64-1.728 0.256-2.368 0.896-1.92 1.792-3.392 4.096-5.056 6.144l-8.384 9.856c-5.76 6.336-12.032 12.416-18.304 18.112-4.928 4.48-10.368 8.32-15.68 12.288-1.92 1.472-3.904 3.072-5.952 4.416-2.24 1.536-4.16 3.584-6.592 4.608-4.608 1.92-8.704 4.8-12.992 7.296-7.424 4.096-15.168 7.68-23.04 10.752-4.48 1.856-8.896 3.84-13.44 5.248-3.712 1.152-7.296 2.752-11.008 3.648-1.28 0.32-2.56 0.192-3.776 0.64-3.648 1.344-7.488 2.176-11.328 3.136-3.264 0.832-6.848 0.128-9.728 1.472-4.352 2.048-9.344 0.128-13.44 2.88-6.4 0-12.736 0.64-19.072 1.216-11.264 0.832-22.528-0.768-33.792-1.088-5.12-0.128-9.984-2.688-15.168-1.92a1.088 1.088 0 0 1-0.832-0.32c-1.664-1.28-3.648-1.28-5.504-1.28-3.968 0-7.808-0.832-11.52-1.536-3.648-0.768-7.36-2.176-10.944-3.392-2.112-0.704-4.416-0.32-6.336-1.28a32.576 32.576 0 0 0-8.768-2.752c-7.04-1.472-13.504-4.416-20.608-5.568-4.672-0.768-8.768-3.392-13.44-4.352-3.328-0.768-6.528-1.92-9.664-2.944-4.032-1.28-8.064-2.688-12.16-3.712-6.528-1.792-12.48-4.544-18.88-6.592-7.616-2.56-14.976-5.504-22.592-8.064-4.48-1.472-8.448-4.16-13.312-4.928a16.768 16.768 0 0 1-5.824-2.176c-3.584-2.048-7.68-2.752-11.264-4.48-3.072-1.536-6.528-2.24-9.536-3.712-4.992-2.56-10.752-3.328-15.296-6.912a1.92 1.92 0 0 0-1.088-0.32c-3.648-0.384-6.912-2.048-10.112-3.648-6.912-3.392-14.4-5.632-21.184-9.344-5.44-3.008-11.392-4.736-16.832-7.68-1.28-0.256-2.368 0-3.648-0.64-2.24-1.152-4.416-2.944-6.72-3.584-4.48-1.216-7.808-4.48-12.224-5.76-2.496-0.704-4.416-2.944-6.72-3.52-2.944-0.704-5.376-2.176-7.68-3.52-3.84-2.176-8.064-3.648-11.968-6.016-2.112-1.344-4.864-1.792-6.72-3.328-2.752-2.304-7.04-2.176-8.96-5.696-4.672-0.384-7.808-3.968-11.84-5.632-4.8-1.92-9.088-4.992-13.632-7.552-1.92-1.088-3.648-3.072-5.504-3.392-4.928-1.024-8.32-4.544-12.416-6.784a142.72 142.72 0 0 1-13.888-8.32c-0.768-0.448-1.856-0.384-2.368-0.96-2.88-3.264-7.04-4.928-10.688-7.168a484.608 484.608 0 0 1-10.56-6.4c-4.352-2.752-8.768-5.44-12.864-8.32-2.752-1.984-6.144-3.2-8.512-5.44-1.728-1.6-3.84-2.176-5.632-3.648-3.2-2.624-6.976-4.8-10.432-7.168-1.6-1.088-3.776-2.112-4.864-3.328-2.112-2.304-4.992-3.52-7.232-5.376-3.392-2.88-7.616-4.864-10.944-7.872-1.984-1.728-4.928-2.048-6.4-4.48-0.512-0.96-1.472-1.664-2.752-1.984-2.24-0.64-3.776-2.432-5.568-3.648-2.688-1.856-5.12-4.352-7.936-6.272-3.328-2.24-6.272-5.248-9.728-7.296-3.712-2.24-6.336-5.76-10.048-7.872-2.304-1.408-3.648-3.968-5.888-4.992-3.904-1.728-6.272-5.184-9.856-7.296-2.752-1.792-4.672-5.12-7.616-6.592-3.456-1.856-5.504-5.056-8.576-7.168-0.832-0.64-1.92-0.768-2.688-1.792-1.92-2.368-4.672-4.16-6.912-6.272-2.432-2.176-5.44-3.648-7.68-6.272-1.728-1.92-3.584-4.48-5.76-5.376-3.392-1.28-4.864-4.224-7.488-6.144-3.328-2.304-5.76-5.696-8.96-8.256-2.048-1.6-4.352-2.88-5.952-4.928-2.432-3.072-5.504-5.696-8.32-8.32C63.58784 417.92 60.06784 414.208 56.35584 410.752c-2.944-2.56-5.632-5.504-8.64-8.192-2.752-2.432-5.248-5.248-8.064-7.68C35.93984 391.424 32.54784 387.584 29.09184 384 25.63584 380.416 21.92384 377.024 18.53184 373.312 15.65184 370.048 12.70784 366.08 7.07584 367.232c-2.816 0.64-4.096 2.368-5.12 4.608-2.88 0.32-1.728 2.56-1.728 3.776v414.208c0 1.664 0.192 3.264 0.128 4.864 0 1.28 0.384 1.856 1.6 1.92-0.768 3.84 1.792 6.848 2.368 10.368 0.384 2.56 1.984 4.608 2.944 6.976 1.408 3.2 4.096 5.888 6.016 8.96a24.96 24.96 0 0 0 6.4 6.72c2.688 1.92 5.376 3.904 8.192 5.632 3.392 1.92 6.592 4.288 9.728 6.464 0.96 0.64 2.048 0.768 3.072 1.728a47.36 47.36 0 0 0 10.24 6.4c2.432 1.28 4.736 2.944 7.36 4.032 5.824 2.432 10.816 6.784 16.832 9.152 3.328 1.28 5.888 3.648 9.344 4.672 0.768 0.32 1.728 0.384 2.432 0.832 4.8 3.328 10.24 5.248 15.488 7.872 1.088 0.64 2.496 0.64 3.328 1.28a21.504 21.504 0 0 0 9.536 4.288c0.64 0.128 1.152 0.192 1.856 0.704 1.408 1.088 3.2 1.92 4.928 2.88 1.92 1.088 4.416 0.448 6.144 2.304 0.64 0.768 2.24 1.728 3.456 1.92 5.12 0.96 9.856 3.392 14.656 5.248 1.408 0.64 3.328 0 4.352 1.152 1.92 1.92 4.608 1.92 6.784 2.88 3.2 1.408 6.592 2.56 10.24 3.392 1.472 0.384 3.584 0 4.544 1.088 1.856 2.048 4.608 2.176 6.592 2.688 5.248 1.28 10.368 2.944 15.488 4.672 4.096 1.472 8.704 1.792 13.056 3.072 3.648 1.024 7.296 2.24 10.944 2.88 1.728 0.32 4.16-0.384 5.12 0.512 2.816 2.752 6.592 1.152 9.728 2.688a23.552 23.552 0 0 0 11.264 1.792c0.768-0.064 1.856-0.256 2.24 0.128 2.88 2.944 7.424 0 10.24 3.008 6.528 0.576 13.12 0.896 19.584 2.752 4.16 1.28 8.768 0.64 13.184 1.6 5.312 1.28 11.072 0.896 16.64 1.472 3.648 0.32 7.232 0.192 10.88 0.256 0 1.152 0.64 1.6 1.792 1.536h78.976c1.152 0 1.856-0.384 1.856-1.536 8.768 0.448 17.408-1.6 26.112-1.28h4.8c1.216 0 1.856-0.448 1.792-1.664 3.904-0.256 7.808-0.704 11.712-1.472 6.144-1.28 12.544-0.96 18.688-2.944 3.072-1.024 6.592-0.832 9.856-1.664 3.84-0.896 7.872-1.92 12.032-1.6 0.448 0 1.152 0 1.408-0.192 2.048-2.752 5.44-1.472 8.192-2.752a21.184 21.184 0 0 1 9.472-1.664c0.448 0 1.024 0 1.472-0.192a23.488 23.488 0 0 1 9.344-3.008 62.08 62.08 0 0 0 12.992-3.392 56.96 56.96 0 0 1 9.6-2.56c0.64-0.128 1.472-0.128 1.856-0.576 1.856-1.92 4.48-1.92 6.592-2.56 4.224-1.088 8.256-2.688 12.416-3.968 1.408-0.32 3.008 0.064 4.16-0.704a25.792 25.792 0 0 1 10.88-4.416 3.392 3.392 0 0 0 1.792-0.576c1.92-1.152 3.648-2.56 6.08-2.816 3.328-0.384 6.592-2.048 9.472-3.52 2.56-1.28 5.312-2.24 7.872-3.52 1.92-1.024 4.096-2.304 6.464-2.752 3.456-0.576 5.76-3.52 9.216-4.288a25.984 25.984 0 0 0 7.488-3.2c3.712-2.048 7.616-3.84 11.392-5.632 2.432-1.28 5.184-2.304 7.168-3.712a54.336 54.336 0 0 1 9.536-5.312c4.224-1.92 8-4.608 12.16-6.4 3.712-1.728 7.04-4.416 10.624-6.4 1.856-1.088 3.52-2.688 5.312-3.584 1.984-1.024 3.968-2.176 5.824-3.392 2.496-1.728 5.44-2.752 7.552-4.864 1.92-1.92 4.48-2.56 6.4-4.288 2.368-1.984 5.504-2.944 7.68-5.12 0.768-0.768 1.216-1.472 2.24-1.472a3.52 3.52 0 0 0 2.688-1.856 6.272 6.272 0 0 1 2.944-2.496c3.456-1.216 5.248-4.48 8.448-6.144 1.984-0.896 3.648-2.752 5.504-4.096 3.2-2.176 6.144-4.48 9.152-6.848 1.664-1.28 3.136-3.584 4.8-4.096 3.712-1.152 4.8-5.12 8.192-6.592a11.328 11.328 0 0 0 3.2-2.368c2.56-2.88 5.76-5.248 8.768-8.064 1.792-1.92 4.288-2.816 6.08-4.864a52.608 52.608 0 0 1 6.848-6.912 57.984 57.984 0 0 0 6.144-5.888l10.944-10.88c3.648-3.52 7.104-7.104 10.752-10.56a45.824 45.824 0 0 0 5.888-6.848 37.056 37.056 0 0 1 6.592-6.976c1.92-1.536 2.496-3.84 4.224-5.44a31.104 31.104 0 0 0 6.656-7.552c1.92-3.392 5.312-5.504 7.168-8.96 1.28-2.432 3.84-4.096 5.376-6.528 1.6-2.624 3.712-4.992 5.696-7.36 2.176-2.56 4.672-5.376 6.144-8.32 1.536-3.2 4.288-5.12 5.76-8.32a49.536 49.536 0 0 1 5.696-8.064c1.472-1.856 2.368-3.84 3.584-5.76 1.92-2.944 3.968-5.76 6.016-8.64 0.768-1.28 0.96-2.56 2.048-3.84 1.92-2.048 4.16-4.48 3.648-7.872" fill="#3370FF"/>
                      <path d="M1022.49984 392.32c-0.384-0.576-0.832-0.576-1.408-0.704-1.664-0.512-3.456-0.896-4.928-1.728a30.08 30.08 0 0 0-5.12-2.944c-2.688-1.088-5.824-1.472-8.128-3.072-3.136-2.432-7.04-2.368-10.432-4.096a26.24 26.24 0 0 0-7.68-2.752c-1.984-0.32-4.096-1.28-6.08-1.92-2.688-0.832-5.568-1.792-8.32-2.56a163.84 163.84 0 0 1-12.992-3.712 22.848 22.848 0 0 0-9.216-1.536c-0.704-2.944-3.136-1.216-4.8-1.6-2.176 0-4.224-1.088-6.272-1.408-6.656-1.216-13.44-1.408-20.224-2.944-5.312-1.152-11.136-0.512-16.64-1.28-3.328-0.512-6.592-0.192-9.984-0.256 0-1.28-0.64-1.6-1.792-1.6h-32.128c-1.152 0-1.856 0.32-1.856 1.6-8.192-0.448-16.192 1.344-24.384 1.28h-4.864c-1.152 0-1.664 0.448-1.728 1.536-4.672-0.384-9.088 1.664-13.76 1.28a1.472 1.472 0 0 0-0.832 0.256c-3.584 2.56-7.936 1.664-11.84 2.944-3.072 1.024-6.784 0.896-9.728 1.984-2.368 0.832-4.992 1.472-7.296 2.432a21.312 21.312 0 0 1-9.152 1.792c0.064 0.64 0.192 1.28-0.64 1.408-1.792 0.128-3.456 1.088-5.12 1.472-4.8 1.28-9.344 3.072-14.016 4.672-4.096 1.472-7.872 3.648-11.904 4.608-4.608 1.088-7.808 4.736-12.672 5.312a8.064 8.064 0 0 0-4.16 1.472 15.04 15.04 0 0 1-4.48 2.752 30.08 30.08 0 0 0-7.296 3.584c-3.328 2.176-7.04 3.648-10.24 5.76-1.28 0.96-3.264 0.576-3.84 1.536-2.368 4.096-7.168 4.16-10.496 6.72-3.52 2.688-7.68 4.736-11.2 7.36-2.88 2.24-5.952 4.288-8.768 6.592-3.648 3.2-7.616 6.016-11.52 8.768-1.984 1.472-3.2 4.224-5.376 4.864-3.328 1.152-5.12 3.968-7.296 6.016-2.176 2.176-4.672 4.48-6.912 6.784-3.328 3.456-6.912 6.656-10.432 9.984-2.624 2.56-4.928 5.632-7.808 7.552-4.416 2.944-7.68 6.784-11.264 10.24-3.648 3.456-7.36 7.04-10.88 10.624-4.032 4.16-8.576 7.872-12.48 12.16-3.328 3.84-7.232 7.168-10.816 10.624-3.648 3.584-7.04 7.424-10.944 10.88-3.84 3.456-7.424 7.04-10.88 10.816-2.752 2.88-5.696 5.76-8.576 8.512-2.944 2.816-5.44 6.08-9.088 8.064-0.384 1.792-1.856 2.496-3.2 3.392-3.776 2.368-6.784 5.696-10.048 8.768-2.176 1.92-3.968 4.608-6.4 6.016-4.16 2.176-7.04 5.824-10.688 8.448-4.416 3.2-8.96 6.528-12.8 10.368-1.92 1.856-4.864 2.176-6.08 4.544-1.024 2.048-3.648 1.6-4.928 3.2-2.56 3.456-6.72 4.864-9.792 7.68-1.856 1.664-4.416 3.008-6.592 4.352-1.408 0.704-2.944 1.28-4.096 2.368a47.36 47.36 0 0 1-9.408 6.528c-1.472 0.896-3.456 1.472-4.544 2.752-2.048 2.176-4.928 3.2-7.232 5.12a29.568 29.568 0 0 1-7.744 4.672c-3.712 1.408-6.464 3.968-9.856 5.632-4.48 2.176-8.768 4.864-13.248 7.04-1.152 0.64-3.136 1.024-3.776 2.56 0 1.088 0.448 1.344 1.28 1.92 2.048 1.152 4.224 1.408 6.208 2.24 3.2 1.472 6.272 3.264 9.472 4.608 3.072 1.28 5.632 3.328 9.28 3.648 1.664 0.32 3.2 1.088 4.48 2.176 2.176 1.92 4.992 2.624 7.296 3.52 2.048 0.704 4.032 2.048 6.4 2.496 1.472 0.256 3.456 0.192 4.608 1.088 2.048 1.6 4.352 2.496 6.592 3.84 1.472 0.832 3.392-0.32 4.288 0.96 1.28 1.856 3.52 2.176 4.928 2.688 4.16 1.408 8 3.136 12.16 4.8 3.456 1.472 7.04 2.496 10.496 4.416 2.88 1.536 6.656 1.6 9.92 2.944 1.28 0.64 1.92 1.792 3.328 1.92 3.2 0.192 6.208 1.152 8.96 2.688 0.896 0.64 1.856 1.664 2.944 1.92 3.2 0.64 6.4 1.408 9.536 2.688 2.88 1.216 5.952 2.56 9.024 2.944 1.728 0.256 2.048 1.92 3.712 1.92 2.816 0 5.12 1.856 7.808 2.432 3.648 0.832 7.232 2.176 10.816 3.264 0.832 0.256 2.176-0.128 2.56 0.32 2.176 2.88 5.824 1.408 8.768 2.88 3.2 1.6 7.296 2.368 11.008 3.584 3.264 1.216 6.528 2.432 10.048 2.56a2.56 2.56 0 0 1 1.6 0.512 26.24 26.24 0 0 0 7.808 2.944c1.28 0.256 2.56 0.512 3.84 0.64 2.368 0.384 4.224 1.984 6.528 2.112 3.328 0.192 6.208 2.24 9.728 2.368 1.28 0.064 3.52-0.128 4.608 0.96 2.24 2.24 5.504 2.112 8.064 2.752 5.504 1.472 11.456 1.152 17.152 3.008 3.328 1.152 7.04 0.768 10.688 1.6 5.824 1.28 12.16 0.896 18.432 1.28 11.52 0.704 23.04 0.448 34.432-0.896 2.944-0.384 5.632-0.512 8.512-1.28 4.096-1.024 8.256-1.664 12.416-1.984 3.584-0.32 6.912-1.856 10.56-1.472a1.344 1.344 0 0 0 0.704-0.256c3.2-1.92 6.848-1.92 10.112-3.072 2.368-0.704 5.12-0.64 7.04-1.792a20.096 20.096 0 0 1 6.72-2.56c3.648-0.64 6.208-3.648 9.92-3.392l0.448-0.256c2.496-1.92 5.568-2.944 8.32-3.84 3.072-1.088 6.016-2.304 8.896-3.712 3.84-1.92 7.424-4.416 11.136-6.592 1.152-0.64 2.752 0.32 3.52-1.472 0.64-1.472 1.984-2.176 3.712-2.56 1.92-0.512 4.096-1.28 4.864-3.456 0.384-0.896 1.088-1.024 1.856-0.896 1.024 0 1.92-0.512 2.432-1.408 2.368-3.52 6.4-5.12 9.6-7.488 3.392-2.304 6.016-5.44 9.536-7.552 4.16-2.432 7.168-6.336 10.496-9.664 2.88-2.944 5.568-5.952 8.576-8.576 2.368-2.048 3.456-4.864 5.76-6.912 1.664-1.408 4.224-2.88 4.864-5.12 1.024-3.392 3.84-4.8 6.144-6.912a13.312 13.312 0 0 1 3.584-4.608l0.896-1.408c1.216-0.704 0.576-2.56 1.92-3.328 2.304-1.536 3.2-4.416 4.224-6.592 1.6-3.392 3.52-6.656 5.44-9.792 2.048-3.456 3.328-7.296 5.888-10.496 1.92-2.496 3.136-5.76 4.48-8.704a369.92 369.92 0 0 1 7.808-15.104 130.56 130.56 0 0 0 4.48-9.088c1.408-3.2 3.2-6.208 4.736-9.28 1.472-3.2 3.52-6.144 4.672-9.28 1.472-3.904 3.648-7.168 5.312-10.88 0.512-1.344 0.384-2.944 1.28-3.904a18.688 18.688 0 0 0 4.16-7.168c1.472-3.84 4.736-6.912 5.12-11.2l0.256-0.128c1.472-1.152 1.984-2.944 2.816-4.48 1.28-2.432 1.92-5.248 3.52-7.488 2.496-3.52 3.904-7.424 5.824-11.2 1.28-2.304 2.176-4.928 3.52-7.04 2.176-3.456 3.648-7.36 5.76-10.752a43.776 43.776 0 0 0 3.584-7.68c0.64-1.792 1.92-3.52 3.008-5.12 1.984-3.072 3.392-6.592 5.952-9.344 1.088-1.152 1.024-2.752 2.304-4.096 2.24-2.176 3.392-5.12 5.12-7.68 1.28-1.92 2.24-4.224 3.776-5.696a47.232 47.232 0 0 0 7.04-9.344c1.984-3.2 4.544-6.016 6.848-8.704 3.712-4.48 7.68-8.96 11.84-13.44l11.136-11.776c2.176-2.304 1.6-2.816 0-4.416" fill="#133C9A"/>
                    </svg>
                    {t("continueWithFeishu")}
                  </Button>
                )}
              </div>

              {/* Separator */}
              <div className="relative my-4">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-background px-2 text-muted-foreground">{t("orDivider")}</span>
                </div>
              </div>
            </>
          )}

          <Tabs defaultValue="login" className="w-full">
            <TabsList className={`grid w-full ${registrationEnabled ? "grid-cols-2" : "grid-cols-1"}`}>
              <TabsTrigger value="login">{tc("login")}</TabsTrigger>
              {registrationEnabled && <TabsTrigger value="register">{tc("register")}</TabsTrigger>}
            </TabsList>
            <TabsContent value="login">
              {forgotMode ? (
                forgotStep === "email" ? (
                  <div className="space-y-4 pt-4">
                    <p className="text-sm text-muted-foreground">
                      {t("forgotPasswordSubtitle")}
                    </p>
                    <Input
                      type="email"
                      placeholder={t("emailPlaceholder")}
                      value={forgotEmail}
                      onChange={(e) => setForgotEmail(e.target.value)}
                      autoFocus
                      autoComplete="email"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault()
                          handleSendForgotCode()
                        }
                      }}
                    />
                    <Button
                      type="button"
                      className="w-full"
                      disabled={forgotSending || !forgotEmail.trim()}
                      onClick={handleSendForgotCode}
                    >
                      {forgotSending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                      {t("sendResetCode")}
                    </Button>
                    <div className="text-center">
                      <button
                        type="button"
                        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                        onClick={handleCancelForgot}
                      >
                        {t("backToLogin")}
                      </button>
                    </div>
                  </div>
                ) : forgotStep === "code" ? (
                  <div className="space-y-4 pt-4">
                    <p className="text-sm text-muted-foreground">
                      {t("resetCodeSent", { email: forgotEmail })}
                    </p>
                    <div className="flex justify-center">
                      <InputOTP
                        maxLength={6}
                        value={forgotCode}
                        onChange={(v) => { setForgotCode(v); clearFieldError("forgotCode") }}
                        onComplete={(code) => doVerifyForgotCode(code)}
                        disabled={forgotVerifying}
                        autoFocus
                      >
                        <InputOTPGroup>
                          <InputOTPSlot index={0} />
                          <InputOTPSlot index={1} />
                          <InputOTPSlot index={2} />
                        </InputOTPGroup>
                        <InputOTPSeparator />
                        <InputOTPGroup>
                          <InputOTPSlot index={3} />
                          <InputOTPSlot index={4} />
                          <InputOTPSlot index={5} />
                        </InputOTPGroup>
                      </InputOTP>
                    </div>
                    {fieldErrors.forgotCode && (
                      <p className="text-sm text-destructive text-center">{fieldErrors.forgotCode}</p>
                    )}
                    {forgotVerifying && (
                      <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        {t("verifying")}
                      </div>
                    )}
                    <div className="flex items-center justify-between">
                      <button
                        type="button"
                        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                        onClick={handleCancelForgot}
                      >
                        {t("backToLogin")}
                      </button>
                      <button
                        type="button"
                        className="text-sm text-primary hover:text-primary/80 transition-colors disabled:text-muted-foreground disabled:cursor-not-allowed"
                        disabled={forgotResendCountdown > 0 || forgotSending}
                        onClick={handleSendForgotCode}
                      >
                        {forgotSending
                          ? t("sendingCode")
                          : forgotResendCountdown > 0
                            ? t("resendCodeIn", { seconds: forgotResendCountdown })
                            : t("resendCode")}
                      </button>
                    </div>
                  </div>
                ) : (
                  <form onSubmit={handleForgotPasswordSubmit} className="space-y-4 pt-4">
                    <p className="text-sm text-muted-foreground">
                      {t("codeVerifiedSetPassword")}
                    </p>
                    <div className="space-y-2">
                      <Input
                        type="password"
                        placeholder={t("newPasswordPlaceholder")}
                        value={forgotNewPassword}
                        onChange={(e) => { setForgotNewPassword(e.target.value); clearFieldError("forgotPassword") }}
                        autoComplete="new-password"
                        autoFocus
                      />
                      <Input
                        type="password"
                        placeholder={t("confirmNewPasswordPlaceholder")}
                        value={forgotConfirmPassword}
                        onChange={(e) => { setForgotConfirmPassword(e.target.value); clearFieldError("forgotPassword") }}
                        autoComplete="new-password"
                      />
                    </div>
                    {fieldErrors.forgotPassword && (
                      <p className="text-sm text-destructive">{fieldErrors.forgotPassword}</p>
                    )}
                    {forgotConfirmPassword.length > 0 && forgotNewPassword !== forgotConfirmPassword && (
                      <p className="text-sm text-destructive">{t("passwordsMustMatch")}</p>
                    )}
                    <Button
                      type="submit"
                      className="w-full"
                      disabled={
                        forgotSubmitting ||
                        forgotNewPassword.length < 8 ||
                        forgotNewPassword !== forgotConfirmPassword
                      }
                    >
                      {forgotSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                      {forgotSubmitting ? t("resettingPassword") : t("resetPassword")}
                    </Button>
                    <div className="text-center">
                      <button
                        type="button"
                        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                        onClick={handleCancelForgot}
                      >
                        {t("backToLogin")}
                      </button>
                    </div>
                  </form>
                )
              ) : !otpLoginMode ? (
                /* Password login mode */
                <form onSubmit={handleLogin} className="space-y-4 pt-4">
                  <div className="space-y-2">
                    <Input
                      type="email"
                      placeholder={t("emailPlaceholder")}
                      value={loginEmail}
                      onChange={(e) => { setLoginEmail(e.target.value); clearFieldError("login") }}
                      required
                      autoFocus
                      autoComplete="email"
                    />
                    <Input
                      type="password"
                      placeholder={t("passwordPlaceholder")}
                      value={loginPassword}
                      onChange={(e) => { setLoginPassword(e.target.value); clearFieldError("login") }}
                      required
                      autoComplete="current-password"
                    />
                    {fieldErrors.login && (
                      <p className="text-sm text-destructive">{fieldErrors.login}</p>
                    )}
                  </div>
                  <Button type="submit" className="w-full" disabled={loginLoading}>
                    {loginLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    {t("signIn")}
                  </Button>
                  {smtpConfigured && (
                    <div className="flex items-center justify-between">
                      <button
                        type="button"
                        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                        onClick={() => {
                          setForgotMode(true)
                          setForgotStep("email")
                          if (loginEmail.trim()) setForgotEmail(loginEmail.trim())
                        }}
                      >
                        {t("forgotPassword")}
                      </button>
                      <button
                        type="button"
                        className="text-sm text-primary hover:text-primary/80 transition-colors"
                        onClick={() => setOtpLoginMode(true)}
                      >
                        {t("loginWithEmailCode")}
                      </button>
                    </div>
                  )}
                </form>
              ) : otpStep === "email" ? (
                /* OTP login — email step */
                <div className="space-y-4 pt-4">
                  <p className="text-sm text-muted-foreground">
                    {t("otpLoginSubtitle")}
                  </p>
                  <Input
                    type="email"
                    placeholder={t("emailPlaceholder")}
                    value={otpEmail}
                    onChange={(e) => setOtpEmail(e.target.value)}
                    autoFocus
                    autoComplete="email"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault()
                        handleSendLoginCode()
                      }
                    }}
                  />
                  <Button
                    type="button"
                    className="w-full"
                    disabled={otpSending || !otpEmail.trim()}
                    onClick={handleSendLoginCode}
                  >
                    {otpSending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    {t("sendLoginCode")}
                  </Button>
                  <div className="text-center">
                    <button
                      type="button"
                      className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                      onClick={() => {
                        setOtpLoginMode(false)
                        setOtpStep("email")
                        setOtpCode("")
                        setOtpEmail("")
                      }}
                    >
                      {t("backToPasswordLogin")}
                    </button>
                  </div>
                </div>
              ) : (
                /* OTP login — code step */
                <div className="space-y-4 pt-4">
                  <p className="text-sm text-muted-foreground">
                    {t("loginCodeSent", { email: otpEmail })}
                  </p>
                  <div className="flex justify-center">
                    <InputOTP
                      maxLength={6}
                      value={otpCode}
                      onChange={(v) => { setOtpCode(v); clearFieldError("otpCode") }}
                      onComplete={(code) => doLoginWithCode(code)}
                      disabled={otpVerifying}
                      autoFocus
                    >
                      <InputOTPGroup>
                        <InputOTPSlot index={0} />
                        <InputOTPSlot index={1} />
                        <InputOTPSlot index={2} />
                      </InputOTPGroup>
                      <InputOTPSeparator />
                      <InputOTPGroup>
                        <InputOTPSlot index={3} />
                        <InputOTPSlot index={4} />
                        <InputOTPSlot index={5} />
                      </InputOTPGroup>
                    </InputOTP>
                  </div>
                  {fieldErrors.otpCode && (
                    <p className="text-sm text-destructive text-center">{fieldErrors.otpCode}</p>
                  )}
                  {otpVerifying && (
                    <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      {t("signingIn")}
                    </div>
                  )}
                  <div className="flex items-center justify-between">
                    <button
                      type="button"
                      className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                      onClick={() => {
                        setOtpStep("email")
                        setOtpCode("")
                        clearFieldError("otpCode")
                      }}
                    >
                      {t("changeEmail")}
                    </button>
                    <button
                      type="button"
                      className="text-sm text-primary hover:text-primary/80 transition-colors disabled:text-muted-foreground disabled:cursor-not-allowed"
                      disabled={otpResendCountdown > 0 || otpSending}
                      onClick={handleSendLoginCode}
                    >
                      {otpSending
                        ? t("sendingCode")
                        : otpResendCountdown > 0
                          ? t("resendCodeIn", { seconds: otpResendCountdown })
                          : t("resendCode")}
                    </button>
                  </div>
                  <div className="text-center">
                    <button
                      type="button"
                      className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                      onClick={() => {
                        setOtpLoginMode(false)
                        setOtpStep("email")
                        setOtpCode("")
                        setOtpEmail("")
                      }}
                    >
                      {t("backToPasswordLogin")}
                    </button>
                  </div>
                </div>
              )}
            </TabsContent>
            {registrationEnabled && <TabsContent value="register">
              <form onSubmit={handleRegister} className="space-y-4 pt-4">
                {!verificationStep ? (
                  <>
                    <div className="space-y-2">
                      <div>
                        <Input
                          type="email"
                          placeholder={t("emailPlaceholder")}
                          value={regEmail}
                          onChange={(e) => { setRegEmail(e.target.value); clearFieldError("regEmail") }}
                          required
                          autoComplete="email"
                        />
                        {fieldErrors.regEmail && (
                          <p className="mt-1 text-sm text-destructive">{fieldErrors.regEmail}</p>
                        )}
                      </div>
                      <div>
                        <Input
                          type="password"
                          placeholder={t("passwordMinLengthPlaceholder")}
                          value={regPassword}
                          onChange={(e) => { setRegPassword(e.target.value); clearFieldError("regPassword") }}
                          required
                          minLength={8}
                          autoComplete="new-password"
                        />
                        {fieldErrors.regPassword && (
                          <p className="mt-1 text-sm text-destructive">{fieldErrors.regPassword}</p>
                        )}
                      </div>
                      <div>
                        <Input
                          type="password"
                          placeholder={t("confirmPasswordPlaceholder")}
                          value={regConfirm}
                          onChange={(e) => { setRegConfirm(e.target.value); clearFieldError("regConfirm") }}
                          required
                          autoComplete="new-password"
                        />
                        {fieldErrors.regConfirm && (
                          <p className="mt-1 text-sm text-destructive">{fieldErrors.regConfirm}</p>
                        )}
                      </div>
                      {registrationMode === "invite" && (
                        <Input
                          placeholder={t("inviteCodePlaceholder")}
                          value={regInviteCode}
                          onChange={(e) => setRegInviteCode(e.target.value)}
                          autoComplete="off"
                          required
                        />
                      )}
                    </div>
                    <Button type="submit" className="w-full" disabled={regLoading || sendingCode}>
                      {(regLoading || sendingCode) && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                      {emailVerificationEnabled && !sendingCode ? t("verifyEmail") : sendingCode ? t("sendingCode") : t("createAccount")}
                    </Button>
                  </>
                ) : (
                  <>
                    <div className="space-y-4">
                      <p className="text-sm text-muted-foreground">
                        {t("verificationCodeSent", { email: regEmail })}
                      </p>
                      <div className="flex justify-center">
                        <InputOTP
                          maxLength={6}
                          value={verificationCode}
                          onChange={setVerificationCode}
                          onComplete={(code) => doRegister(code)}
                          disabled={regLoading}
                          autoFocus
                        >
                          <InputOTPGroup>
                            <InputOTPSlot index={0} />
                            <InputOTPSlot index={1} />
                            <InputOTPSlot index={2} />
                          </InputOTPGroup>
                          <InputOTPSeparator />
                          <InputOTPGroup>
                            <InputOTPSlot index={3} />
                            <InputOTPSlot index={4} />
                            <InputOTPSlot index={5} />
                          </InputOTPGroup>
                        </InputOTP>
                      </div>
                      {regLoading && (
                        <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          {t("verifying")}
                        </div>
                      )}
                      <div className="flex items-center justify-between">
                        <button
                          type="button"
                          className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                          onClick={() => {
                            setVerificationStep(false)
                            setVerificationCode("")
                          }}
                        >
                          {t("changeEmail")}
                        </button>
                        <button
                          type="button"
                          className="text-sm text-primary hover:text-primary/80 transition-colors disabled:text-muted-foreground disabled:cursor-not-allowed"
                          disabled={resendCountdown > 0 || sendingCode}
                          onClick={handleSendCode}
                        >
                          {sendingCode
                            ? t("sendingCode")
                            : resendCountdown > 0
                              ? t("resendCodeIn", { seconds: resendCountdown })
                              : t("resendCode")}
                        </button>
                      </div>
                    </div>
                  </>
                )}
              </form>
            </TabsContent>}
          </Tabs>
        </div>

        {/* Footer links — locale-aware */}
        <div className="absolute bottom-10 left-0 right-0 flex justify-center gap-4">
          <a
            href={`https://agent.fim.ai/${locale}/privacy`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            {t("privacyPolicy")}
          </a>
          <a
            href={`https://agent.fim.ai/${locale}/terms`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            {t("termsOfService")}
          </a>
        </div>
      </div>
    </div>
  )
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center bg-background">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <LoginPageInner />
    </Suspense>
  )
}
