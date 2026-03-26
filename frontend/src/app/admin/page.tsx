"use client"

import { useEffect, Suspense } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useTranslations } from "next-intl"
import {
  LayoutDashboard, Activity, Plug, Settings, Shield, Users, MessageSquare,
  HardDrive, Cpu, Lock, Key, Bot, BookOpen, FileText, BarChart3, Wrench,
  Building2, GitBranch, Sparkles, FlaskConical, KeyRound, ClipboardCheck,
  Calendar, Bell, Scan, Webhook, Package, Store,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { useAuth } from "@/contexts/auth-context"
import { AdminOverview } from "@/components/admin/admin-overview"
import { AdminSettings } from "@/components/admin/admin-settings"
import { AdminUsers } from "@/components/admin/admin-users"
import { AdminConnectors } from "@/components/admin/admin-connectors"
import { AdminAudit } from "@/components/admin/admin-audit"
import { AdminConversations } from "@/components/admin/admin-conversations"
import { AdminStorage } from "@/components/admin/admin-storage"
import { AdminModels } from "@/components/admin/admin-models"
import { AdminHealth } from "@/components/admin/admin-health"
import { AdminSecurity } from "@/components/admin/admin-security"
import { AdminApiKeys } from "@/components/admin/admin-api-keys"
import { AdminAgents } from "@/components/admin/admin-agents"
import { AdminKnowledgeBases } from "@/components/admin/admin-knowledge-bases"
import { AdminContent } from "@/components/admin/admin-content"
import { AdminAnalytics } from "@/components/admin/admin-analytics"
import { AdminTools } from "@/components/admin/admin-tools"
import { AdminWorkflows } from "@/components/admin/admin-workflows"
import { AdminOrganizations } from "@/components/admin/admin-organizations"
import { AdminSkills } from "@/components/admin/admin-skills"
import { AdminEval } from "@/components/admin/admin-eval"
import { AdminCredentials } from "@/components/admin/admin-credentials"
import { AdminReviews } from "@/components/admin/admin-reviews"
import { AdminSchedules } from "@/components/admin/admin-schedules"
import { AdminNotifications } from "@/components/admin/admin-notifications"
import { AdminTraces } from "@/components/admin/admin-traces"
import { AdminHooks } from "@/components/admin/admin-hooks"
import { AdminPackages } from "@/components/admin/admin-packages"
import { AdminMarket } from "@/components/admin/admin-market"

// ---------------------------------------------------------------------------
// Tab definitions grouped logically
// ---------------------------------------------------------------------------

interface NavItem {
  key: string
  icon: React.ElementType
  dimmed?: boolean // for "coming soon" items
}

interface NavGroup {
  label?: string // undefined = no separator label
  items: NavItem[]
}

const NAV_GROUPS: NavGroup[] = [
  {
    // Overview — no label, always at top
    items: [
      { key: "overview", icon: LayoutDashboard },
      { key: "health", icon: Activity },
    ],
  },
  {
    label: "platform",
    items: [
      { key: "users", icon: Users },
      { key: "organizations", icon: Building2 },
      { key: "conversations", icon: MessageSquare },
      { key: "content", icon: FileText },
      { key: "reviews", icon: ClipboardCheck },
    ],
  },
  {
    label: "resources",
    items: [
      { key: "agents", icon: Bot },
      { key: "knowledgeBases", icon: BookOpen },
      { key: "connectors", icon: Plug },
      { key: "skills", icon: Sparkles },
      { key: "workflows", icon: GitBranch },
      { key: "market", icon: Store },
      { key: "schedules", icon: Calendar },
      { key: "evaluations", icon: FlaskConical },
      { key: "tools", icon: Wrench },
      { key: "models", icon: Cpu },
    ],
  },
  {
    label: "security",
    items: [
      { key: "security", icon: Lock },
      { key: "apikeys", icon: Key },
      { key: "credentials", icon: KeyRound },
    ],
  },
  {
    label: "operations",
    items: [
      { key: "analytics", icon: BarChart3 },
      { key: "notifications", icon: Bell },
      { key: "audit", icon: Shield },
      { key: "storage", icon: HardDrive },
    ],
  },
  {
    label: "system",
    items: [
      { key: "settings", icon: Settings },
    ],
  },
  {
    label: "comingSoon",
    items: [
      { key: "traces", icon: Scan, dimmed: true },
      { key: "hooks", icon: Webhook, dimmed: true },
      { key: "packages", icon: Package, dimmed: true },
    ],
  },
]

// Flatten for type safety
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const ALL_TAB_KEYS = NAV_GROUPS.flatMap((g) => g.items.map((i) => i.key))
type TabKey = (typeof ALL_TAB_KEYS)[number]

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
        <nav className="w-52 shrink-0 border-r border-border/40 p-4 space-y-0.5 overflow-y-auto">
          {NAV_GROUPS.map((group, gi) => (
            <div key={gi}>
              {group.label ? (
                <div className="px-2 pt-3 pb-1">
                  <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
                    {t(`sections.${group.label}`)}
                  </span>
                </div>
              ) : (
                gi > 0 && <div className="my-2 border-t border-border/30" />
              )}
              {group.items.map(({ key, icon: Icon, dimmed }) => (
                <Link
                  key={key}
                  href={key === "overview" ? "/admin" : `/admin?tab=${key}`}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                    activeTab === key
                      ? "bg-accent text-accent-foreground"
                      : dimmed
                        ? "text-muted-foreground/50 hover:bg-accent/30 hover:text-muted-foreground"
                        : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span>{t(`tabs.${key}`)}</span>
                </Link>
              ))}
            </div>
          ))}
        </nav>

        {/* Right content */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeTab === "overview" && <AdminOverview />}
          {activeTab === "health" && <AdminHealth />}
          {activeTab === "users" && <AdminUsers />}
          {activeTab === "organizations" && <AdminOrganizations />}
          {activeTab === "conversations" && <AdminConversations />}
          {activeTab === "connectors" && <AdminConnectors />}
          {activeTab === "storage" && <AdminStorage />}
          {activeTab === "models" && <AdminModels />}
          {activeTab === "tools" && <AdminTools />}
          {activeTab === "workflows" && <AdminWorkflows />}
          {activeTab === "skills" && <AdminSkills />}
          {activeTab === "schedules" && <AdminSchedules />}
          {activeTab === "evaluations" && <AdminEval />}
          {activeTab === "reviews" && <AdminReviews />}
          {activeTab === "audit" && <AdminAudit />}
          {activeTab === "security" && <AdminSecurity />}
          {activeTab === "apikeys" && <AdminApiKeys />}
          {activeTab === "credentials" && <AdminCredentials />}
          {activeTab === "agents" && <AdminAgents />}
          {activeTab === "knowledgeBases" && <AdminKnowledgeBases />}
          {activeTab === "content" && <AdminContent />}
          {activeTab === "analytics" && <AdminAnalytics />}
          {activeTab === "notifications" && <AdminNotifications />}
          {activeTab === "settings" && <AdminSettings />}
          {activeTab === "traces" && <AdminTraces />}
          {activeTab === "hooks" && <AdminHooks />}
          {activeTab === "packages" && <AdminPackages />}
          {activeTab === "market" && <AdminMarket />}
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
