"use client"

import { useTheme } from "next-themes"
import { useEffect, useState } from "react"
import { Monitor, Moon, Sun } from "lucide-react"
import { useTranslations } from "next-intl"
import { cn } from "@/lib/utils"
import { Separator } from "@/components/ui/separator"

const THEME_KEYS = ["system", "light", "dark"] as const
const THEME_ICONS = {
  system: Monitor,
  light: Sun,
  dark: Moon,
} as const

export function AppearanceSettings() {
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  const t = useTranslations("settings.appearance")

  // Avoid hydration mismatch
  useEffect(() => setMounted(true), [])

  if (!mounted) return null

  const themeLabelKeys: Record<string, string> = {
    system: "auto",
    light: "light",
    dark: "dark",
  }

  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent)
  const ctrlKey = isMac ? "\u2318" : "Ctrl"

  const shortcutGroups = [
    {
      labelKey: "shortcutsChatGroup",
      shortcuts: [
        { keys: ["Enter"], labelKey: "shortcutsSendMessage" },
        { keys: ["Shift", "Enter"], labelKey: "shortcutsNewLine" },
        { keys: [ctrlKey, "/"], labelKey: "shortcutsToggleSidebar" },
      ],
    },
    {
      labelKey: "shortcutsNavigationGroup",
      shortcuts: [
        { keys: [ctrlKey, "K"], labelKey: "shortcutsQuickSearch" },
        { keys: ["Esc"], labelKey: "shortcutsCloseDialog" },
      ],
    },
    {
      labelKey: "shortcutsWorkflowGroup",
      shortcuts: [
        { keys: ["?"], labelKey: "shortcutsShowShortcuts" },
        { keys: [ctrlKey, "A"], labelKey: "shortcutsSelectAll" },
        { keys: ["Delete"], labelKey: "shortcutsDeleteSelected" },
        { keys: [ctrlKey, "Z"], labelKey: "shortcutsUndo" },
        { keys: [ctrlKey, "S"], labelKey: "shortcutsSave" },
      ],
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-base font-semibold text-foreground">{t("title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">
          {t("description")}
        </p>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-medium text-foreground">{t("colorMode")}</h3>
        <div className="grid grid-cols-3 gap-4 max-w-2xl">
          {THEME_KEYS.map((value) => {
            const Icon = THEME_ICONS[value]
            const label = t(themeLabelKeys[value])
            return (
              <button
                key={value}
                onClick={() => setTheme(value)}
                className={cn(
                  "group relative flex flex-col items-center gap-2 rounded-lg border-2 p-5 transition-all",
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
            )
          })}
        </div>
      </div>

      <Separator />

      {/* Keyboard Shortcuts */}
      <div className="space-y-3">
        <div>
          <h3 className="text-sm font-medium text-foreground">{t("keyboardShortcutsTitle")}</h3>
          <p className="text-xs text-muted-foreground mt-1">
            {t("keyboardShortcutsDescription")}
          </p>
        </div>

        <div className="space-y-4 max-w-xl">
          {shortcutGroups.map((group) => (
            <div key={group.labelKey} className="space-y-2">
              <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                {t(group.labelKey)}
              </h4>
              <div className="rounded-md border border-border divide-y divide-border">
                {group.shortcuts.map((shortcut) => (
                  <div
                    key={shortcut.labelKey}
                    className="flex items-center justify-between px-3 py-2"
                  >
                    <span className="text-sm text-foreground">
                      {t(shortcut.labelKey)}
                    </span>
                    <div className="flex items-center gap-1">
                      {shortcut.keys.map((key, idx) => (
                        <span key={idx}>
                          {idx > 0 && (
                            <span className="text-xs text-muted-foreground mx-0.5">+</span>
                          )}
                          <kbd className="inline-flex items-center justify-center min-w-[1.5rem] h-6 px-1.5 rounded border border-border bg-muted text-xs font-mono text-muted-foreground">
                            {key}
                          </kbd>
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
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
