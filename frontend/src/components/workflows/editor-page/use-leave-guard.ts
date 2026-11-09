import { useCallback, useEffect, useRef, useState } from "react"

/**
 * Where a click on a link would take the app, when that is another page of
 * this app; null for everything a leave guard should let through: clicks
 * that open elsewhere (new tab, modifier keys, `target`, downloads), links
 * to other origins (the browser's own unload prompt covers those), and
 * links that stay on the current page.
 */
export function inAppDestination(
  event: Pick<MouseEvent, "button" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey" | "defaultPrevented">,
  anchor: HTMLAnchorElement | null,
  current: Pick<Location, "origin" | "pathname" | "href">,
): string | null {
  if (!anchor) return null
  if (event.defaultPrevented || event.button !== 0) return null
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null
  if (anchor.hasAttribute("download")) return null
  const target = anchor.getAttribute("target")
  if (target && target !== "_self") return null
  const href = anchor.getAttribute("href")
  if (!href) return null
  let url: URL
  try {
    url = new URL(href, current.href)
  } catch {
    return null
  }
  if (url.origin !== current.origin) return null
  if (url.pathname === current.pathname) return null
  return url.pathname + url.search + url.hash
}

/**
 * When `hasUnsavedChanges()` says so at the moment of the click, a click on
 * a link to another page of the app does not navigate: the destination is
 * kept in `pendingHref` for the page to ask "stay or leave?". The question
 * is put to a function, not to a flag, because the flag a page renders with
 * lags behind the last edit.
 *
 * The App Router has no hook to veto a navigation, so the click is caught
 * on the document in the capture phase, before the link's own handler.
 * Navigations started in code (`router.push`) and the browser's Back button
 * do not pass through here.
 */
export function useLeaveGuard(hasUnsavedChanges: () => boolean) {
  const [pendingHref, setPendingHref] = useState<string | null>(null)
  const hasUnsavedChangesRef = useRef(hasUnsavedChanges)
  hasUnsavedChangesRef.current = hasUnsavedChanges

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const anchor =
        event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[href]") : null
      const destination = inAppDestination(event, anchor, window.location)
      if (!destination || !hasUnsavedChangesRef.current()) return
      event.preventDefault()
      event.stopPropagation()
      setPendingHref(destination)
    }
    document.addEventListener("click", onClick, true)
    return () => document.removeEventListener("click", onClick, true)
  }, [])

  const stay = useCallback(() => setPendingHref(null), [])

  return { pendingHref, stay }
}
