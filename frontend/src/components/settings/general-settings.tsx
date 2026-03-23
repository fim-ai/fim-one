"use client"

import { useState, useEffect, useMemo } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { Check, ChevronsUpDown, Pencil, Sparkles } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Separator } from "@/components/ui/separator"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { UserAvatar } from "@/components/shared/user-avatar"
import { AvatarPickerDialog } from "@/components/settings/avatar-picker-dialog"
import { useAuth } from "@/contexts/auth-context"
import { authApi } from "@/lib/api"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

const MAX_INSTRUCTIONS_LENGTH = 2000
const MAX_DISPLAY_NAME_LENGTH = 50
const MAX_USERNAME_LENGTH = 50
const MIN_USERNAME_LENGTH = 2

const TIMEZONE_FALLBACK = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Moscow",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Bangkok",
  "Asia/Tokyo",
  "Asia/Shanghai",
  "Asia/Singapore",
  "Australia/Sydney",
  "Pacific/Auckland",
]

function getTimezoneOptions(): string[] {
  try {
    if (typeof Intl !== "undefined" && "supportedValuesOf" in Intl) {
      return (Intl as unknown as { supportedValuesOf: (key: string) => string[] }).supportedValuesOf("timeZone")
    }
  } catch {
    // Fallback for browsers that don't support supportedValuesOf
  }
  return TIMEZONE_FALLBACK
}


