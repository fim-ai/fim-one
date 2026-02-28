"use client"

import { useState, useEffect } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Separator } from "@/components/ui/separator"
import { useAuth } from "@/contexts/auth-context"
import { authApi } from "@/lib/api"

const MAX_INSTRUCTIONS_LENGTH = 2000
const MAX_DISPLAY_NAME_LENGTH = 50

export function GeneralSettings() {
  const { user, updateUser } = useAuth()

  // --- Profile ---
  const [displayName, setDisplayName] = useState("")
  const [savingProfile, setSavingProfile] = useState(false)
  const [profileSaved, setProfileSaved] = useState(false)

  // --- Personal Instructions ---
  const [instructions, setInstructions] = useState("")
  const [savingInstructions, setSavingInstructions] = useState(false)
  const [instructionsSaved, setInstructionsSaved] = useState(false)

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
      setProfileSaved(true)
      setTimeout(() => setProfileSaved(false), 2000)
    } catch (err) {
      console.error("Failed to save profile:", err)
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
      setInstructionsSaved(true)
      setTimeout(() => setInstructionsSaved(false), 2000)
    } catch (err) {
      console.error("Failed to save instructions:", err)
    } finally {
      setSavingInstructions(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Profile Section */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">Profile</h3>
          <p className="text-sm text-muted-foreground">
            Your personal profile information.
          </p>
        </div>

        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Username</label>
            <p className="text-sm text-foreground">{user?.username}</p>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">Display Name</label>
            <Input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Enter a display name"
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
                {profileSaved && (
                  <span className="text-xs text-green-600">Saved</span>
                )}
                <Button
                  size="sm"
                  onClick={handleSaveProfile}
                  disabled={
                    !isDisplayNameDirty || isDisplayNameOverLimit || savingProfile
                  }
                >
                  {savingProfile ? "Saving..." : "Save"}
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
          <h3 className="text-base font-medium">Personal Instructions</h3>
          <p className="text-sm text-muted-foreground">
            These instructions will be applied to all your conversations.
            Agent-specific instructions take higher priority.
          </p>
        </div>

        <Textarea
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="E.g., Always respond in Chinese. Prefer concise answers..."
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
            {instructionsSaved && (
              <span className="text-xs text-green-600">Saved</span>
            )}
            <Button
              size="sm"
              onClick={handleSaveInstructions}
              disabled={
                !isInstructionsDirty ||
                isInstructionsOverLimit ||
                savingInstructions
              }
            >
              {savingInstructions ? "Saving..." : "Save"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
