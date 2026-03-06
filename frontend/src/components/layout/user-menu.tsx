"use client"

import { useRouter } from "next/navigation"
import { ChevronsUpDown, Globe, LayoutDashboard, LogOut, Settings } from "lucide-react"
import { cn } from "@/lib/utils"
import { APP_VERSION } from "@/lib/constants"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
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
import { authApi } from "@/lib/api"

interface UserMenuProps {
  collapsed: boolean
}

const LANGUAGE_OPTIONS = [
  { value: "auto", label: "Auto" },
  { value: "en", label: "English" },
  { value: "zh", label: "中文" },
] as const

export function UserMenu({ collapsed }: UserMenuProps) {
  const { user, logout, updateUser } = useAuth()
  const router = useRouter()

  if (!user) return null

  const displayLabel = user.display_name || user.username
  const initial = displayLabel.charAt(0).toUpperCase()

  const handleLanguageChange = async (value: string) => {
    try {
      const updated = await authApi.updateProfile({ preferred_language: value })
      if (updated) {
        updateUser({ preferred_language: value as "auto" | "en" | "zh" })
      }
    } catch {
      // Silently fail — user will see old value
    }

    // Sync locale cookie for next-intl and reload to apply
    const locale = value === "auto" ? "" : value
    document.cookie = locale
      ? `NEXT_LOCALE=${locale}; path=/; max-age=${60 * 60 * 24 * 365}`
      : "NEXT_LOCALE=; path=/; max-age=0"
    window.location.reload()
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
            "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
            "outline-none focus-visible:ring-1 focus-visible:ring-ring",
            collapsed && "h-9 w-9 justify-center px-0",
          )}
        >
          <Avatar className="h-7 w-7 shrink-0">
            <AvatarFallback className="bg-primary/10 text-xs text-primary">
              {initial}
            </AvatarFallback>
          </Avatar>
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
      <DropdownMenuContent side="top" align="start" className="w-48">
        <DropdownMenuLabel className="flex items-center justify-between font-normal text-xs text-muted-foreground">
          <span>{displayLabel}</span>
          <span className="opacity-50">v{APP_VERSION}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => router.push("/settings")}>
          <Settings className="h-4 w-4" />
          Settings
        </DropdownMenuItem>
        {user.is_admin && (
          <DropdownMenuItem onClick={() => router.push("/admin")}>
            <LayoutDashboard className="h-4 w-4" />
            Admin Panel
          </DropdownMenuItem>
        )}
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <Globe className="h-4 w-4" />
            Language
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
          Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
