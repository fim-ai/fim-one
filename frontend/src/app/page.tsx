"use client"

import { useSearchParams } from "next/navigation"
import { useTranslations } from "next-intl"
import { PlaygroundPage } from "@/components/playground/playground-page"
import { DashboardPage } from "@/components/dashboard/dashboard-page"
import { usePageTitle } from "@/hooks/use-page-title"

export default function RootPage() {
  const searchParams = useSearchParams()
  const tl = useTranslations("layout")
  const cParam = searchParams.get("c")

  usePageTitle(cParam ? undefined : tl("dashboard"))

  if (cParam) return <PlaygroundPage />
  return <DashboardPage />
}
