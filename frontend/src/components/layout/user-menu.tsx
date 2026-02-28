"use client"

import { useRouter } from "next/navigation"
import { LogOut, Settings } from "lucide-react"
import { cn } from "@/lib/utils"
import { APP_VERSION } from "@/lib/constants"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuth } from "@/contexts/auth-context"

interface UserMenuProps {
  collapsed: boolean
}

export function UserMenu({ collapsed }: UserMenuProps) {
  const { user, logout } = useAuth()
  const router = useRouter()

  if (!user) return null

  const displayLabel = user.display_name || user.username
  const initial = displayLabel.charAt(0).toUpperCase()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
            "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
            "outline-none focus-visible:ring-1 focus-visible:ring-ring",
            collapsed && "justify-center px-0",
          )}
        >
          <Avatar className="h-7 w-7 shrink-0">
            <AvatarFallback className="bg-primary/10 text-xs text-primary">
              {initial}
            </AvatarFallback>
          </Avatar>
          {!collapsed && (
            <span className="flex-1 truncate text-left text-xs">
              {displayLabel}
            </span>
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
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={logout}>
          <LogOut className="h-4 w-4" />
          Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
