"use client"

import { useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Separator } from "@/components/ui/separator"
import { useAuth } from "@/contexts/auth-context"
import { authApi } from "@/lib/api"
import { toast } from "sonner"

const MAX_INSTRUCTIONS_LENGTH = 2000
const MAX_DISPLAY_NAME_LENGTH = 50

export function GeneralSettings() {
  const { user, updateUser } = useAuth()
  const t = useTranslations("settings.general")
  const tc = useTranslations("common")

  // --- Profile ---
  const [displayName, setDisplayName] = useState("")
  const [savingProfile, setSavingProfile] = useState(false)

  // --- Personal Instructions ---
  const [instructions, setInstructions] = useState("")
  const [savingInstructions, setSavingInstructions] = useState(false)

  useEffect(() => {
    if (user) {
      setDisplayName(user.display_name || "")
      setInstructions(user.system_instructions || "")
    }
  }, [user])

  const isDisplayNameDirty = displayName !== (user?.display_name || "")
  const isDisplayNameOverLimit = displayName.length > MAX_DISPLAY_NAME_LENGTH

  const isInstructionsDirty = instructions !== (user?.system_instructions || "")
  const isInstructionsOverLimit = instructions.length > MAX_INSTRUCTIONS_LENGTH

  const handleSaveProfile = async () => {
    if (!isDisplayNameDirty || isDisplayNameOverLimit) return
    setSavingProfile(true)
    try {
      const updated = await authApi.updateProfile({
        display_name: displayName.trim(),
      })
      updateUser(updated)
      toast.success(t("profileSaved"))
    } catch (err) {
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
    } catch (err) {
      toast.error(t("instructionsSaveFailed"))
    } finally {
      setSavingInstructions(false)
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
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("usernameLabel")}</label>
            <p className="text-sm text-foreground">{user?.username}</p>
          </div>

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
    </div>
  )
}
