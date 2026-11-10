"use client"

import { useEffect, useState } from "react"
import { useTranslations } from "next-intl"
import { Loader2, Plus, Pencil } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ApiError } from "@/lib/api"
import { channelsApi } from "@/lib/api/channels"
import { getErrorMessage } from "@/lib/error-utils"
import {
  CREATABLE_CHANNEL_TYPES,
  WEBHOOK_URL_EXAMPLES,
  isWebhookChannelType,
  isWebhookUrlFor,
} from "@/types/channel"
import type {
  Channel,
  ChannelConfigInput,
  ChannelCreateRequest,
  ChannelType,
  ChatInfo,
  FeishuChannelConfigInput,
  WebhookChannelConfigInput,
} from "@/types/channel"
import { FeishuChatPicker } from "./feishu-chat-picker"

interface ChannelFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  channel: Channel | null // null = create mode
  orgId: string
  onSaved: (channel: Channel) => void
}

// Server error code -> the form field it is about.
const SERVER_FIELD_ERRORS: Record<string, keyof FieldErrors> = {
  feishu_channel_requires_encrypt_key: "encrypt_key",
  channel_webhook_url_invalid: "webhook_url",
  channel_email_recipients_required: "email_to",
  channel_email_not_permitted: "email_to",
}

type FieldErrors = Partial<{
  name: string
  app_id: string
  app_secret: string
  chat_id: string
  verification_token: string
  encrypt_key: string
  webhook_url: string
  email_to: string
}>

