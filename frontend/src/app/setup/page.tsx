"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Loader2, ShieldCheck } from "lucide-react"
import { APP_NAME, ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, USER_KEY } from "@/lib/constants"
import { authApi, ApiError } from "@/lib/api"

export default function SetupPage() {
  const router = useRouter()

  // Setup status check
  const [checking, setChecking] = useState(true)

  // Form state
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [email, setEmail] = useState("")
  const [error, setError] = useState("")
  const [submitting, setSubmitting] = useState(false)

  // On mount: check if already logged in, then check setup status
  useEffect(() => {
    // If already logged in, go to home
    const token = localStorage.getItem(ACCESS_TOKEN_KEY)
    if (token) {
      router.replace("/")
      return
    }

    // Check if system is already initialized
    authApi
      .setupStatus()
      .then((res) => {
        if (res.initialized) {
          router.replace("/login")
        } else {
          setChecking(false)
        }
      })
      .catch(() => {
        // If endpoint doesn't exist or fails, redirect to login for safety
        router.replace("/login")
      })
  }, [router])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")

    if (password !== confirmPassword) {
      setError("Passwords do not match")
      return
    }

    if (password.length < 6) {
      setError("Password must be at least 6 characters")
      return
    }

    setSubmitting(true)
    try {
      if (!email.trim()) {
        setError("Email is required")
        setSubmitting(false)
        return
      }
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        setError("Please enter a valid email address")
        setSubmitting(false)
        return
      }

      const data = await authApi.setup({ username, password, email: email.trim() })

      // Store tokens (same pattern as login)
      localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token)
      localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token)
      localStorage.setItem(USER_KEY, JSON.stringify(data.user))

      // Full page navigation to ensure auth context reloads
      window.location.href = "/"
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        // System already initialized — someone else set it up
        router.replace("/login")
        return
      }
      setError(err instanceof Error ? err.message : "Setup failed")
    } finally {
      setSubmitting(false)
    }
  }

  // Show loading while checking setup status
  if (checking) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex min-h-screen" style={{ background: 'oklch(0.13 0.008 55)' }}>
      {/* Left panel -- brand / hero, always dark, hidden on mobile */}
      <div className="login-brand-panel hidden lg:flex w-[45%] shrink-0 flex-col justify-between px-14 py-10 text-white">
        {/* Mesh gradient background */}
        <div className="login-mesh-bg" aria-hidden="true">
          <div className="mesh-orb" />
        </div>

        {/* Top -- logo */}
        <div className="relative z-10 flex items-center gap-2.5">
          <img
            src="/fim-mark.svg"
            alt="FIM"
            className="h-6 w-auto brightness-0 invert"
          />
          <span
            className="text-xl font-bold tracking-tight text-white/90"
            style={{ fontFamily: 'var(--font-cabinet), sans-serif' }}
          >
            {APP_NAME}
          </span>
        </div>

        {/* Middle-lower -- tagline */}
        <div className="relative z-10 -mt-8">
          <h1
            className="text-[2.75rem] font-bold leading-[1.1] tracking-tight text-white"
            style={{ fontFamily: 'var(--font-cabinet), sans-serif' }}
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

        {/* Bottom -- copyright */}
        <div className="relative z-10">
          <p className="text-xs text-white/35">&copy; 2026 {APP_NAME}</p>
        </div>
      </div>

      {/* Right panel -- form, follows light/dark theme */}
      <div className="flex flex-1 flex-col items-center justify-center bg-background/[0.88] backdrop-blur-sm px-6 py-12">
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

          {/* One-time setup badge */}
          <div className="mb-4 flex justify-center lg:justify-start">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
              <ShieldCheck className="h-3.5 w-3.5" />
              One-time setup
            </span>
          </div>

          {/* Heading */}
          <div className="mb-6 text-center lg:text-left">
            <h2 className="text-xl font-semibold tracking-tight">Initialize System</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Create your admin account to get started
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Input
                placeholder="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                minLength={2}
                autoFocus
                autoComplete="username"
              />
              <Input
                type="password"
                placeholder="Password (min 6 characters)"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                autoComplete="new-password"
              />
              <Input
                type="password"
                placeholder="Confirm password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                autoComplete="new-password"
              />
              <Input
                type="email"
                placeholder="Email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </div>
            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create Admin Account
            </Button>
          </form>
        </div>
      </div>
    </div>
  )
}
