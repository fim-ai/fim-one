"use client"

import { useEffect, Suspense } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import {
  Building2,
  Key,
  Palette,
  Settings,
  User,
  ShieldCheck,
  BarChart3,
  Bell,
  BookMarked,
} from "lucide-react"
import { useTranslations } from "next-intl"
import { cn } from "@/lib/utils"
import { useAuth } from "@/contexts/auth-context"
import { GeneralSettings } from "@/components/settings/general-settings"
import { AccountSettings } from "@/components/settings/account-settings"
import { AppearanceSettings } from "@/components/settings/appearance-settings"
import { OrganizationSettings } from "@/components/settings/organization-settings"
import { ApiKeysSettings } from "@/components/settings/api-keys-settings"
import { CredentialsSettings } from "@/components/settings/credentials-settings"
import { UsageSettings } from "@/components/settings/usage-settings"
import { NotificationsSettings } from "@/components/settings/notifications-settings"
import { SubscriptionsSettings } from "@/components/settings/subscriptions-settings"

const TAB_KEYS = [
  "general",
  "account",
  "appearance",
  "organizations",
  "api-keys",
  "credentials",
  "usage",
  "notifications",
  "subscriptions",
] as const

const TAB_ICONS = {
  general: Settings,
  account: User,
  appearance: Palette,
  organizations: Building2,
  "api-keys": Key,
  credentials: ShieldCheck,
  usage: BarChart3,
  notifications: Bell,
  subscriptions: BookMarked,
} as const

type TabKey = (typeof TAB_KEYS)[number]

function SettingsContent() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const t = useTranslations("settings")

  const activeTab = (searchParams.get("tab") as TabKey) || "general"

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center px-6 py-4 shrink-0 border-b border-border/40">
        <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
          <Settings className="h-5 w-5" />
          {t("title")}
        </h1>
      </div>

      {/* Body: left nav + right content */}
      <div className="flex flex-1 min-h-0">
        {/* Left nav */}
        <nav className="w-52 shrink-0 border-r border-border/40 p-4 space-y-1 overflow-y-auto">
          {TAB_KEYS.map((key) => {
            const Icon = TAB_ICONS[key]
            const tabLabelKey = key === "api-keys" ? "apiKeys" : key
            return (
              <Link
                key={key}
                href={key === "general" ? "/settings" : `/settings?tab=${key}`}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                  activeTab === key
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                <span>{t(`tabs.${tabLabelKey}`)}</span>
              </Link>
            )
          })}
        </nav>

        {/* Right content */}
        <div className="flex-1 overflow-y-auto p-6">
          <div>
            {activeTab === "general" && <GeneralSettings />}
            {activeTab === "account" && <AccountSettings />}
            {activeTab === "appearance" && <AppearanceSettings />}
            {activeTab === "organizations" && <OrganizationSettings />}
            {activeTab === "api-keys" && <ApiKeysSettings />}
            {activeTab === "credentials" && <CredentialsSettings />}
            {activeTab === "usage" && <UsageSettings />}
            {activeTab === "notifications" && <NotificationsSettings />}
            {activeTab === "subscriptions" && <SubscriptionsSettings />}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function SettingsPage() {
  return (
    <Suspense>
      <SettingsContent />
    </Suspense>
  )
}
