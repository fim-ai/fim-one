import { useEffect } from "react"

/** Glyph badged into the favicon while a turn is running. */
const BUSY_GLYPH = "⏳"
/**
 * Next re-inserts the `icon.svg` <link> on soft navigation / head re-renders,
 * so holding an element reference goes stale mid-turn (the old node detaches
 * and the browser falls back to the static icon). Instead of animating frames
 * on a captured node, re-query the live link and re-assert the busy href on a
 * slow tick.
 */
const REASSERT_INTERVAL_MS = 1000

function getIconLink(): HTMLLinkElement {
  // When several icon links exist the browser honours the last one in
  // document order, so target that.
  const links = document.querySelectorAll<HTMLLinkElement>("link[rel~='icon']")
  if (links.length > 0) return links[links.length - 1]
  const link = document.createElement("link")
  link.rel = "icon"
  document.head.appendChild(link)
  return link
}

/**
 * Swaps the browser-tab favicon to a busy glyph while `loading` is true,
 * signalling that the assistant is still working even when the tab is in the
 * background. Restores the real favicon when the turn ends (or the component
 * unmounts).
 */
export function useFaviconLoading(loading: boolean): void {
  useEffect(() => {
    if (!loading) return

    const canvas = document.createElement("canvas")
    canvas.width = 64
    canvas.height = 64
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    ctx.font = "56px serif"
    ctx.textAlign = "center"
    ctx.textBaseline = "middle"
    ctx.fillText(BUSY_GLYPH, 32, 36)
    const busyHref = canvas.toDataURL("image/png")

    // Track the most recent real icon href seen on the live link, so restore
    // works even if Next swapped the element (and its href) mid-turn.
    let savedHref = ""
    const apply = () => {
      const link = getIconLink()
      const current = link.getAttribute("href")
      if (current && current !== busyHref) savedHref = current
      if (current !== busyHref) link.setAttribute("href", busyHref)
    }
    apply()
    const timer = window.setInterval(apply, REASSERT_INTERVAL_MS)

    return () => {
      window.clearInterval(timer)
      const link = getIconLink()
      if (savedHref) {
        link.setAttribute("href", savedHref)
      } else {
        link.removeAttribute("href")
      }
    }
  }, [loading])
}
