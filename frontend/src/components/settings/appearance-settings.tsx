"use client"

import { useTheme } from "next-themes"
import { useEffect, useState } from "react"
import { Monitor, Moon, Sun } from "lucide-react"
import { cn } from "@/lib/utils"

const THEME_OPTIONS = [
  {
    value: "light",
    label: "Light",
    icon: Sun,
  },
  {
    value: "system",
    label: "Auto",
    icon: Monitor,
  },
  {
    value: "dark",
    label: "Dark",
    icon: Moon,
  },
] as const

export function AppearanceSettings() {
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  // Avoid hydration mismatch
  useEffect(() => setMounted(true), [])

  if (!mounted) return null

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-base font-semibold text-foreground">Appearance</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Customize how the app looks on your device.
        </p>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-medium text-foreground">Color mode</h3>
        <div className="grid grid-cols-3 gap-3">
          {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              onClick={() => setTheme(value)}
              className={cn(
                "group relative flex flex-col items-center gap-2 rounded-lg border-2 p-4 transition-all",
                theme === value
                  ? "border-primary bg-primary/5"
                  : "border-border hover:border-primary/40 hover:bg-accent/50"
              )}
            >
              {/* Mini preview card */}
              <div
                className={cn(
                  "w-full aspect-[4/3] rounded-md overflow-hidden border",
                  theme === value ? "border-primary/30" : "border-border"
                )}
              >
                <ThemePreview mode={value} />
              </div>

              {/* Label */}
              <div className="flex items-center gap-1.5">
                <Icon className={cn(
                  "h-3.5 w-3.5",
                  theme === value ? "text-primary" : "text-muted-foreground"
                )} />
                <span className={cn(
                  "text-sm font-medium",
                  theme === value ? "text-primary" : "text-muted-foreground"
                )}>
                  {label}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

/** Mini UI mockup showing what light/dark/auto looks like */
function ThemePreview({ mode }: { mode: string }) {
  const isLight = mode === "light"
  const isAuto = mode === "system"

  if (isAuto) {
    // Split preview: left half light, right half dark
    return (
      <div className="flex h-full w-full">
        <div className="w-1/2 bg-[oklch(0.97_0.005_78)] p-1.5 flex flex-col gap-1">
          <div className="h-1.5 w-3/4 rounded-full bg-[oklch(0.18_0.01_60)] opacity-60" />
          <div className="flex-1 rounded bg-[oklch(0.95_0.005_75)] p-1">
            <div className="h-1 w-full rounded-full bg-[oklch(0.55_0.12_70)] opacity-50 mb-0.5" />
            <div className="h-1 w-2/3 rounded-full bg-[oklch(0.18_0.01_60)] opacity-20" />
          </div>
          <div className="h-2 rounded bg-[oklch(0.55_0.12_70)] opacity-60" />
        </div>
        <div className="w-1/2 bg-[oklch(0.17_0.008_55)] p-1.5 flex flex-col gap-1">
          <div className="h-1.5 w-3/4 rounded-full bg-[oklch(0.95_0.008_75)] opacity-60" />
          <div className="flex-1 rounded bg-[oklch(0.23_0.006_50)] p-1">
            <div className="h-1 w-full rounded-full bg-[oklch(0.72_0.14_70)] opacity-50 mb-0.5" />
            <div className="h-1 w-2/3 rounded-full bg-[oklch(0.95_0.008_75)] opacity-20" />
          </div>
          <div className="h-2 rounded bg-[oklch(0.72_0.14_70)] opacity-60" />
        </div>
      </div>
    )
  }

  // Light or dark full preview
  const bg = isLight ? "bg-[oklch(0.97_0.005_78)]" : "bg-[oklch(0.17_0.008_55)]"
  const sidebar = isLight ? "bg-[oklch(0.95_0.005_75)]" : "bg-[oklch(0.23_0.006_50)]"
  const textBar = isLight ? "bg-[oklch(0.18_0.01_60)]" : "bg-[oklch(0.95_0.008_75)]"
  const accent = isLight ? "bg-[oklch(0.55_0.12_70)]" : "bg-[oklch(0.72_0.14_70)]"
  const subtleText = isLight ? "bg-[oklch(0.18_0.01_60)]" : "bg-[oklch(0.95_0.008_75)]"

  return (
    <div className={`h-full w-full ${bg} p-1.5 flex gap-1`}>
      {/* Sidebar */}
      <div className={`w-1/4 ${sidebar} rounded p-1 flex flex-col gap-0.5`}>
        <div className={`h-1 w-full rounded-full ${textBar} opacity-40`} />
        <div className={`h-1 w-3/4 rounded-full ${textBar} opacity-20`} />
        <div className={`h-1 w-full rounded-full ${accent} opacity-40`} />
        <div className={`h-1 w-2/3 rounded-full ${textBar} opacity-20`} />
      </div>
      {/* Main content */}
      <div className="flex-1 flex flex-col gap-1">
        <div className={`h-1.5 w-1/2 rounded-full ${textBar} opacity-50`} />
        <div className={`flex-1 rounded ${sidebar} p-1`}>
          <div className={`h-1 w-full rounded-full ${accent} opacity-50 mb-0.5`} />
          <div className={`h-1 w-4/5 rounded-full ${subtleText} opacity-20 mb-0.5`} />
          <div className={`h-1 w-3/5 rounded-full ${subtleText} opacity-15`} />
        </div>
        <div className={`h-2 rounded ${accent} opacity-60`} />
      </div>
    </div>
  )
}
