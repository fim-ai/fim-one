"use client"

import { useTranslations } from "next-intl"
import { cn } from "@/lib/utils"
import type { ScopeValue } from "@/hooks/use-scope-filter"

interface ScopeFilterProps {
  value: ScopeValue
  onChange: (scope: ScopeValue) => void
}

const SCOPES: ScopeValue[] = ["all", "mine", "org", "installed"]

const SCOPE_LABELS: Record<ScopeValue, string> = {
  all: "all",
  mine: "mine",
  org: "fromOrg",
  installed: "installed",
}

export function ScopeFilter({ value, onChange }: ScopeFilterProps) {
  const tc = useTranslations("common")

  return (
    <div className="flex flex-wrap gap-1.5">
      {SCOPES.map((key) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
            value === key
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
          )}
        >
          {tc(SCOPE_LABELS[key])}
        </button>
      ))}
    </div>
  )
}
