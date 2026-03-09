"use client"

import { useEffect } from "react"
import { useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, USER_KEY } from "@/lib/constants"
import { Loader2 } from "lucide-react"

function CallbackHandler() {
  const searchParams = useSearchParams()

  useEffect(() => {
    // Errors still arrive as query params (server can't set fragments on error redirects easily)
    const error = searchParams.get("error")
    if (error) {
      window.location.href = `/login?error=${encodeURIComponent(error)}`
      return
    }

    // Tokens arrive in the URL fragment (#) so they never appear in server
    // logs, nginx access logs, or Referer headers.
    const hash = window.location.hash.substring(1) // remove leading #
    const params = new URLSearchParams(hash)
    const accessToken = params.get("access_token")
    const refreshToken = params.get("refresh_token")
    const userJson = params.get("user")

    if (accessToken && refreshToken && userJson) {
      try {
        // Validate user JSON is parseable
        const user = JSON.parse(userJson)
        localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
        localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
        localStorage.setItem(USER_KEY, JSON.stringify(user))
        // Restore redirect URL saved before OAuth navigation, then clean up
        const redirect = sessionStorage.getItem("fim_oauth_redirect")
        sessionStorage.removeItem("fim_oauth_redirect")
        const target = redirect && redirect.startsWith("/") ? redirect : "/"
        // Full page reload to re-initialize AuthProvider
        window.location.href = target
      } catch {
        window.location.href = "/login?error=oauth_failed"
      }
    } else {
      // No tokens in fragment — could be a direct visit or a broken redirect
      window.location.href = "/login?error=oauth_failed"
    }
  }, [searchParams])

  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  )
}

export default function AuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center bg-background">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <CallbackHandler />
    </Suspense>
  )
}
