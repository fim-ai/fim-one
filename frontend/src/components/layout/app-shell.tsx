"use client"

import { useState, useEffect, useRef } from "react"
import { usePathname, useRouter } from "next/navigation"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { Bot, Layers, Library, Loader2, MessagesSquare, Moon, PanelLeftClose, PanelLeftOpen, Plug, Plus, Search, Sun, Wrench, X } from "lucide-react"
import { getApiBaseUrl } from "@/lib/constants"
import { setMaintenanceCallback } from "@/lib/api"
import { cn } from "@/lib/utils"
import { APP_NAME } from "@/lib/constants"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useTheme } from "next-themes"
import { useAuth } from "@/contexts/auth-context"
import { ConversationProvider, useConversation } from "@/contexts/conversation-context"
import { ConversationSidebar } from "@/components/layout/conversation-sidebar"
import { ChatSearchDialog } from "@/components/layout/chat-search-dialog"
import { UserMenu } from "@/components/layout/user-menu"
import { NavigationProgress } from "@/components/layout/navigation-progress"

/** Wraps children in a right-side tooltip when the sidebar is collapsed. */
function SidebarTooltip({
  label,
  collapsed,
  children,
}: {
  label: string
  collapsed: boolean
  children: React.ReactNode
}) {
  if (!collapsed) return <>{children}</>
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right" sideOffset={8}>
        {label}
      </TooltipContent>
    </Tooltip>
  )
}

function SidebarNewChat({ collapsed }: { collapsed: boolean }) {
  const { clearActive } = useConversation()
  const router = useRouter()
  const pathname = usePathname()
  const t = useTranslations("layout")
  const tc = useTranslations("common")
  const [searchOpen, setSearchOpen] = useState(false)
  const [isMac, setIsMac] = useState(true) // default to Mac to avoid flash
  const isActive = pathname === "/new"

  useEffect(() => {
    const nav = navigator as Navigator & { userAgentData?: { platform?: string } }
    const platform = nav.userAgentData?.platform ?? navigator.platform ?? ""
    setIsMac(/mac|iphone|ipad|ipod/i.test(platform))
  }, [])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "o") {
        e.preventDefault()
        clearActive()
        router.push("/new")
      }
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [clearActive, router])

  const handleNewChat = () => {
    clearActive()
    router.push("/new")
  }

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-1 px-2 py-2 shrink-0">
        <SidebarTooltip label={isMac ? t("newChatTooltipMac", { shortcut: "⇧⌘O" }) : t("newChatTooltipWin", { shortcut: "Ctrl+Shift+O" })} collapsed>
          <button
            onClick={handleNewChat}
            className={cn(
              "flex h-9 w-9 items-center justify-center rounded-md transition-colors",
              isActive
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            )}
          >
            <Plus className="h-4 w-4" />
          </button>
        </SidebarTooltip>
        <SidebarTooltip label={isMac ? t("searchTooltipMac", { shortcut: "⌘K" }) : t("searchTooltipWin", { shortcut: "Ctrl+K" })} collapsed>
          <button
            onClick={() => setSearchOpen(true)}
            className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
          >
            <Search className="h-4 w-4" />
          </button>
        </SidebarTooltip>
        <ChatSearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
      </div>
    )
  }

  return (
    <div className="px-3 py-2 shrink-0">
      <Link
        href="/new"
        onClick={handleNewChat}
        className={cn(
          "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
          isActive
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
        )}
      >
        <span className="flex h-5 w-5 items-center justify-center rounded-md bg-foreground/10 text-foreground">
          <Plus className="h-3.5 w-3.5" />
        </span>
        <span>{t("newChat")}</span>
        <kbd className="ml-auto text-xs text-muted-foreground/40 font-normal tracking-[0.1em] opacity-0 group-hover:opacity-100 transition-opacity" style={{ fontFamily: "system-ui, -apple-system, sans-serif" }}>{isMac ? "⇧⌘O" : "Ctrl+Shift+O"}</kbd>
      </Link>
      <button
        onClick={() => setSearchOpen(true)}
        className="group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
      >
        <span className="flex h-5 w-5 items-center justify-center">
          <Search className="h-4 w-4" />
        </span>
        <span>{tc("search")}</span>
        <kbd className="ml-auto text-xs text-muted-foreground/40 font-normal tracking-[0.1em] opacity-0 group-hover:opacity-100 transition-opacity" style={{ fontFamily: "system-ui, -apple-system, sans-serif" }}>{isMac ? "⌘K" : "Ctrl+K"}</kbd>
      </button>
      <ChatSearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
    </div>
  )
}

