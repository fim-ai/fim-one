"use client"

import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { authApi, ApiError } from "@/lib/api"

const MIN_PASSWORD_LENGTH = 6

export function AccountSettings() {
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState(false)
  const [error, setError] = useState("")

  const confirmMismatch =
    confirmPassword.length > 0 && newPassword !== confirmPassword
  const newTooShort =
    newPassword.length > 0 && newPassword.length < MIN_PASSWORD_LENGTH
  const canSubmit =
    currentPassword.length > 0 &&
    newPassword.length >= MIN_PASSWORD_LENGTH &&
    newPassword === confirmPassword &&
    !saving

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return

    setSaving(true)
    setError("")
    setSuccess(false)

    try {
      await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError("Failed to change password. Please try again.")
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Change Password Section */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">Change Password</h3>
          <p className="text-sm text-muted-foreground">
            Update your password to keep your account secure.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 max-w-sm">
          <div className="space-y-2">
            <label className="text-sm font-medium">Current Password</label>
            <Input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              placeholder="Enter current password"
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">New Password</label>
            <Input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Enter new password"
            />
            {newTooShort && (
              <p className="text-xs text-destructive">
                Password must be at least {MIN_PASSWORD_LENGTH} characters.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">Confirm New Password</label>
            <Input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Confirm new password"
            />
            {confirmMismatch && (
              <p className="text-xs text-destructive">
                Passwords do not match.
              </p>
            )}
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}

          {success && (
            <p className="text-sm text-green-600">
              Password changed successfully.
            </p>
          )}

          <Button type="submit" size="sm" disabled={!canSubmit}>
            {saving ? "Changing..." : "Change Password"}
          </Button>
        </form>
      </div>

      <Separator />

      {/* Danger Zone */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium text-destructive">
            Danger Zone
          </h3>
          <p className="text-sm text-muted-foreground">
            Irreversible and destructive actions.
          </p>
        </div>

        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-muted-foreground">
            Account deletion is not yet available.
          </p>
        </div>
      </div>
    </div>
  )
}
