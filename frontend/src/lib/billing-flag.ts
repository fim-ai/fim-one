/**
 * Lightweight client-side accessor for the ``billing_enabled`` flag.
 *
 * The flag lives in ``system_settings`` on the backend; the user-facing
 * way to read it is the public ``/api/version`` envelope, which already
 * round-trips on every page load. Admins also see it on
 * ``/api/admin/settings`` — but we don't want to gate the user
 * sidebar on an admin-only call.
 *
 * Strategy: piggy-back on the ``/api/version`` endpoint (which
 * historically returned ``{version}``) by adding the flag there. The
 * frontend treats a missing key as ``false`` for backward compat.
 */

import { apiFetch, ApiError } from "@/lib/api"

interface VersionEnvelope {
  version?: string
  billing_enabled?: boolean
}

/**
 * Resolve the billing-enabled flag once. Returns ``false`` on any
 * failure (fail-closed): a private deployment without the version
 * endpoint shouldn't accidentally surface payment UX.
 */
export async function fetchBillingEnabled(): Promise<boolean> {
  try {
    const env = await apiFetch<VersionEnvelope>("/api/version")
    return env.billing_enabled === true
  } catch (err) {
    // Anything goes wrong — auth, network, parse — assume off.
    if (err instanceof ApiError && err.status === 401) {
      // 401 means the user isn't authenticated yet; nothing to surface.
      return false
    }
    return false
  }
}

/**
 * Window event broadcast right after a successful billing toggle so
 * sibling subtrees (admin sidebar, user nav) can refresh without each
 * polling ``/api/version`` on its own timer.
 */
export const BILLING_FLAG_CHANGED_EVENT = "billing-flag-changed"

export interface BillingFlagChangedDetail {
  enabled: boolean
}

/**
 * Admin-only: switch the flag.
 *
 * - ``setBillingEnabled(true)`` invokes the activation endpoint, which
 *   seeds plans + backfills user plan bindings the first time it
 *   runs and is a no-op on subsequent calls.
 * - ``setBillingEnabled(false)`` is a pure flag flip.
 *
 * On success we dispatch :data:`BILLING_FLAG_CHANGED_EVENT` so the
 * admin sidebar (which gates the Billing nav group on this flag)
 * re-renders without a hard reload.
 */
export type AccessModel = "off" | "freemium" | "paid_only"

export async function setBillingEnabled(
  enabled: boolean,
): Promise<{
  plans_seeded: number
  users_backfilled: number
  default_plan_id: number | null
  billing_enabled: boolean
  access_model: AccessModel
}> {
  return setAccessModel(enabled ? "freemium" : "off")
}

export async function setAccessModel(
  accessModel: AccessModel,
): Promise<{
  plans_seeded: number
  users_backfilled: number
  default_plan_id: number | null
  billing_enabled: boolean
  access_model: AccessModel
}> {
  const result = await apiFetch<{
    plans_seeded: number
    users_backfilled: number
    default_plan_id: number | null
    billing_enabled: boolean
    access_model: AccessModel
  }>("/api/admin/system/billing/toggle", {
    method: "POST",
    body: JSON.stringify({ access_model: accessModel }),
  })
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent<BillingFlagChangedDetail>(
        BILLING_FLAG_CHANGED_EVENT,
        { detail: { enabled: result.billing_enabled } },
      ),
    )
  }
  return result
}