export function ChannelFormDialog({
  open,
  onOpenChange,
  channel,
  orgId,
  onSaved,
}: ChannelFormDialogProps) {
  const t = useTranslations("channels")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  const isEditing = channel !== null

  const [type, setType] = useState<ChannelType>("feishu")
  const [name, setName] = useState("")
  const [appId, setAppId] = useState("")
  const [appSecret, setAppSecret] = useState("")
  const [chatId, setChatId] = useState("")
  const [verificationToken, setVerificationToken] = useState("")
  const [encryptKey, setEncryptKey] = useState("")
  const [webhookUrl, setWebhookUrl] = useState("")
  const [emailTo, setEmailTo] = useState("")

  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const [showPicker, setShowPicker] = useState(false)

  // Picker prerequisites: app_id is required; for create mode the user must
  // also have typed an app_secret. For edit mode, the server can fall back
  // to the stored (encrypted) secret.
  const canOpenPicker =
    appId.trim().length > 0 &&
    (isEditing
      ? appSecret.length > 0 ||
        (channel?.config.app_secret_configured ?? false)
      : appSecret.length > 0)

  // Reset / prefill on open
  useEffect(() => {
    if (!open) return
    setFieldErrors({})
    if (channel) {
      setType(channel.type)
      setName(channel.name)
      setAppId(channel.config.app_id ?? "")
      setAppSecret("")
      setChatId(channel.config.chat_id ?? "")
      setVerificationToken("")
      setEncryptKey("")
      setWebhookUrl("")
      setEmailTo(channel.config.to ?? "")
    } else {
      setType("feishu")
      setName("")
      setAppId("")
      setAppSecret("")
      setChatId("")
      setVerificationToken("")
      setEncryptKey("")
      setWebhookUrl("")
      setEmailTo("")
    }
  }, [open, channel])

  // Dirty state: create mode = any required field typed; edit mode = any field differs
  const isDirty = (() => {
    if (!channel) {
      return (
        name.trim().length > 0 ||
        appId.trim().length > 0 ||
        appSecret.length > 0 ||
        chatId.trim().length > 0 ||
        verificationToken.length > 0 ||
        encryptKey.length > 0 ||
        webhookUrl.trim().length > 0 ||
        emailTo.trim().length > 0
      )
    }
    return (
      name !== channel.name ||
      appId !== (channel.config.app_id ?? "") ||
      chatId !== (channel.config.chat_id ?? "") ||
      appSecret.length > 0 ||
      verificationToken.length > 0 ||
      encryptKey.length > 0 ||
      webhookUrl.trim().length > 0 ||
      emailTo !== (channel.config.to ?? "")
    )
  })()

  const clearFieldError = (key: keyof FieldErrors) => {
    setFieldErrors((prev) => {
      if (!prev[key]) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const handleClose = (next: boolean) => {
    if (!next && isDirty) {
      setShowCloseConfirm(true)
      return
    }
    onOpenChange(next)
  }

  const validate = (): FieldErrors => {
    const errors: FieldErrors = {}
    if (!name.trim()) errors.name = t("form.nameRequired")
    if (type === "feishu") {
      if (!appId.trim()) errors.app_id = t("form.app_id_required")
      if (!chatId.trim()) errors.chat_id = t("form.chat_id_required")
      if (!isEditing && !appSecret) {
        errors.app_secret = t("form.app_secret_required")
      }
      if (!isEditing && !verificationToken) {
        errors.verification_token = t("form.verification_token_required")
      }
      // Callback signature verification fails closed without the key, so
      // it is required: on create always, on edit when not yet configured.
      if (
        !encryptKey &&
        !(isEditing && channel?.config.encrypt_key_configured)
      ) {
        errors.encrypt_key = t("form.encrypt_key_required")
      }
    } else if (isWebhookChannelType(type)) {
      // The stored URL is never sent back, so on edit a blank field keeps it.
      const url = webhookUrl.trim()
      if (!url) {
        if (!isEditing) errors.webhook_url = t("form.webhook_url_required")
      } else if (!isWebhookUrlFor(type, url)) {
        errors.webhook_url = t("form.webhook_url_invalid")
      }
    } else if (type === "email") {
      const addresses = emailTo
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean)
      if (addresses.length === 0 || addresses.some((a) => !a.includes("@"))) {
        errors.email_to = t("form.email_to_required")
      }
    }
    return errors
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const errors = validate()
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }

    setIsSubmitting(true)
    try {
      let config: ChannelConfigInput
      if (isWebhookChannelType(type)) {
        const webhookConfig: WebhookChannelConfigInput = {}
        if (webhookUrl.trim()) webhookConfig.webhook_url = webhookUrl.trim()
        config = webhookConfig
      } else if (type === "email") {
        config = { to: emailTo.trim() }
      } else {
        const feishuConfig: FeishuChannelConfigInput = {
          app_id: appId.trim(),
          chat_id: chatId.trim(),
        }
        // Only include secret fields when user typed something; empty means "keep as-is"
        if (appSecret) feishuConfig.app_secret = appSecret
        if (verificationToken) feishuConfig.verification_token = verificationToken
        if (encryptKey) feishuConfig.encrypt_key = encryptKey
        config = feishuConfig
      }

      let saved: Channel
      if (channel) {
        saved = await channelsApi.update(channel.id, {
          name: name.trim(),
          config,
        })
        toast.success(t("messages.updated"))
      } else {
        const body: ChannelCreateRequest = {
          name: name.trim(),
          type,
          org_id: orgId,
          config,
        }
        saved = await channelsApi.create(body)
        toast.success(t("messages.created"))
      }
      onSaved(saved)
      onOpenChange(false)
    } catch (err: unknown) {
      // A 400 that names a field is shown under that field; the rest toast.
      const code = err instanceof ApiError ? err.errorCode : null
      const field = code ? SERVER_FIELD_ERRORS[code] : undefined
      if (field) {
        setFieldErrors({ [field]: getErrorMessage(err, tError) })
      } else {
        toast.error(getErrorMessage(err, tError))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={handleClose}>
        <DialogContent
          className="sm:max-w-lg max-h-[90vh] overflow-y-auto"
          onInteractOutside={(e) => {
            // Picker is layered above this dialog — clicks inside it count as
            // "outside" the parent content, which would spuriously trigger the
            // discard-edits prompt. Let the picker own its own close logic.
            if (showPicker) {
              e.preventDefault()
              return
            }
            if (isDirty) {
              e.preventDefault()
              setShowCloseConfirm(true)
            }
          }}
          onEscapeKeyDown={(e) => {
            if (showPicker) {
              e.preventDefault()
            }
          }}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {isEditing ? (
                <Pencil className="h-4 w-4" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              {isEditing ? t("form.editTitle") : t("form.createTitle")}
            </DialogTitle>
            <DialogDescription>
              {isEditing
                ? t("form.editDescription")
                : t("form.createDescription")}
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* General */}
            <fieldset className="space-y-3">
              <legend className="text-sm font-semibold text-foreground">
                {t("form.sectionGeneral")}
              </legend>

              <div className="space-y-1.5">
                <Label htmlFor="channel-type" className="text-sm font-medium">
                  {t("form.type")} <span className="text-destructive">*</span>
                </Label>
                <Select
                  value={type}
                  onValueChange={(v) => {
                    setType(v as ChannelType)
                    setFieldErrors({})
                  }}
                  disabled={isEditing}
                >
                  <SelectTrigger id="channel-type" className="w-full">
                    <SelectValue placeholder={t("form.typePlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {CREATABLE_CHANNEL_TYPES.map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(`types.${value}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {t("form.typeHint")}
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="channel-name" className="text-sm font-medium">
                  {t("form.name")} <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="channel-name"
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value)
                    clearFieldError("name")
                  }}
                  placeholder={t("form.namePlaceholder")}
                  aria-invalid={fieldErrors.name ? true : undefined}
                />
                {fieldErrors.name && (
                  <p className="text-sm text-destructive">{fieldErrors.name}</p>
                )}
              </div>
            </fieldset>

            {/* Feishu credentials */}
            {type === "feishu" && (
              <fieldset className="space-y-3">
                <legend className="text-sm font-semibold text-foreground">
                  {t("form.sectionFeishu")}
                </legend>
                <p className="text-xs text-muted-foreground">
                  {t("form.sectionFeishuDescription")}
                </p>

                <div className="space-y-1.5">
                  <Label htmlFor="channel-app-id" className="text-sm font-medium">
                    {t("form.app_id")} <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    id="channel-app-id"
                    value={appId}
                    onChange={(e) => {
                      setAppId(e.target.value)
                      clearFieldError("app_id")
                    }}
                    placeholder={t("form.app_id_placeholder")}
                    aria-invalid={fieldErrors.app_id ? true : undefined}
                  />
                  {fieldErrors.app_id && (
                    <p className="text-sm text-destructive">
                      {fieldErrors.app_id}
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label
                    htmlFor="channel-app-secret"
                    className="text-sm font-medium"
                  >
                    {t("form.app_secret")}{" "}
                    {!isEditing && (
                      <span className="text-destructive">*</span>
                    )}
                  </Label>
                  <Input
                    id="channel-app-secret"
                    type="password"
                    autoComplete="new-password"
                    value={appSecret}
                    onChange={(e) => {
                      setAppSecret(e.target.value)
                      clearFieldError("app_secret")
                    }}
                    placeholder={
                      isEditing && channel?.config.app_secret_configured
                        ? t("form.app_secret_edit_placeholder")
                        : t("form.app_secret_placeholder")
                    }
                    aria-invalid={fieldErrors.app_secret ? true : undefined}
                  />
                  {fieldErrors.app_secret && (
                    <p className="text-sm text-destructive">
                      {fieldErrors.app_secret}
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label
                      htmlFor="channel-chat-id"
                      className="text-sm font-medium"
                    >
                      {t("form.chat_id")}{" "}
                      <span className="text-destructive">*</span>
                    </Label>
                    <button
                      type="button"
                      onClick={() => setShowPicker(true)}
                      disabled={!canOpenPicker}
                      title={
                        !canOpenPicker
                          ? t("picker.needCredentials")
                          : undefined
                      }
                      className="text-xs text-primary hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground disabled:no-underline"
                    >
                      {t("form.pickChat")}
                    </button>
                  </div>
                  <Input
                    id="channel-chat-id"
                    value={chatId}
                    onChange={(e) => {
                      setChatId(e.target.value)
                      clearFieldError("chat_id")
                    }}
                    placeholder={t("form.chat_id_placeholder")}
                    aria-invalid={fieldErrors.chat_id ? true : undefined}
                  />
                  <p className="text-xs text-muted-foreground">
                    {t("form.chat_id_hint")}
                  </p>
                  {fieldErrors.chat_id && (
                    <p className="text-sm text-destructive">
                      {fieldErrors.chat_id}
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label
                    htmlFor="channel-verif-token"
                    className="text-sm font-medium"
                  >
                    {t("form.verification_token")}{" "}
                    {!isEditing && (
                      <span className="text-destructive">*</span>
                    )}
                  </Label>
                  <Input
                    id="channel-verif-token"
                    type="password"
                    autoComplete="new-password"
                    value={verificationToken}
                    onChange={(e) => {
                      setVerificationToken(e.target.value)
                      clearFieldError("verification_token")
                    }}
                    placeholder={
                      isEditing &&
                      channel?.config.verification_token_configured
                        ? t("form.verification_token_edit_placeholder")
                        : t("form.verification_token_placeholder")
                    }
                    aria-invalid={
                      fieldErrors.verification_token ? true : undefined
                    }
                  />
                  {fieldErrors.verification_token ? (
                    <p className="text-sm text-destructive">
                      {fieldErrors.verification_token}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {t("form.verification_token_hint")}
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label
                    htmlFor="channel-encrypt-key"
                    className="text-sm font-medium"
                  >
                    {t("form.encrypt_key")}
                  </Label>
                  <Input
                    id="channel-encrypt-key"
                    type="password"
                    autoComplete="new-password"
                    value={encryptKey}
                    onChange={(e) => {
                      setEncryptKey(e.target.value)
                      clearFieldError("encrypt_key")
                    }}
                    placeholder={
                      isEditing && channel?.config.encrypt_key_configured
                        ? t("form.encrypt_key_edit_placeholder")
                        : t("form.encrypt_key_placeholder")
                    }
                    aria-invalid={fieldErrors.encrypt_key ? true : undefined}
                  />
                  {fieldErrors.encrypt_key && (
                    <p className="text-sm text-destructive">
                      {fieldErrors.encrypt_key}
                    </p>
                  )}
                </div>
              </fieldset>
            )}

            {/* Webhook URL */}
            {isWebhookChannelType(type) && (
              <fieldset className="space-y-3">
                <legend className="text-sm font-semibold text-foreground">
                  {t("form.sectionWebhook")}
                </legend>
                <p className="text-xs text-muted-foreground">
                  {t("form.sectionWebhookDescription")}
                </p>
                <div className="space-y-1.5">
                  <Label
                    htmlFor="channel-webhook-url"
                    className="text-sm font-medium"
                  >
                    {t("form.webhook_url")}{" "}
                    {!isEditing && <span className="text-destructive">*</span>}
                  </Label>
                  <Input
                    id="channel-webhook-url"
                    type="password"
                    autoComplete="new-password"
                    value={webhookUrl}
                    onChange={(e) => {
                      setWebhookUrl(e.target.value)
                      clearFieldError("webhook_url")
                    }}
                    placeholder={
                      isEditing
                        ? t("form.webhook_url_edit_placeholder")
                        : WEBHOOK_URL_EXAMPLES[type]
                    }
                    aria-invalid={fieldErrors.webhook_url ? true : undefined}
                  />
                  {fieldErrors.webhook_url && (
                    <p className="text-sm text-destructive">
                      {fieldErrors.webhook_url}
                    </p>
                  )}
                </div>
              </fieldset>
            )}

            {/* Email recipients */}
            {type === "email" && (
              <fieldset className="space-y-3">
                <legend className="text-sm font-semibold text-foreground">
                  {t("form.sectionEmail")}
                </legend>
                <p className="text-xs text-muted-foreground">
                  {t("form.sectionEmailDescription")}
                </p>
                <div className="space-y-1.5">
                  <Label
                    htmlFor="channel-email-to"
                    className="text-sm font-medium"
                  >
                    {t("form.email_to")}{" "}
                    <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    id="channel-email-to"
                    value={emailTo}
                    onChange={(e) => {
                      setEmailTo(e.target.value)
                      clearFieldError("email_to")
                    }}
                    placeholder={t("form.email_to_placeholder")}
                    aria-invalid={fieldErrors.email_to ? true : undefined}
                  />
                  {fieldErrors.email_to ? (
                    <p className="text-sm text-destructive">
                      {fieldErrors.email_to}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {t("form.email_to_hint")}
                    </p>
                  )}
                </div>
              </fieldset>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => handleClose(false)}
                disabled={isSubmitting}
              >
                {tc("cancel")}
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                {isEditing ? tc("save") : tc("create")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={showCloseConfirm} onOpenChange={setShowCloseConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("discard.title")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("discard.description")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("keepEditing")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setShowCloseConfirm(false)
                onOpenChange(false)
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {t("discard.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <FeishuChatPicker
        open={showPicker}
        onOpenChange={setShowPicker}
        appId={appId}
        appSecret={appSecret}
        channelId={channel?.id ?? null}
        orgId={orgId}
        onSelect={(chat: ChatInfo) => {
          setChatId(chat.chat_id)
          clearFieldError("chat_id")
        }}
      />
    </>
  )
}
