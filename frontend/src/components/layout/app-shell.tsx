"use client"

import { useState, useEffect } from "react"
import { usePathname, useRouter } from "next/navigation"
import Link from "next/link"
import { Bot, ChevronLeft, ChevronRight, Library, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { APP_NAME, APP_VERSION } from "@/lib/constants"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { useAuth } from "@/contexts/auth-context"
import { ConversationProvider } from "@/contexts/conversation-context"
import { ConversationSidebar } from "@/components/layout/conversation-sidebar"
import { UserMenu } from "@/components/layout/user-menu"

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
  const [collapsed, setCollapsed] = useState(false)
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
          <div className="flex h-14 items-center gap-2 px-4 shrink-0">
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

          {/* Conversation list */}
          <div className="flex-1 min-h-0 py-2">
            <ConversationSidebar collapsed={collapsed} />
          </div>

          {/* Navigation */}
          <div className="px-3 pb-2 shrink-0">
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
          </div>

          {/* Bottom area */}
          <div className="space-y-2 px-3 pb-4 shrink-0">
            <Separator />
            <UserMenu collapsed={collapsed} />
            <div className="flex items-center justify-between pt-1">
              {!collapsed && (
                <Badge variant="secondary" className="text-xs font-normal">
                  v{APP_VERSION}
                </Badge>
              )}
              <button
                onClick={() => setCollapsed(!collapsed)}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
              >
                {collapsed ? (
                  <ChevronRight className="h-4 w-4" />
                ) : (
                  <ChevronLeft className="h-4 w-4" />
                )}
              </button>
            </div>
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
