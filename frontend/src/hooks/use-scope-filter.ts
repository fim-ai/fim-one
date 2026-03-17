"use client"

import { useCallback } from "react"
import { useSearchParams, useRouter, usePathname } from "next/navigation"

export type ScopeValue = "all" | "mine" | "org" | "installed"

export function useScopeFilter() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()

  const scope = (searchParams.get("scope") as ScopeValue) || "all"

  const setScope = useCallback(
    (newScope: ScopeValue) => {
      const params = new URLSearchParams(searchParams.toString())
      if (newScope === "all") {
        params.delete("scope")
      } else {
        params.set("scope", newScope)
      }
      const qs = params.toString()
      router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false })
    },
    [searchParams, router, pathname],
  )

  const filterByScope = useCallback(
    <T extends { user_id: string | null; source?: string }>(items: T[], currentUserId: string) => {
      if (scope === "mine") return items.filter((i) => i.user_id === currentUserId)
      if (scope === "org") return items.filter((i) => i.user_id !== currentUserId)
      if (scope === "installed") return items.filter((i) => i.source === "market")
      return items
    },
    [scope],
  )

  return { scope, setScope, filterByScope }
}
