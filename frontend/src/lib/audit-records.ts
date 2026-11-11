/**
 * Audit records as the three audit pages read them.
 *
 * The platform administrators' list (`/api/admin/audit-log`, `/api/admin/call-log`)
 * names the actor `admin_id` / `admin_username`; an organization's list
 * (`/api/orgs/{id}/audit-log`) names it `actor_id` / `actor_label` and has no
 * address fields at all. Both are read as one {@link AuditRecord}.
 */

export type AuditSource = "audit" | "login" | "review" | "call"

export interface AuditChange {
  from?: unknown
  to?: unknown
  changed?: boolean
}

export interface AuditRecord {
  id: string
  source: AuditSource
  created_at: string
  action: string
  actor_id: string | null
  actor_label: string | null
  actor_kind: string | null
  org_id: string | null
  target_type: string | null
  target_id: string | null
  target_label: string | null
  outcome: string
  changes: Record<string, AuditChange> | null
  detail: string | null
  /** Only in the platform administrators' list. */
  ip_address: string | null
  user_agent: string | null
  extra: Record<string, unknown>
}

export interface AuditRecordPage {
  items: AuditRecord[]
  total: number
  page: number
  size: number
  pages: number
}

/** What both lists send for one row. */
export type RawAuditRecord = Record<string, unknown>

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null
}

export function normalizeAuditRecord(raw: RawAuditRecord): AuditRecord {
  const changes = raw.changes
  const extra = raw.extra
  return {
    id: String(raw.id ?? ""),
    source: (text(raw.source) ?? "audit") as AuditSource,
    created_at: text(raw.created_at) ?? "",
    action: text(raw.action) ?? "",
    actor_id: text(raw.actor_id) ?? text(raw.admin_id),
    actor_label: text(raw.actor_label) ?? text(raw.admin_username),
    actor_kind: text(raw.actor_kind),
    org_id: text(raw.org_id),
    target_type: text(raw.target_type),
    target_id: text(raw.target_id),
    target_label: text(raw.target_label),
    // Rows written before the outcome was recorded are successful actions.
    outcome: text(raw.outcome) ?? "success",
    changes:
      changes && typeof changes === "object" && !Array.isArray(changes)
        ? (changes as Record<string, AuditChange>)
        : null,
    detail: text(raw.detail),
    ip_address: text(raw.ip_address),
    user_agent: text(raw.user_agent),
    extra:
      extra && typeof extra === "object" && !Array.isArray(extra)
        ? (extra as Record<string, unknown>)
        : {},
  }
}

export function normalizeAuditPage(raw: {
  items?: RawAuditRecord[]
  total?: number
  page?: number
  size?: number
  pages?: number
}): AuditRecordPage {
  return {
    items: (raw.items ?? []).map(normalizeAuditRecord),
    total: raw.total ?? 0,
    page: raw.page ?? 1,
    size: raw.size ?? 50,
    pages: raw.pages ?? 1,
  }
}

/** The value a filter select holds when it filters nothing. */
export const ALL = "__all__"

export const AUDIT_OUTCOMES = ["success", "denied", "failed"] as const

export interface AuditQuery {
  page?: number
  size?: number
  source?: AuditSource
  action?: string
  /** `admin_id` on the platform list, `actor_id` on an organization's. */
  actorId?: string
  actorParam?: "admin_id" | "actor_id" | "user_id"
  orgId?: string
  outcome?: string
  ipAddress?: string
  dateFrom?: string
  dateTo?: string
}

/** The query string for a list or an export; a filter left at "all" or empty is left out. */
export function buildAuditQuery(query: AuditQuery): URLSearchParams {
  const params = new URLSearchParams()
  const set = (name: string, value: string | undefined) => {
    const trimmed = value?.trim()
    if (trimmed && trimmed !== ALL) params.set(name, trimmed)
  }
  if (query.page !== undefined) params.set("page", String(query.page))
  if (query.size !== undefined) params.set("size", String(query.size))
  if (query.source && query.source !== "audit") params.set("source", query.source)
  set("action", query.action)
  set(query.actorParam ?? "admin_id", query.actorId)
  set("org_id", query.orgId)
  set("outcome", query.outcome)
  set("ip_address", query.ipAddress)
  set("date_from", query.dateFrom)
  set("date_to", query.dateTo)
  return params
}

/** One changed field as text: "member → admin", or the label for a secret. */
export function formatAuditChange(change: AuditChange, changedLabel: string): string {
  if (change.changed) return changedLabel
  const show = (value: unknown) =>
    value === null || value === undefined
      ? "—"
      : typeof value === "string"
        ? value
        : JSON.stringify(value)
  return `${show(change.from)} → ${show(change.to)}`
}

/**
 * The message key of an action name: the message library reads a dot as
 * nesting, so "org.member_add" is stored as "org__member_add".
 */
export function auditActionKey(action: string): string {
  return action.replace(/\./g, "__")
}

/** The request line of a call record: "GET https://host/path", or null. */
export function callRequestLine(record: AuditRecord): string | null {
  const method = text(record.extra.request_method)
  const url = text(record.extra.request_url)
  if (!method && !url) return null
  return [method, url].filter(Boolean).join(" ")
}

export function callStatus(record: AuditRecord): number | null {
  const status = record.extra.response_status
  return typeof status === "number" ? status : null
}

export function callDurationMs(record: AuditRecord): number | null {
  const ms = record.extra.response_time_ms
  return typeof ms === "number" ? ms : null
}

/** A browser and system out of a user-agent string: "Chrome on macOS". */
export function describeUserAgent(ua: string | null): string | null {
  if (!ua) return null
  // Edge and Chrome both say "Chrome"; Chrome and Safari both say "Safari".
  const browsers: Array<[RegExp, string]> = [
    [/Edg\//, "Edge"],
    [/Firefox\//, "Firefox"],
    [/Chrome\//, "Chrome"],
    [/Safari\//, "Safari"],
  ]
  const systems: Array<[RegExp, string]> = [
    [/iPhone|iPad/, "iOS"],
    [/Android/, "Android"],
    [/Windows/, "Windows"],
    [/Macintosh|Mac OS/, "macOS"],
    [/Linux/, "Linux"],
  ]
  const browser = browsers.find(([pattern]) => pattern.test(ua))?.[1]
  const system = systems.find(([pattern]) => pattern.test(ua))?.[1]
  if (!browser) return ua.length > 40 ? `${ua.slice(0, 40)}…` : ua
  return system ? `${browser} on ${system}` : browser
}
