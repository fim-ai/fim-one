"use client"

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
} from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { authApi, setAuthFailureCallback } from "@/lib/api"
import {
  ACCESS_TOKEN_KEY,
  REFRESH_TOKEN_KEY,
  USER_KEY,
} from "@/lib/constants"
import type { UserInfo, LoginRequest, LoginWithCodeRequest, RegisterRequest } from "@/types/auth"

/** Decode JWT payload and return the `exp` field in milliseconds, or null on failure. */
function getTokenExpiry(token: string): number | null {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.exp ? payload.exp * 1000 : null
  } catch {
    return null
  }
}

interface AuthContextValue {
  user: UserInfo | null
  isLoading: boolean
  login: (body: LoginRequest) => Promise<void>
  loginWithCode: (body: LoginWithCodeRequest) => Promise<void>
  register: (body: RegisterRequest) => Promise<void>
  logout: () => void
  updateUser: (partial: Partial<UserInfo>) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const router = useRouter()
  const tError = useTranslations("errors")

  const clearAuth = useCallback(() => {
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    setUser(null)
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = null
    }
  }, [])

  const saveTokens = useCallback(
    (accessToken: string, refreshToken: string, userInfo: UserInfo) => {
      localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
      localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
      localStorage.setItem(USER_KEY, JSON.stringify(userInfo))
      setUser(userInfo)
    },
    [],
  )

  const scheduleRefresh = useCallback(
    (expiresIn: number) => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      // Refresh 5 min before expiry, minimum 60s
      const delay = Math.max((expiresIn - 300) * 1000, 60_000)
      refreshTimerRef.current = setTimeout(async () => {
        const rt = localStorage.getItem(REFRESH_TOKEN_KEY)
        if (!rt) return
        try {
          const data = await authApi.refresh(rt)
          saveTokens(data.access_token, data.refresh_token, data.user)
          scheduleRefresh(data.expires_in)
        } catch {
          clearAuth()
          const currentPath = window.location.pathname
          const redirectParam = currentPath && currentPath !== "/" ? `?redirect=${encodeURIComponent(currentPath)}` : ""
          router.replace(`/login${redirectParam}`)
        }
      }, delay)
    },
    [saveTokens, clearAuth, router],
  )

  // Sync preferred_language → NEXT_LOCALE cookie for next-intl
  const syncLocaleCookie = useCallback((lang: string | undefined) => {
    if (!lang || lang === "auto") {
      document.cookie = "NEXT_LOCALE=; path=/; max-age=0"
    } else {
      document.cookie = `NEXT_LOCALE=${lang}; path=/; max-age=${60 * 60 * 24 * 365}`
    }
  }, [])

  // Register auth failure callback for api.ts
  useEffect(() => {
    setAuthFailureCallback(() => {
      clearAuth()
      toast.error(tError("session_expired"))
      // Carry current path so login page can redirect back after re-auth
      const currentPath = window.location.pathname
      const redirectParam = currentPath && currentPath !== "/" ? `?redirect=${encodeURIComponent(currentPath)}` : ""
      router.replace(`/login${redirectParam}`)
    })
    return () => setAuthFailureCallback(null)
  }, [clearAuth, router, tError])

  // Initial token check on mount
  useEffect(() => {
    const token = localStorage.getItem(ACCESS_TOKEN_KEY)
    const savedUser = localStorage.getItem(USER_KEY)
    if (token && savedUser) {
      try {
        const parsed = JSON.parse(savedUser) as UserInfo
        setUser(parsed)
        syncLocaleCookie(parsed.preferred_language)
        const expiry = getTokenExpiry(token)
        const expiresIn = expiry
          ? Math.max(Math.floor((expiry - Date.now()) / 1000), 0)
          : 7200 // fallback to 2h if JWT has no exp
        scheduleRefresh(expiresIn)
      } catch {
        clearAuth()
      }
    }
    setIsLoading(false)
    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const login = useCallback(
    async (body: LoginRequest) => {
      const data = await authApi.login(body)
      saveTokens(data.access_token, data.refresh_token, data.user)
      syncLocaleCookie(data.user.preferred_language)
      scheduleRefresh(data.expires_in)
    },
    [saveTokens, syncLocaleCookie, scheduleRefresh],
  )

  const loginWithCode = useCallback(
    async (body: LoginWithCodeRequest) => {
      const data = await authApi.loginWithCode(body)
      saveTokens(data.access_token, data.refresh_token, data.user)
      syncLocaleCookie(data.user.preferred_language)
      scheduleRefresh(data.expires_in)
    },
    [saveTokens, syncLocaleCookie, scheduleRefresh],
  )

  const register = useCallback(
    async (body: RegisterRequest) => {
      const data = await authApi.register(body)
      saveTokens(data.access_token, data.refresh_token, data.user)
      syncLocaleCookie(data.user.preferred_language)
      scheduleRefresh(data.expires_in)
    },
    [saveTokens, syncLocaleCookie, scheduleRefresh],
  )

  const updateUser = useCallback(
    (partial: Partial<UserInfo>) => {
      setUser((prev) => {
        if (!prev) return prev
        const updated = { ...prev, ...partial }
        localStorage.setItem(USER_KEY, JSON.stringify(updated))
        return updated
      })
    },
    [],
  )

  const logout = useCallback(() => {
    clearAuth()
    router.replace("/login")
  }, [clearAuth, router])

  return (
    <AuthContext.Provider value={{ user, isLoading, login, loginWithCode, register, logout, updateUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
