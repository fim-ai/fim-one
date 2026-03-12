"use client"

import { useSearchParams } from "next/navigation"
import { PlaygroundPage } from "@/components/playground/playground-page"
import { DashboardPage } from "@/components/dashboard/dashboard-page"

export default function RootPage() {
  const searchParams = useSearchParams()
  const cParam = searchParams.get("c")

  if (cParam) return <PlaygroundPage />
  return <DashboardPage />
}
