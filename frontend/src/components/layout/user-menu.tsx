"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { ChevronsUpDown, Languages, LayoutDashboard, LogOut, Settings } from "lucide-react"
import { cn } from "@/lib/utils"
import { UserAvatar } from "@/components/shared/user-avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
} from "@/components/ui/dropdown-menu"
import { useAuth } from "@/contexts/auth-context"
import type { UserInfo } from "@/types/auth"
import { authApi } from "@/lib/api"
import { useTranslations } from "next-intl"

interface UserMenuProps {
  collapsed: boolean
}

const LANGUAGE_OPTIONS = [
  { value: "auto", label: "Auto" },
  { value: "en", label: "English" },
  { value: "zh", label: "中文" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "de", label: "Deutsch" },
  { value: "fr", label: "Français" },
] as const

export function UserMenu({ collapsed }: UserMenuProps) {
  const { user, logout, updateUser } = useAuth()
  const router = useRouter()
  const t = useTranslations("common")
  const [open, setOpen] = useState(false)

  if (!user) return null

  const displayLabel = user.display_name || user.email || ""
  const initial = (displayLabel || "U").charAt(0).toUpperCase()

  const handleLanguageChange = async (value: string) => {
    try {
      const updated = await authApi.updateProfile({ preferred_language: value })
      if (updated) {
        updateUser({ preferred_language: value as UserInfo["preferred_language"] })
      }
    } catch {
      // Silently fail — user will see old value
    }

    // Sync locale cookie for next-intl and reload to apply
    const locale = value === "auto" ? "" : value
    document.cookie = locale
      ? `NEXT_LOCALE=${locale}; path=/; max-age=${60 * 60 * 24 * 365}`
      : "NEXT_LOCALE=; path=/; max-age=0"
    setOpen(false)
    window.location.reload()
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
            "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
            "outline-none focus-visible:ring-1 focus-visible:ring-ring",
            collapsed && "h-9 w-9 justify-center px-0",
          )}
        >
          <UserAvatar
            avatar={user.avatar ?? null}
            fallback={initial}
            userId={user.id}
            className="h-7 w-7"
            iconClassName="h-3.5 w-3.5"
          />
          {!collapsed && (
            <>
              <span className="flex-1 truncate text-left text-xs">
                {displayLabel}
              </span>
              <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 opacity-50" />
            </>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className={collapsed ? "min-w-48" : "w-[calc(var(--radix-popper-anchor-width)+2.25rem)]"}>
        <DropdownMenuLabel className="font-normal text-xs text-muted-foreground truncate">
          {displayLabel}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings">
            <Settings className="h-4 w-4" />
            {t("settings")}
          </Link>
        </DropdownMenuItem>
        {user.is_admin && (
          <DropdownMenuItem asChild>
            <Link href="/admin">
              <LayoutDashboard className="h-4 w-4" />
              {t("adminPanel")}
            </Link>
          </DropdownMenuItem>
        )}
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <Languages className="h-4 w-4" />
            {t("language")}
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuRadioGroup
              value={user.preferred_language || "auto"}
              onValueChange={handleLanguageChange}
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <DropdownMenuRadioItem key={opt.value} value={opt.value}>
                  {opt.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={logout}>
          <LogOut className="h-4 w-4" />
          {t("logout")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
