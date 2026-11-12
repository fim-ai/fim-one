"use client"

import { useState } from "react"
import { useTranslations, useFormatter } from "next-intl"
import { ShieldAlert, X } from "lucide-react"
import { useAuth } from "@/contexts/auth-context"
import { apiFetch, authApi, workflowApi } from "@/lib/api"
import type { SecurityNotice, SecurityNoticeItem } from "@/types/auth"

const NAMES_INLINE = 5

const activate = (path: string, method: "PUT" | "PATCH") =>
  apiFetch(path, { method, body: JSON.stringify({ is_active: true }) })

/** How each item of a kind is turned back on from the notice, where it can be. */
const TURN_BACK_ON: Record<string, (id: string) => Promise<unknown>> = {
  workflow_access: (id) => workflowApi.resumeAccess(id),
  agents_custom_endpoint: (id) => activate(`/api/agents/${id}`, "PUT"),
  connectors: (id) => activate(`/api/connectors/${id}`, "PUT"),
  mcp_servers: (id) => activate(`/api/mcp-servers/${id}`, "PUT"),
  channels: (id) => activate(`/api/channels/${id}`, "PATCH"),
  api_keys: (id) => activate(`/api/me/api-keys/${id}/active`, "PATCH"),
}

function namesOf(item: SecurityNoticeItem, more: (count: number) => string): string {
  const names = item.items.slice(0, NAMES_INLINE).map((i) => (i.role ? `${i.name} (${i.role})` : i.name))
  const rest = item.count - names.length
  return rest > 0 ? `${names.join(", ")}, ${more(rest)}` : names.join(", ")
}

/**
 * Shown after an emailed code first verified the account's address: what was
 * switched off then, and how to turn each back on. Stays until dismissed.
 */
export function SecurityNoticeBanner() {
  const { user, updateUser } = useAuth()
  const notice = user?.security_notice
  if (!notice) return null
  return <NoticeBody notice={notice} onDismissed={() => updateUser({ security_notice: null })} />
}

export function NoticeBody({ notice, onDismissed }: { notice: SecurityNotice; onDismissed: () => void }) {
  const t = useTranslations("securityNotice")
  const tLayout = useTranslations("layout")
  const format = useFormatter()
  const [open, setOpen] = useState(false)
  const [done, setDone] = useState<Record<string, boolean>>({})
  const [error, setError] = useState(false)

  const run = async (key: string, action: () => Promise<unknown>) => {
    setError(false)
    try {
      await action()
      setDone((prev) => ({ ...prev, [key]: true }))
    } catch {
      setError(true)
    }
  }

  const dismiss = async () => {
    try {
      await authApi.dismissSecurityNotice()
      onDismissed()
    } catch {
      setError(true)
    }
  }

  const more = (count: number) => t("more", { count })
  const date = format.dateTime(new Date(notice.at), { dateStyle: "medium", timeStyle: "short" })
  const signIn = notice.sign_in ?? {}
  const lines: string[] = []
  if (signIn.password_removed) lines.push(t("signInPassword"))
  if (signIn.second_factor_removed) lines.push(t("signInSecondFactor"))
  if (signIn.providers_unlinked?.length) {
    lines.push(t("signInProviders", { providers: signIn.providers_unlinked.join(", ") }))
  }
  const entries: { key: string; text: string; kind: string; items: SecurityNoticeItem["items"] }[] = []
  if (signIn.api_keys?.length) {
    const keys = signIn.api_keys
    entries.push({
      key: "api_keys",
      kind: "api_keys",
      text: t("signInApiKeys", { names: keys.map((k) => k.name).join(", ") }),
      items: keys,
    })
  }
  for (const item of notice.items) {
    entries.push({
      key: item.kind,
      kind: item.kind,
      text: t(`kind_${item.kind}`, { names: namesOf(item, more), count: item.count }),
      items: item.items,
    })
  }

  return (
    <div
      role="status"
      data-testid="security-notice"
      className="border-b border-amber-500/30 bg-amber-500/15 px-4 py-2 text-sm text-amber-900 dark:text-amber-200 shrink-0"
    >
      <div className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="flex-1 space-y-1">
          <p className="font-medium">{t("title", { date })}</p>
          <p>{t("intro")}</p>
          <button type="button" className="underline underline-offset-2" onClick={() => setOpen((v) => !v)}>
            {open ? t("hideDetails") : t("details")}
          </button>
          {open && (
            <ul className="list-disc space-y-1 pl-5">
              {lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
              {entries.map((entry) => {
                const turnOn = TURN_BACK_ON[entry.kind]
                return (
                  <li key={entry.key}>
                    {entry.text}
                    {turnOn && (
                      <span className="ml-2 inline-flex flex-wrap gap-2">
                        {entry.items.map((it) => {
                          const doneKey = `${entry.kind}:${it.id}`
                          return (
                            <button
                              key={it.id}
                              type="button"
                              disabled={done[doneKey]}
                              className="rounded border border-amber-600/40 px-1.5 py-0.5 text-xs hover:bg-amber-500/20 disabled:opacity-60"
                              onClick={() => run(doneKey, () => turnOn(it.id))}
                            >
                              {it.name}: {done[doneKey] ? t("resumed") : t("resume")}
                            </button>
                          )
                        })}
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          {notice.restorable.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-1">
              {notice.restorable.map((key) => (
                <button
                  key={key}
                  type="button"
                  disabled={done[key]}
                  className="rounded border border-amber-600/40 px-2 py-0.5 text-xs hover:bg-amber-500/20 disabled:opacity-60"
                  onClick={() => run(key, () => authApi.restoreFromSecurityNotice(key))}
                >
                  {done[key]
                    ? t("restored")
                    : key === "system_instructions"
                      ? t("restoreSystemInstructions")
                      : t("restoreDefaultAgent")}
                </button>
              ))}
            </div>
          )}
          {error && <p className="text-destructive">{t("failed")}</p>}
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 rounded p-0.5 transition-colors hover:bg-amber-500/20"
          aria-label={tLayout("dismiss")}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
