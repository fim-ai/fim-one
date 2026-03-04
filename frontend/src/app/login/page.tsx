"use client"

import { useState, useEffect, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useAuth } from "@/contexts/auth-context"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Loader2 } from "lucide-react"
import { APP_NAME, getApiBaseUrl, getApiDirectUrl } from "@/lib/constants"
import { authApi } from "@/lib/api"

function LoginPageInner() {
  const { user, isLoading: authLoading, login, register } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()

  // Login form state
  const [loginUsername, setLoginUsername] = useState("")
  const [loginPassword, setLoginPassword] = useState("")
  const [loginError, setLoginError] = useState("")
  const [loginLoading, setLoginLoading] = useState(false)

  // Register form state
  const [regUsername, setRegUsername] = useState("")
  const [regEmail, setRegEmail] = useState("")
  const [regPassword, setRegPassword] = useState("")
  const [regConfirm, setRegConfirm] = useState("")
  const [regError, setRegError] = useState("")
  const [regLoading, setRegLoading] = useState(false)

  // Setup status check
  const [setupChecking, setSetupChecking] = useState(true)

  // OAuth state
  const [oauthProviders, setOauthProviders] = useState<string[]>([])
  const [oauthError, setOauthError] = useState("")

  // Redirect if already logged in
  useEffect(() => {
    if (!authLoading && user) {
      router.replace("/")
    }
  }, [authLoading, user, router])

  // Check if system needs first-time setup
  useEffect(() => {
    authApi
      .setupStatus()
      .then((res) => {
        if (!res.initialized) {
          router.replace("/setup")
        } else {
          setSetupChecking(false)
        }
      })
      .catch(() => {
        // If API call fails (old backend, network error), stay on login for backward compatibility
        setSetupChecking(false)
      })
  }, [router])

  // Check for OAuth error in URL params
  useEffect(() => {
    const error = searchParams.get("error")
    if (error) {
      if (error === "oauth_failed") {
        setOauthError("OAuth authentication failed. Please try again.")
      } else {
        setOauthError(error)
      }
    }
  }, [searchParams])

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

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoginError("")
    setLoginLoading(true)
    try {
      const isEmail = loginUsername.includes("@")
      await login({
        ...(isEmail ? { email: loginUsername } : { username: loginUsername }),
        password: loginPassword,
      })
      router.replace("/")
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : "Login failed")
    } finally {
      setLoginLoading(false)
    }
  }

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    setRegError("")
    if (!regEmail.trim()) {
      setRegError("Email is required")
      return
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(regEmail)) {
      setRegError("Please enter a valid email address")
      return
    }
    if (regPassword !== regConfirm) {
      setRegError("Passwords do not match")
      return
    }
    if (regPassword.length < 6) {
      setRegError("Password must be at least 6 characters")
      return
    }
    setRegLoading(true)
    try {
      await register({
        username: regUsername,
        password: regPassword,
        email: regEmail,
      })
      router.replace("/")
    } catch (err) {
      setRegError(err instanceof Error ? err.message : "Registration failed")
    } finally {
      setRegLoading(false)
    }
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
    <div className="flex min-h-screen">
      {/* Left panel — brand / hero, always dark, hidden on mobile */}
      <div className="login-brand-panel hidden lg:flex w-[45%] shrink-0 flex-col justify-between px-14 py-10 text-white">
        {/* Mesh gradient background */}
        <div className="login-mesh-bg" aria-hidden="true">
          <div className="mesh-orb" />
        </div>

        {/* Top — logo */}
        <div className="relative z-10 flex items-center gap-2.5">
          <img
            src="/fim-mark.svg"
            alt="FIM"
            className="h-6 w-auto brightness-0 invert"
          />
          <span
            className="text-xl font-bold tracking-tight text-white/90"
            style={{ fontFamily: '"Cabinet Grotesk", sans-serif' }}
          >
            {APP_NAME}
          </span>
        </div>

        {/* Middle-lower — tagline */}
        <div className="relative z-10 -mt-8">
          <h1
            className="text-[2.75rem] font-bold leading-[1.1] tracking-tight text-white"
            style={{ fontFamily: '"Cabinet Grotesk", sans-serif' }}
          >
            AI-Powered
            <br />
            Connector Hub
          </h1>
          <p className="mt-4 text-base leading-relaxed text-white/55">
            Connect any API.
            <br />
            Orchestrate with agents.
            <br />
            Ship faster.
          </p>
        </div>

        {/* Bottom — copyright */}
        <div className="relative z-10">
          <p className="text-xs text-white/35">&copy; 2026 {APP_NAME}</p>
        </div>
      </div>

      {/* Right panel — form, follows light/dark theme */}
      <div className="flex flex-1 flex-col items-center justify-center bg-background px-6 py-12">
        <div className="w-full max-w-sm">
          {/* Mobile-only logo (< lg) */}
          <div className="mb-8 flex items-center justify-center gap-2 lg:hidden">
            <img src="/fim-mark-light.svg" alt="FIM" className="h-8 w-auto dark:hidden" />
            <img src="/fim-mark.svg" alt="FIM" className="h-8 w-auto hidden dark:block" />
            <span
              className="text-lg font-bold"
              style={{ fontFamily: '"Cabinet Grotesk", sans-serif' }}
            >
              {APP_NAME}
            </span>
          </div>

          {/* Heading */}
          <div className="mb-6 text-center lg:text-left">
            <h2 className="text-xl font-semibold tracking-tight">Welcome</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Sign in to your account or create a new one
            </p>
          </div>

          {/* OAuth Error */}
          {oauthError && (
            <p className="text-sm text-destructive text-center mb-4">{oauthError}</p>
          )}

          {/* OAuth Buttons */}
          {oauthProviders.length > 0 && (
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
                    Continue with GitHub
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
                    Continue with Google
                  </Button>
                )}
              </div>

              {/* Separator */}
              <div className="relative my-4">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-background px-2 text-muted-foreground">or</span>
                </div>
              </div>
            </>
          )}

          <Tabs defaultValue="login" className="w-full">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="login">Login</TabsTrigger>
              <TabsTrigger value="register">Register</TabsTrigger>
            </TabsList>
            <TabsContent value="login">
              <form onSubmit={handleLogin} className="space-y-4 pt-4">
                <div className="space-y-2">
                  <Input
                    placeholder="Username or Email"
                    value={loginUsername}
                    onChange={(e) => setLoginUsername(e.target.value)}
                    required
                    autoFocus
                    autoComplete="username"
                  />
                  <Input
                    type="password"
                    placeholder="Password"
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    required
                    autoComplete="current-password"
                  />
                </div>
                {loginError && (
                  <p className="text-sm text-destructive">{loginError}</p>
                )}
                <Button type="submit" className="w-full" disabled={loginLoading}>
                  {loginLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Sign In
                </Button>
              </form>
            </TabsContent>
            <TabsContent value="register">
              <form onSubmit={handleRegister} className="space-y-4 pt-4">
                <div className="space-y-2">
                  <Input
                    placeholder="Username (min 2 characters)"
                    value={regUsername}
                    onChange={(e) => setRegUsername(e.target.value)}
                    required
                    minLength={2}
                    autoComplete="username"
                  />
                  <Input
                    type="email"
                    placeholder="Email"
                    value={regEmail}
                    onChange={(e) => setRegEmail(e.target.value)}
                    required
                    autoComplete="email"
                  />
                  <Input
                    type="password"
                    placeholder="Password (min 6 characters)"
                    value={regPassword}
                    onChange={(e) => setRegPassword(e.target.value)}
                    required
                    minLength={6}
                    autoComplete="new-password"
                  />
                  <Input
                    type="password"
                    placeholder="Confirm password"
                    value={regConfirm}
                    onChange={(e) => setRegConfirm(e.target.value)}
                    required
                    autoComplete="new-password"
                  />
                </div>
                {regError && (
                  <p className="text-sm text-destructive">{regError}</p>
                )}
                <Button type="submit" className="w-full" disabled={regLoading}>
                  {regLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Create Account
                </Button>
              </form>
            </TabsContent>
          </Tabs>
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
