"use client"

import { useState, useEffect } from "react"
import { usePathname, useRouter } from "next/navigation"
import Link from "next/link"
import { Bot, Library, Loader2, MessagesSquare, PanelLeftClose, PanelLeftOpen, Plug, Plus, Search } from "lucide-react"
import { cn } from "@/lib/utils"
import { APP_NAME } from "@/lib/constants"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
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

  const handleNewChat = () => {
    clearActive()
    router.push("/new")
  }

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-1 px-2 py-2 shrink-0">
        <SidebarTooltip label="New Chat" collapsed>
          <button
            onClick={handleNewChat}
            className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
          >
            <Plus className="h-4 w-4" />
          </button>
        </SidebarTooltip>
        <SidebarTooltip label="Search (Cmd+K)" collapsed>
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
      <div className="flex items-center gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="flex-1 justify-start gap-2"
          onClick={handleNewChat}
        >
          <Plus className="h-4 w-4" />
          New Chat
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0 px-2"
          onClick={() => setSearchOpen(true)}
          title="Search (Cmd+K)"
        >
          <Search className="h-4 w-4" />
        </Button>
      </div>
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
      <div className="flex h-screen overflow-hidden bg-background">
        {/* Sidebar */}
        <aside
          className={cn(
            "flex flex-col border-r border-border bg-sidebar transition-all duration-200",
            collapsed ? "w-16" : "w-60",
          )}
        >
         <TooltipProvider delayDuration={300}>
          {/* Logo area + collapse toggle */}
          <div className={cn("flex h-14 items-center shrink-0", collapsed ? "justify-center px-2" : "justify-between px-4")}>
            {!collapsed && (
              <div className="flex items-center gap-2">
                <img
                  src="/fim-mark-light.svg"
                  alt="FIM"
                  className="h-6 w-auto shrink-0 dark:hidden"
                />
                <img
                  src="/fim-mark.svg"
                  alt="FIM"
                  className="h-6 w-auto shrink-0 hidden dark:block"
                />
                <span className="text-sm font-semibold tracking-tight text-sidebar-foreground">
                  {APP_NAME}
                </span>
              </div>
            )}
            <SidebarTooltip label="Expand sidebar" collapsed={collapsed}>
              <button
                onClick={() => setCollapsed(!collapsed)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
              >
                {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
              </button>
            </SidebarTooltip>
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
                  pathname === "/agents"
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
                  pathname === "/connectors"
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
            <UserMenu collapsed={collapsed} />
          </div>
         </TooltipProvider>
        </aside>

        {/* Main area */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Content (no top header bar — playground manages its own header) */}
          <main className="flex-1 overflow-hidden">{children}</main>
        </div>
      </div>
    </ConversationProvider>
  )
}