function RedirectToLogin() {
  const router = useRouter()
  const pathname = usePathname()
  useEffect(() => {
    // Carry current path so the login page can redirect back after auth
    const redirectParam = pathname && pathname !== "/" ? `?redirect=${encodeURIComponent(pathname)}` : ""
    router.replace(`/login${redirectParam}`)
  }, [router, pathname])
  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  )
}

function ThemeToggle({ collapsed }: { collapsed: boolean }) {
  const { resolvedTheme, setTheme } = useTheme()
  const t = useTranslations("layout")
  const [mounted, setMounted] = useState(false)

  useEffect(() => setMounted(true), [])

  const toggle = () => setTheme(resolvedTheme === "dark" ? "light" : "dark")

  // Avoid hydration mismatch — render a placeholder until mounted
  if (!mounted) {
    return (
      <button
        className={cn(
          "inline-flex items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
          collapsed ? "h-9 w-9" : "h-8 w-8",
        )}
        disabled
      >
        <Sun className="h-4 w-4" />
      </button>
    )
  }

  return (
    <SidebarTooltip label={resolvedTheme === "dark" ? t("lightMode") : t("darkMode")} collapsed={collapsed}>
      <button
        onClick={toggle}
        className={cn(
          "inline-flex items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
          collapsed ? "h-9 w-9" : "h-8 w-8",
        )}
      >
        {resolvedTheme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </button>
    </SidebarTooltip>
  )
}

function SidebarFooter({ collapsed }: { collapsed: boolean }) {
  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-1">
        <ThemeToggle collapsed />
        <UserMenu collapsed />
      </div>
    )
  }

  return (
    <div className="flex items-center gap-1">
      <div className="flex-1 min-w-0">
        <UserMenu collapsed={false} />
      </div>
      <ThemeToggle collapsed={false} />
    </div>
  )
}

