/**
 * Client-side accessor for the soft-shelvable module flags (Skills,
 * Workflows). Like the billing flag, these live in ``system_settings`` and
 * are surfaced on the public ``/api/version`` envelope so the frontend can
 * hide nav + routes on mount without an admin-only call.
 *
 * Modules are OFF by default: a fresh install starts simple and an admin
 * enables them in Admin -> Settings -> Modules.
 */

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { apiFetch, ApiError } from "@/lib/api"

export interface ModuleFlags {
  skills: boolean
  workflows: boolean
}

const DEFAULT_FLAGS: ModuleFlags = { skills: false, workflows: false }

interface VersionEnvelope {
  modules?: Partial<ModuleFlags>
}

/**
 * Broadcast right after an admin toggles a module so sibling subtrees
 * (sidebar nav) refresh without each polling ``/api/version``.
 */
export const MODULE_FLAGS_CHANGED_EVENT = "module-flags-changed"

/** Resolve module flags once. Fail-closed (both off) on any error. */
export async function fetchModuleFlags(): Promise<ModuleFlags> {
  try {
    const env = await apiFetch<VersionEnvelope>("/api/version")
    return {
      skills: env.modules?.skills === true,
      workflows: env.modules?.workflows === true,
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return DEFAULT_FLAGS
    return DEFAULT_FLAGS
  }
}

/**
 * React hook: module flags, refreshed on the change event. Returns both
 * off until the first fetch resolves, so nav never flashes a shelved
 * module in. ``loaded`` distinguishes "still fetching" from "off" — a
 * route guard must wait for ``loaded`` before deciding to redirect.
 */
export function useModuleFlags(): ModuleFlags & { loaded: boolean } {
  const [flags, setFlags] = useState<ModuleFlags>(DEFAULT_FLAGS)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      fetchModuleFlags().then((f) => {
        if (!cancelled) {
          setFlags(f)
          setLoaded(true)
        }
      })
    }
    load()
    const onChange = () => load()
    if (typeof window !== "undefined") {
      window.addEventListener(MODULE_FLAGS_CHANGED_EVENT, onChange)
    }
    return () => {
      cancelled = true
      if (typeof window !== "undefined") {
        window.removeEventListener(MODULE_FLAGS_CHANGED_EVENT, onChange)
      }
    }
  }, [])

  return { ...flags, loaded }
}

/**
 * Route guard: redirect to the dashboard when a module is disabled.
 * Waits for the flags to load before deciding, so an enabled module is
 * never bounced during the initial fetch. Returns ``true`` while it is
 * safe to render the page (loading or enabled), ``false`` once a redirect
 * has been triggered — callers can render a spinner meanwhile.
 */
export function useModuleGuard(moduleName: keyof ModuleFlags): boolean {
  const flags = useModuleFlags()
  const router = useRouter()
  const enabled = flags[moduleName]

  useEffect(() => {
    if (flags.loaded && !enabled) {
      router.replace("/")
    }
  }, [flags.loaded, enabled, router])

  return !flags.loaded || enabled
}
