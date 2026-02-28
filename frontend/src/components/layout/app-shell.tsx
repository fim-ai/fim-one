"use client"

import { useState, useEffect } from "react"
import { usePathname, useRouter } from "next/navigation"
import Link from "next/link"
import { Bot, Library, Loader2, MessagesSquare, PanelLeftClose, PanelLeftOpen, Plus, Search } from "lucide-react"
import { cn } from "@/lib/utils"
import { APP_NAME } from "@/lib/constants"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { useAuth } from "@/contexts/auth-context"
import { ConversationProvider, useConversation } from "@/contexts/conversation-context"
import { ConversationSidebar } from "@/components/layout/conversation-sidebar"
import { ChatSearchDialog } from "@/components/layout/chat-search-dialog"
import { UserMenu } from "@/components/layout/user-menu"

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
        <button
          onClick={handleNewChat}
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
          title="New Chat"
        >
          <Plus className="h-4 w-4" />
        </button>
        <button
          onClick={() => setSearchOpen(true)}
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
          title="Search (Cmd+K)"
        >
          <Search className="h-4 w-4" />
        </button>
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

  // Login page: no sidebar, full-width content
  if (pathname === "/login") {
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
          {/* Logo area */}
          <div className={cn("flex h-14 items-center gap-2 shrink-0", collapsed ? "justify-center px-2" : "px-4")}>
            <img
              src="/fim-mark.svg"
              alt="FIM"
              className="h-6 w-auto shrink-0"
            />
            {!collapsed && (
              <span className="text-sm font-semibold tracking-tight text-sidebar-foreground">
                {APP_NAME}
              </span>
            )}
          </div>

          <Separator />

          {/* New Chat + Search — highest priority */}
          <SidebarNewChat collapsed={collapsed} />

          <Separator />

          {/* Navigation */}
          <div className={cn("px-3 py-2 shrink-0", collapsed && "flex flex-col items-center gap-1")}>
            <Link
              href="/agents"
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                pathname === "/agents"
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                collapsed && "justify-center px-0"
              )}
            >
              <Bot className="h-4 w-4" />
              {!collapsed && <span>Agents</span>}
            </Link>
            <Link
              href="/kb"
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                pathname === "/kb" || pathname.startsWith("/kb/")
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                collapsed && "justify-center px-0"
              )}
            >
              <Library className="h-4 w-4" />
              {!collapsed && <span>Knowledge</span>}
            </Link>
            <Link
              href="/chats"
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                pathname === "/chats"
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                collapsed && "justify-center px-0"
              )}
            >
              <MessagesSquare className="h-4 w-4" />
              {!collapsed && <span>All Chats</span>}
            </Link>
          </div>

          <Separator />

          {/* Conversation list */}
          <div className="flex-1 min-h-0 py-2 overflow-hidden">
            <ConversationSidebar collapsed={collapsed} hideHeader />
          </div>

          {/* Bottom area */}
          <div className={cn("shrink-0 pb-3", collapsed ? "px-2" : "px-3")}>
            <Separator className="mb-2" />
            {collapsed ? (
              <div className="flex flex-col items-center gap-2">
                <UserMenu collapsed={collapsed} />
                <button
                  onClick={() => setCollapsed(!collapsed)}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                >
                  <PanelLeftOpen className="h-4 w-4" />
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-1">
                <div className="flex-1 min-w-0">
                  <UserMenu collapsed={collapsed} />
                </div>
                <button
                  onClick={() => setCollapsed(!collapsed)}
                  className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                >
                  <PanelLeftClose className="h-4 w-4" />
                </button>
              </div>
            )}
          </div>
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
