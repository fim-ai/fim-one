"use client"

import { useEffect } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { Settings, User } from "lucide-react"
import { cn } from "@/lib/utils"
import { useAuth } from "@/contexts/auth-context"
import { GeneralSettings } from "@/components/settings/general-settings"
import { AccountSettings } from "@/components/settings/account-settings"

const TABS = [
  { key: "general", label: "General", icon: Settings },
  { key: "account", label: "Account", icon: User },
] as const

type TabKey = (typeof TABS)[number]["key"]

function SettingsContent() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()

  const activeTab = (searchParams.get("tab") as TabKey) || "general"

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const handleTabChange = (tab: TabKey) => {
    if (tab === "general") {
      router.replace("/settings")
    } else {
      router.replace(`/settings?tab=${tab}`)
    }
  }

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center px-6 py-4 shrink-0 border-b border-border/40">
        <h1 className="text-lg font-semibold text-foreground">Settings</h1>
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
          <div className="max-w-2xl">
            {activeTab === "general" && <GeneralSettings />}
            {activeTab === "account" && <AccountSettings />}
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
