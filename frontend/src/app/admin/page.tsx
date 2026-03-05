"use client"

import { useEffect, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { LayoutDashboard, Plug, Settings, Users } from "lucide-react"
import { cn } from "@/lib/utils"
import { useAuth } from "@/contexts/auth-context"
import { AdminOverview } from "@/components/admin/admin-overview"
import { AdminSettings } from "@/components/admin/admin-settings"
import { AdminUsers } from "@/components/admin/admin-users"
import { AdminConnectors } from "@/components/admin/admin-connectors"

const TABS = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "users", label: "Users", icon: Users },
  { key: "connectors", label: "Connectors", icon: Plug },
  { key: "settings", label: "Settings", icon: Settings },
] as const

type TabKey = (typeof TABS)[number]["key"]

function AdminPanelContent() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()

  const activeTab = (searchParams.get("tab") as TabKey) || "overview"

  // Auth guard: admin only
  useEffect(() => {
    if (authLoading) return
    if (!user) {
      router.replace("/login")
      return
    }
    if (!user.is_admin) {
      router.replace("/")
    }
  }, [authLoading, user, router])

  const handleTabChange = (tab: TabKey) => {
    if (tab === "overview") {
      router.replace("/admin")
    } else {
      router.replace(`/admin?tab=${tab}`)
    }
  }

  if (authLoading || !user || !user.is_admin) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center px-6 py-4 shrink-0 border-b border-border/40">
        <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
          <LayoutDashboard className="h-5 w-5" />
          Admin Panel
        </h1>
      </div>

      {/* Body: left nav + right content */}
      <div className="flex flex-1 min-h-0">
        {/* Left nav */}
        <nav className="w-52 shrink-0 border-r border-border/40 p-4 space-y-1">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => handleTabChange(key)}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                activeTab === key
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              <span>{label}</span>
            </button>
          ))}
        </nav>

        {/* Right content */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeTab === "overview" && <AdminOverview />}
          {activeTab === "users" && <AdminUsers />}
          {activeTab === "connectors" && <AdminConnectors />}
          {activeTab === "settings" && <AdminSettings />}
        </div>
      </div>
    </div>
  )
}

export default function AdminPanelPage() {
  return (
    <Suspense>
      <AdminPanelContent />
    </Suspense>
  )
}
