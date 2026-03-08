export const APP_NAME = "FIM Agent"
export const APP_VERSION = "0.4.0"
/** All fetch() calls use empty base (same-origin), proxied by Next.js rewrites */
export function getApiBaseUrl() {
  return ""
}

/** Direct browser navigation (OAuth redirects) needs the real backend URL */
export function getApiDirectUrl() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL
  if (typeof window !== "undefined") {
    const { protocol, hostname, port } = window.location
    // Standard ports (80/443) → production reverse-proxy setup: backend is co-located on same origin
    // Non-standard port (e.g. :3000 in local dev) → backend runs separately on :8000
    if (!port || port === "80" || port === "443") {
      return `${protocol}//${hostname}`
    }
    return `${protocol}//${hostname}:8000`
  }
  return "http://localhost:8000"
}

export const ACCESS_TOKEN_KEY = "fim_access_token"
export const REFRESH_TOKEN_KEY = "fim_refresh_token"
export const USER_KEY = "fim_user"
