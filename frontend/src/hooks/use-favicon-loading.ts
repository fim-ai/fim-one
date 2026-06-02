import { useEffect } from "react"

/** Moon-phase frames cycled into the favicon while a turn is streaming. */
const MOON_FRAMES = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
const FRAME_INTERVAL_MS = 180

function getIconLink(): HTMLLinkElement {
  let link = document.querySelector<HTMLLinkElement>("link[rel~='icon']")
  if (!link) {
    link = document.createElement("link")
    link.rel = "icon"
    document.head.appendChild(link)
  }
  return link
}

/**
 * Animates the browser-tab favicon into a cycling moon while `loading` is true,
 * signalling to the user that the assistant is still streaming / thinking even
 * when the tab is in the background. Restores the original favicon when the turn
 * ends (or the component unmounts).
 *
 * The original `href` is captured on the loading edge — not at mount — so we
 * always restore the real icon even if Next swaps `icon.svg` in late.
 */
export function useFaviconLoading(loading: boolean): void {
  useEffect(() => {
    if (!loading) return

    const link = getIconLink()
    const savedHref = link.getAttribute("href")

    const canvas = document.createElement("canvas")
    canvas.width = 64
    canvas.height = 64
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    let i = 0
    const draw = () => {
      ctx.clearRect(0, 0, 64, 64)
      ctx.font = "56px serif"
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"
      ctx.fillText(MOON_FRAMES[i++ % MOON_FRAMES.length], 32, 36)
      link.setAttribute("href", canvas.toDataURL("image/png"))
    }
    draw()
    const timer = window.setInterval(draw, FRAME_INTERVAL_MS)

    return () => {
      window.clearInterval(timer)
      if (savedHref) {
        link.setAttribute("href", savedHref)
      } else {
        link.removeAttribute("href")
      }
    }
  }, [loading])
}