export function GeneralSettings() {
  const { user, updateUser } = useAuth()
  const t = useTranslations("settings.general")
  const tc = useTranslations("common")
  const router = useRouter()

  // --- Avatar ---
  const [avatarDialogOpen, setAvatarDialogOpen] = useState(false)

  // --- Username ---
  const [username, setUsername] = useState("")
  const [usernameError, setUsernameError] = useState("")
  const [savingUsername, setSavingUsername] = useState(false)
  const [usernameConfirmOpen, setUsernameConfirmOpen] = useState(false)

  // --- Profile ---
  const [displayName, setDisplayName] = useState("")
  const [savingProfile, setSavingProfile] = useState(false)

  // --- Personal Instructions ---
  const [instructions, setInstructions] = useState("")
  const [savingInstructions, setSavingInstructions] = useState(false)

  // --- Timezone ---
  const [timezone, setTimezone] = useState("")
  const [savingTimezone, setSavingTimezone] = useState(false)
  const [timezoneOpen, setTimezoneOpen] = useState(false)
  const timezoneOptions = useMemo(() => getTimezoneOptions(), [])

  useEffect(() => {
    if (user) {
      setUsername(user.username || "")
      setDisplayName(user.display_name || "")
      setInstructions(user.system_instructions || "")
      setTimezone(user.timezone || "")
    }
  }, [user])

  const displayLabel = user?.display_name || user?.username || ""
  const initial = (displayLabel || "U").charAt(0).toUpperCase()

  // Username validation
  const isUsernameDirty = username !== (user?.username || "")
  const isUsernameOverLimit = username.length > MAX_USERNAME_LENGTH
  const isUsernameTooShort = username.length > 0 && username.length < MIN_USERNAME_LENGTH

  // Display name validation
  const isDisplayNameDirty = displayName !== (user?.display_name || "")
  const isDisplayNameOverLimit = displayName.length > MAX_DISPLAY_NAME_LENGTH

  // Timezone validation
  const userTimezone = user?.timezone || ""
  const isTimezoneDirty = timezone !== userTimezone

  // Instructions validation
  const isInstructionsDirty = instructions !== (user?.system_instructions || "")
  const isInstructionsOverLimit = instructions.length > MAX_INSTRUCTIONS_LENGTH

  // First-time setup (no existing username) saves directly; otherwise confirm first
  const handleSaveUsernameClick = () => {
    if (!isUsernameDirty || isUsernameOverLimit || isUsernameTooShort) return
    if (!user?.username) {
      // First-time setup — no cooldown, save directly
      doSaveUsername()
    } else {
      // Changing existing username — confirm first
      setUsernameConfirmOpen(true)
    }
  }

  const doSaveUsername = async () => {
    setSavingUsername(true)
    try {
      const updated = await authApi.updateProfile({
        username: username.trim(),
      })
      updateUser(updated)
      toast.success(t("usernameSaved"))
    } catch (err: unknown) {
      const msg =
        (err as { message?: string })?.message ||
        (err as { error?: string })?.error ||
        ""
      if (msg.includes("username_taken")) {
        setUsernameError(t("usernameTaken"))
      } else if (msg.includes("username_cooldown")) {
        const days = (err as { errorArgs?: Record<string, unknown> })?.errorArgs?.days ?? 7
        setUsernameError(t("usernameCooldown", { days: String(days) }))
      } else {
        toast.error(t("usernameSaveFailed"))
      }
    } finally {
      setSavingUsername(false)
    }
  }

  const handleSaveProfile = async () => {
    if (!isDisplayNameDirty || isDisplayNameOverLimit) return
    setSavingProfile(true)
    try {
      const updated = await authApi.updateProfile({
        display_name: displayName.trim(),
      })
      updateUser(updated)
      toast.success(t("profileSaved"))
    } catch {
      toast.error(t("profileSaveFailed"))
    } finally {
      setSavingProfile(false)
    }
  }

  const handleSaveInstructions = async () => {
    if (!isInstructionsDirty || isInstructionsOverLimit) return
    setSavingInstructions(true)
    try {
      const updated = await authApi.updateProfile({
        system_instructions: instructions,
      })
      updateUser(updated)
      toast.success(t("instructionsSaved"))
    } catch {
      toast.error(t("instructionsSaveFailed"))
    } finally {
      setSavingInstructions(false)
    }
  }

  const handleSaveTimezone = async () => {
    if (!isTimezoneDirty) return
    setSavingTimezone(true)
    try {
      const updated = await authApi.updateProfile({
        timezone: timezone,
      } as Parameters<typeof authApi.updateProfile>[0])
      updateUser(updated)
      toast.success(t("timezoneSaved"))
    } catch {
      toast.error(t("timezoneSaveFailed"))
    } finally {
      setSavingTimezone(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Profile Section */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("profileTitle")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("profileDescription")}
          </p>
        </div>

        <div className="space-y-4">
          {/* Avatar */}
          <div className="flex items-center gap-4">
            <div className="relative group">
              <UserAvatar
                avatar={user?.avatar ?? null}
                fallback={initial}
                userId={user?.id}
                className="h-16 w-16"
                iconClassName="h-8 w-8"
              />
              <button
                onClick={() => setAvatarDialogOpen(true)}
                className="absolute inset-0 flex items-center justify-center rounded-full bg-black/0 group-hover:bg-black/40 transition-colors cursor-pointer"
              >
                <Pencil className="h-4 w-4 text-white opacity-0 group-hover:opacity-100 transition-opacity" />
              </button>
            </div>
            <div>
              <p className="text-sm font-medium">{t("avatarTitle")}</p>
              <p className="text-xs text-muted-foreground">
                {t("avatarDescription")}
              </p>
            </div>
          </div>

          {/* Username */}
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("usernameLabel")}</label>
            <Input
              value={username}
              onChange={(e) => {
                setUsername(e.target.value)
                setUsernameError("")
              }}
              placeholder={t("usernamePlaceholder")}
              maxLength={MAX_USERNAME_LENGTH + 10}
              className="max-w-sm"
            />
            {usernameError && (
              <p className="text-sm text-destructive">{usernameError}</p>
            )}
            <div className="flex items-center justify-between max-w-sm">
              <span
                className={`text-xs ${
                  isUsernameOverLimit || isUsernameTooShort
                    ? "text-destructive"
                    : "text-muted-foreground"
                }`}
              >
                {isUsernameTooShort
                  ? t("usernameMinLength")
                  : `${username.length} / ${MAX_USERNAME_LENGTH}`}
              </span>
              <Button
                size="sm"
                onClick={handleSaveUsernameClick}
                disabled={
                  !isUsernameDirty ||
                  isUsernameOverLimit ||
                  isUsernameTooShort ||
                  savingUsername
                }
              >
                {savingUsername ? tc("saving") : tc("save")}
              </Button>
            </div>
          </div>

          {/* Display Name */}
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("displayNameLabel")}</label>
            <Input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t("displayNamePlaceholder")}
              maxLength={MAX_DISPLAY_NAME_LENGTH + 10}
              className="max-w-sm"
            />
            <div className="flex items-center justify-between max-w-sm">
              <span
                className={`text-xs ${
                  isDisplayNameOverLimit
                    ? "text-destructive"
                    : "text-muted-foreground"
                }`}
              >
                {displayName.length} / {MAX_DISPLAY_NAME_LENGTH}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={handleSaveProfile}
                  disabled={
                    !isDisplayNameDirty || isDisplayNameOverLimit || savingProfile
                  }
                >
                  {savingProfile ? tc("saving") : tc("save")}
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <Separator />

      {/* Personal Instructions Section */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("instructionsTitle")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("instructionsDescription")}
          </p>
        </div>

        <Textarea
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder={t("instructionsPlaceholder")}
          rows={8}
          className="resize-y"
        />
        <div className="flex items-center justify-between">
          <span
            className={`text-xs ${
              isInstructionsOverLimit
                ? "text-destructive"
                : "text-muted-foreground"
            }`}
          >
            {instructions.length} / {MAX_INSTRUCTIONS_LENGTH}
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              onClick={handleSaveInstructions}
              disabled={
                !isInstructionsDirty ||
                isInstructionsOverLimit ||
                savingInstructions
              }
            >
              {savingInstructions ? tc("saving") : tc("save")}
            </Button>
          </div>
        </div>
      </div>

      <Separator />

      {/* Timezone Section */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("timezoneTitle")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("timezoneDescription")}
          </p>
        </div>

        <div className="space-y-2">
          <Popover open={timezoneOpen} onOpenChange={setTimezoneOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                role="combobox"
                aria-expanded={timezoneOpen}
                className="w-full max-w-sm justify-between font-normal"
              >
                <span className="truncate">
                  {timezone || t("timezonePlaceholder")}
                </span>
                <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
              <Command>
                <CommandInput placeholder={tc("searchPlaceholder")} />
                <CommandList>
                  <CommandEmpty>{tc("noResults")}</CommandEmpty>
                  <CommandGroup>
                    {timezoneOptions.map((tz) => (
                      <CommandItem
                        key={tz}
                        value={tz}
                        onSelect={() => {
                          setTimezone(tz === timezone ? "" : tz)
                          setTimezoneOpen(false)
                        }}
                      >
                        <Check
                          className={cn(
                            "mr-2 h-4 w-4",
                            timezone === tz ? "opacity-100" : "opacity-0"
                          )}
                        />
                        {tz}
                      </CommandItem>
                    ))}
                  </CommandGroup>
                </CommandList>
              </Command>
            </PopoverContent>
          </Popover>
          <div className="flex items-center justify-end max-w-sm">
            <Button
              size="sm"
              onClick={handleSaveTimezone}
              disabled={!isTimezoneDirty || savingTimezone}
            >
              {savingTimezone ? tc("saving") : tc("save")}
            </Button>
          </div>
        </div>
      </div>

      <Separator />

      {/* Personalization Section */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("personalizationTitle")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("personalizationDescription")}
          </p>
        </div>
        <Button
          variant="outline"
          onClick={async () => {
            try {
              const updated = await authApi.updateProfile({ onboarding_completed: false })
              updateUser(updated)
              router.push("/onboarding")
            } catch {
              toast.error(t("personalizationFailed"))
            }
          }}
        >
          <Sparkles className="mr-2 h-4 w-4" />
          {t("redoOnboarding")}
        </Button>
      </div>

      <AvatarPickerDialog
        open={avatarDialogOpen}
        onOpenChange={setAvatarDialogOpen}
      />

      <AlertDialog open={usernameConfirmOpen} onOpenChange={setUsernameConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("usernameChangeConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("usernameChangeConfirmDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setUsernameConfirmOpen(false)
                doSaveUsername()
              }}
            >
              {t("usernameChangeConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
