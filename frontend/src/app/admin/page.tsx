"use client"

import { useEffect, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useTranslations } from "next-intl"
import { LayoutDashboard, Activity, Plug, Settings, Shield, Users, MessageSquare, HardDrive, Server, Cpu, Lock, Key, BookOpen, FileText, BarChart3 } from "lucide-react"
import { cn } from "@/lib/utils"
import { useAuth } from "@/contexts/auth-context"
import { AdminOverview } from "@/components/admin/admin-overview"
import { AdminSettings } from "@/components/admin/admin-settings"
import { AdminUsers } from "@/components/admin/admin-users"
import { AdminConnectors } from "@/components/admin/admin-connectors"
import { AdminAudit } from "@/components/admin/admin-audit"
import { AdminConversations } from "@/components/admin/admin-conversations"
import { AdminStorage } from "@/components/admin/admin-storage"
import { AdminMcpServers } from "@/components/admin/admin-mcp-servers"
import { AdminModels } from "@/components/admin/admin-models"
import { AdminHealth } from "@/components/admin/admin-health"
import { AdminSecurity } from "@/components/admin/admin-security"
import { AdminApiKeys } from "@/components/admin/admin-api-keys"
import { AdminResources } from "@/components/admin/admin-resources"
import { AdminContent } from "@/components/admin/admin-content"
import { AdminAnalytics } from "@/components/admin/admin-analytics"

const TAB_KEYS = ["overview", "health", "users", "conversations", "connectors", "mcp", "models", "audit", "storage", "security", "apikeys", "resources", "content", "analytics", "settings"] as const

const TAB_ICONS = {
  overview: LayoutDashboard,
  health: Activity,
  users: Users,
  conversations: MessageSquare,
  connectors: Plug,
  mcp: Server,
  models: Cpu,
  audit: Shield,
  storage: HardDrive,
  security: Lock,
  apikeys: Key,
  resources: BookOpen,
  content: FileText,
  analytics: BarChart3,
  settings: Settings,
} as const

type TabKey = (typeof TAB_KEYS)[number]

function AdminPanelContent() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const t = useTranslations("admin")

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
          {t("panelTitle")}
        </h1>
      </div>

      {/* Body: left nav + right content */}
      <div className="flex flex-1 min-h-0">
        {/* Left nav */}
        <nav className="w-52 shrink-0 border-r border-border/40 p-4 space-y-1">
          {TAB_KEYS.map((key) => {
            const Icon = TAB_ICONS[key]
            return (
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
                <span>{t(`tabs.${key}`)}</span>
              </button>
            )
          })}
        </nav>

        {/* Right content */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeTab === "overview" && <AdminOverview />}
          {activeTab === "health" && <AdminHealth />}
          {activeTab === "users" && <AdminUsers />}
          {activeTab === "conversations" && <AdminConversations />}
          {activeTab === "connectors" && <AdminConnectors />}
          {activeTab === "storage" && <AdminStorage />}
          {activeTab === "mcp" && <AdminMcpServers />}
          {activeTab === "models" && <AdminModels />}
          {activeTab === "audit" && <AdminAudit />}
          {activeTab === "security" && <AdminSecurity />}
          {activeTab === "apikeys" && <AdminApiKeys />}
          {activeTab === "resources" && <AdminResources />}
          {activeTab === "content" && <AdminContent />}
          {activeTab === "analytics" && <AdminAnalytics />}
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