function AnnouncementBanner() {
  const t = useTranslations("layout")
  const [text, setText] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    fetch(`${getApiBaseUrl()}/api/auth/announcement`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.enabled && data?.text?.trim()) {
          setText(data.text.trim())
        }
      })
      .catch(() => {})
  }, [])

  if (!text || dismissed) return null

  return (
    <div className="flex items-center gap-3 bg-amber-500/15 border-b border-amber-500/30 px-4 py-2 text-sm text-amber-800 dark:text-amber-300 shrink-0">
      <span className="flex-1">{text}</span>
      <button
        onClick={() => setDismissed(true)}
        className="ml-auto shrink-0 rounded p-0.5 hover:bg-amber-500/20 transition-colors"
        aria-label={t("dismiss")}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

function MaintenanceOverlay() {
  const t = useTranslations("layout")
  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-background gap-6">
      <Wrench className="h-12 w-12 text-orange-500 animate-pulse" />
      <div className="text-center space-y-2">
        <h1 className="text-2xl font-semibold">{t("maintenanceTitle")}</h1>
        <p className="text-muted-foreground text-sm max-w-sm">
          {t("maintenanceDescription")}
        </p>
      </div>
    </div>
  )
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations("layout")
  const [isMaintenance, setIsMaintenance] = useState(false)

  useEffect(() => {
    setMaintenanceCallback(() => setIsMaintenance(true))
    return () => setMaintenanceCallback(null)
  }, [])

  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("sidebar-collapsed") === "true"
    }
    return false
  })

  useEffect(() => {
    localStorage.setItem("sidebar-collapsed", String(collapsed))
  }, [collapsed])

  const prevCollapsedRef = useRef(false)

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ active: boolean }>).detail
      if (detail.active) {
        prevCollapsedRef.current = collapsed
        setCollapsed(true)
      } else {
        setCollapsed(prevCollapsedRef.current)
      }
    }
    window.addEventListener("builder-mode-change", handler)
    return () => window.removeEventListener("builder-mode-change", handler)
  }, [collapsed])

  const pathname = usePathname()
  const { user, isLoading } = useAuth()

  // Loading state during auth check
  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (isMaintenance) return <MaintenanceOverlay />

  // Public pages: no sidebar, full-width content
  if (pathname === "/login" || pathname === "/auth/callback" || pathname === "/setup" || pathname === "/onboarding") {
    return <main className="h-screen bg-background">{children}</main>
  }

  // Not authenticated and not on login page — redirect to login
  if (!user) {
    return <RedirectToLogin />
  }

  // Authenticated: full layout with conversation sidebar
  return (
    <ConversationProvider>
      <NavigationProgress />
      <TooltipProvider delayDuration={300}>
      <div className="flex h-screen overflow-hidden bg-background">
        {/* Sidebar */}
        <aside
          className={cn(
            "flex flex-col border-r border-border bg-sidebar transition-all duration-200",
            collapsed ? "w-16" : "w-72",
          )}
        >
          {/* Logo area + collapse toggle */}
          <div className={cn("flex shrink-0", collapsed ? "items-center justify-center px-2 py-3" : "h-14 items-center justify-between px-4")}>
            {collapsed ? (
              <SidebarTooltip label={t("expandSidebar")} collapsed>
                <button
                  onClick={() => setCollapsed(false)}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                >
                  <PanelLeftOpen className="h-4 w-4" />
                </button>
              </SidebarTooltip>
            ) : (
              <>
                <Link href="/new" className="flex items-center gap-2 rounded-md px-1 -mx-1 transition-colors hover:opacity-70">
                  <img src="/fim-mark-light.svg" alt="FIM" className="h-5 w-auto shrink-0 dark:hidden" />
                  <img src="/fim-mark.svg" alt="FIM" className="h-5 w-auto shrink-0 hidden dark:block" />
                  <span className="text-base font-bold tracking-tight text-sidebar-foreground" style={{ fontFamily: 'var(--font-cabinet), sans-serif' }}>{APP_NAME}</span>
                </Link>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => setCollapsed(!collapsed)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                    >
                      <PanelLeftClose className="h-4 w-4" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" sideOffset={4}>{t("collapseSidebar")}</TooltipContent>
                </Tooltip>
              </>
            )}
          </div>

          <Separator />

          {/* New Chat + Search — highest priority */}
          <SidebarNewChat collapsed={collapsed} />

          <Separator />

          {/* Navigation */}
          <div className={cn("px-3 py-2 shrink-0", collapsed && "flex flex-col items-center gap-1")}>
            <SidebarTooltip label={t("agents")} collapsed={collapsed}>
              <Link
                href="/agents"
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                  pathname === "/agents" || pathname.startsWith("/agents/")
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                  collapsed && "h-9 w-9 justify-center px-0"
                )}
              >
                <Bot className="h-4 w-4" />
                {!collapsed && <span>{t("agents")}</span>}
              </Link>
            </SidebarTooltip>
            <SidebarTooltip label={t("knowledge")} collapsed={collapsed}>
              <Link
                href="/kb"
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                  pathname === "/kb" || pathname.startsWith("/kb/")
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                  collapsed && "h-9 w-9 justify-center px-0"
                )}
              >
                <Library className="h-4 w-4" />
                {!collapsed && <span>{t("knowledge")}</span>}
              </Link>
            </SidebarTooltip>
            <SidebarTooltip label={t("connectors")} collapsed={collapsed}>
              <Link
                href="/connectors"
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                  pathname === "/connectors" || pathname.startsWith("/connectors/")
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                  collapsed && "h-9 w-9 justify-center px-0"
                )}
              >
                <Plug className="h-4 w-4 shrink-0" />
                {!collapsed && <span>{t("connectors")}</span>}
              </Link>
            </SidebarTooltip>
            <SidebarTooltip label={t("tools")} collapsed={collapsed}>
              <Link
                href="/tools"
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                  pathname === "/tools"
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                  collapsed && "h-9 w-9 justify-center px-0"
                )}
              >
                <Wrench className="h-4 w-4" />
                {!collapsed && <span>{t("tools")}</span>}
              </Link>
            </SidebarTooltip>
            <SidebarTooltip label={t("artifacts")} collapsed={collapsed}>
              <Link
                href="/artifacts"
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                  pathname === "/artifacts"
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                  collapsed && "h-9 w-9 justify-center px-0"
                )}
              >
                <Layers className="h-4 w-4" />
                {!collapsed && <span>{t("artifacts")}</span>}
              </Link>
            </SidebarTooltip>
            <SidebarTooltip label={t("allChats")} collapsed={collapsed}>
              <Link
                href="/chats"
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                  pathname === "/chats"
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                  collapsed && "h-9 w-9 justify-center px-0"
                )}
              >
                <MessagesSquare className="h-4 w-4" />
                {!collapsed && <span>{t("allChats")}</span>}
              </Link>
            </SidebarTooltip>
          </div>

          <Separator />

          {/* Conversation list */}
          <div className="flex-1 min-h-0 py-2 overflow-hidden">
            <ConversationSidebar collapsed={collapsed} hideHeader />
          </div>

          {/* Bottom area */}
          <div className={cn("shrink-0 pb-3", collapsed ? "px-2" : "px-3")}>
            <Separator className="mb-2" />
            <SidebarFooter collapsed={collapsed} />
          </div>
        </aside>

        {/* Main area */}
        <div className="flex flex-1 flex-col overflow-hidden">
          <AnnouncementBanner />
          <main className="flex-1 flex flex-col min-h-0 overflow-hidden">{children}</main>
        </div>
      </div>
      </TooltipProvider>
    </ConversationProvider>
  )
}
