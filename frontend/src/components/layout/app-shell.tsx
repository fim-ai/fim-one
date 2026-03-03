"use client"

import { useState, useEffect } from "react"
import { usePathname, useRouter } from "next/navigation"
import Link from "next/link"
import { Bot, Library, Loader2, MessagesSquare, Moon, PanelLeftClose, PanelLeftOpen, Plug, Plus, Search, Sun } from "lucide-react"
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
  const [searchOpen, setSearchOpen] = useState(false)
  const [isMac, setIsMac] = useState(true) // default to Mac to avoid flash

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
        <SidebarTooltip label={isMac ? "Search (⌘K)" : "Search (Ctrl+K)"} collapsed>
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
      <button
        onClick={handleNewChat}
        className="group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
      >
        <span className="flex h-5 w-5 items-center justify-center rounded-md bg-foreground/10 text-foreground">
          <Plus className="h-3.5 w-3.5" />
        </span>
        <span>New chat</span>
        <kbd className="ml-auto text-xs text-muted-foreground/40 font-normal tracking-[0.1em] opacity-0 group-hover:opacity-100 transition-opacity" style={{ fontFamily: "system-ui, -apple-system, sans-serif" }}>{isMac ? "⇧⌘O" : "Ctrl+Shift+O"}</kbd>
      </button>
      <button
        onClick={() => setSearchOpen(true)}
        className="group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
      >
        <Search className="h-4 w-4" />
        <span>Search</span>
        <kbd className="ml-auto text-xs text-muted-foreground/40 font-normal tracking-[0.1em] opacity-0 group-hover:opacity-100 transition-opacity" style={{ fontFamily: "system-ui, -apple-system, sans-serif" }}>{isMac ? "⌘K" : "Ctrl+K"}</kbd>
      </button>
      <ChatSearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
    </div>
  )
}

function RedirectToLogin() {
  const router = useRouter()
  useEffect(() => {
    router.replace("/login")
  }, [router])
  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  )
}

function ThemeToggle({ collapsed }: { collapsed: boolean }) {
  const { resolvedTheme, setTheme } = useTheme()
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
    <SidebarTooltip label={resolvedTheme === "dark" ? "Light mode" : "Dark mode"} collapsed={collapsed}>
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

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("sidebar-collapsed") === "true"
    }
    return false
  })

  useEffect(() => {
    localStorage.setItem("sidebar-collapsed", String(collapsed))
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

  // Public pages: no sidebar, full-width content
  if (pathname === "/login" || pathname === "/auth/callback") {
    return <main className="h-screen bg-background">{children}</main>
  }

  // Not authenticated and not on login page — redirect to login
  if (!user) {
    return <RedirectToLogin />
  }

  // Authenticated: full layout with conversation sidebar
  return (
    <ConversationProvider>
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
              <SidebarTooltip label="Expand sidebar" collapsed>
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
                  <span className="text-sm font-semibold tracking-tight text-sidebar-foreground">{APP_NAME}</span>
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
                  <TooltipContent side="bottom" sideOffset={4}>Collapse sidebar</TooltipContent>
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
            <SidebarTooltip label="Agents" collapsed={collapsed}>
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
                {!collapsed && <span>Agents</span>}
              </Link>
            </SidebarTooltip>
            <SidebarTooltip label="Knowledge" collapsed={collapsed}>
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
                {!collapsed && <span>Knowledge</span>}
              </Link>
            </SidebarTooltip>
            <SidebarTooltip label="Connectors" collapsed={collapsed}>
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
                {!collapsed && <span>Connectors</span>}
              </Link>
            </SidebarTooltip>
            <SidebarTooltip label="All Chats" collapsed={collapsed}>
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
                {!collapsed && <span>All Chats</span>}
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
          {/* Content (no top header bar — playground manages its own header) */}
          <main className="flex-1 overflow-hidden">{children}</main>
        </div>
      </div>
      </TooltipProvider>
    </ConversationProvider>
  )
}
