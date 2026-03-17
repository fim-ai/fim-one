"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { Wrench } from "lucide-react"
import { useAuth } from "@/contexts/auth-context"
import { BuiltinToolsSection } from "@/components/tools/builtin-tools-section"

export default function ToolsPage() {
  const t = useTranslations("tools")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()

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
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Wrench className="h-5 w-5" />
            {t("pageTitle")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("pageDescription")}
          </p>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <BuiltinToolsSection />
      </div>
    </div>
  )
}
