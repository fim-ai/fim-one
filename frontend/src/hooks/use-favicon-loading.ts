import { useEffect } from "react"

/** Frames in one breathing cycle, and how long each frame is held. */
const FRAME_COUNT = 12
const FRAME_INTERVAL_MS = 100
/** Canvas edge for the generated frames; the browser rescales from this. */
const CANVAS_SIZE = 64
/**
 * How far the mark dims at the low point of the cycle. Desaturating alone turns
 * the amber into a near-white grey that vanishes against a light tab bar, so the
 * dip darkens as well: the resulting mid-grey reads on light and dark chrome
 * alike. Alpha only leans in a little, and carries the whole cue on engines
 * without `ctx.filter`.
 */
const MAX_DARKEN = 0.28
const MAX_FADE = 0.2
const MAX_FADE_NO_FILTER = 0.45
/** Source used when the live `<link>` href isn't a usable image URL. */
const ICON_FALLBACK_SRC = "/icon.svg"
/**
 * Under `prefers-reduced-motion` nothing may animate on a timer, but dropping
 * back to the plain icon would erase the busy cue altogether. Hold the deepest
 * frame of the cycle instead: a static grey mark, still visibly distinct from
 * idle. Re-asserting it stays on a slow tick because Next can swap the `<link>`
 * mid-turn; that restores the same href rather than advancing an animation.
 */
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)"
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

/** True for hrefs we generated ourselves, which must never be saved or re-read. */
function isGeneratedHref(href: string): boolean {
  return href.startsWith("data:image/png")
}

/**
 * Firefox refuses to rasterise an `<img>` SVG with no intrinsic size, and the
 * app icon carries only a viewBox. Copy the viewBox extent onto width/height,
 * then inline the result as a data URL so the canvas stays untainted and
 * `toDataURL()` keeps working.
 */
function toSizedSvgDataUrl(raw: string): string {
  const svg = new DOMParser().parseFromString(raw, "image/svg+xml").documentElement
  const viewBox = svg.getAttribute("viewBox")?.trim().split(/[\s,]+/).map(Number)
  if (!svg.getAttribute("width") && viewBox?.length === 4 && viewBox.every(Number.isFinite)) {
    svg.setAttribute("width", String(viewBox[2]))
    svg.setAttribute("height", String(viewBox[3]))
  }
  const sized = new XMLSerializer().serializeToString(svg)
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(sized)}`
}

async function loadIconImage(src: string): Promise<HTMLImageElement> {
  const res = await fetch(src)
  if (!res.ok) throw new Error(`favicon fetch failed: ${res.status}`)
  const img = new Image()
  img.src = toSizedSvgDataUrl(await res.text())
  await img.decode()
  return img
}

/**
 * Renders one cycle of the mark breathing between full colour and a muted grey.
 * The frames are baked up front so the ticker only swaps a pre-built href.
 */
function buildFrames(img: HTMLImageElement): string[] {
  const canvas = document.createElement("canvas")
  canvas.width = CANVAS_SIZE
  canvas.height = CANVAS_SIZE
  const ctx = canvas.getContext("2d")
  if (!ctx) return []

  const scale = Math.min(CANVAS_SIZE / img.naturalWidth, CANVAS_SIZE / img.naturalHeight)
  const width = img.naturalWidth * scale
  const height = img.naturalHeight * scale
  const x = (CANVAS_SIZE - width) / 2
  const y = (CANVAS_SIZE - height) / 2
  // Safari gained ctx.filter late; without it the alpha pulse carries the cue.
  const supportsFilter = typeof ctx.filter === "string"
  const fade = supportsFilter ? MAX_FADE : MAX_FADE_NO_FILTER

  const frames: string[] = []
  for (let i = 0; i < FRAME_COUNT; i++) {
    // Cosine ease: 0 at both ends of the cycle, 1 in the middle, so the loop
    // has no visible seam when it wraps.
    const dip = (1 - Math.cos((2 * Math.PI * i) / FRAME_COUNT)) / 2
    ctx.clearRect(0, 0, CANVAS_SIZE, CANVAS_SIZE)
    if (supportsFilter) ctx.filter = `grayscale(${dip}) brightness(${1 - MAX_DARKEN * dip})`
    ctx.globalAlpha = 1 - fade * dip
    ctx.drawImage(img, x, y, width, height)
    frames.push(canvas.toDataURL("image/png"))
  }
  return frames
}

/**
 * Pulses the browser-tab favicon while `loading` is true: the app mark keeps
 * its shape and breathes between full colour and grey, signalling that the
 * assistant is still working even when the tab is in the background. Restores
 * the real favicon when the turn ends (or the component unmounts).
 *
 * Next re-inserts the `icon.svg` `<link>` on soft navigation / head re-renders,
 * so holding an element reference goes stale mid-turn (the old node detaches
 * and the browser falls back to the static icon). Each tick re-queries the live
 * link instead, which re-asserts the animation onto whatever node is current.
 */
export function useFaviconLoading(loading: boolean): void {
  useEffect(() => {
    if (!loading) return

    let cancelled = false
    let timer = 0
    let savedHref = ""

    const initialHref = getIconLink().getAttribute("href")
    const src = initialHref && !isGeneratedHref(initialHref) ? initialHref : ICON_FALLBACK_SRC
    if (initialHref && !isGeneratedHref(initialHref)) savedHref = initialHref

    void (async () => {
      let frames: string[]
      try {
        frames = buildFrames(await loadIconImage(src))
      } catch {
        // The busy favicon is a cosmetic cue; leave the static icon in place.
        return
      }
      if (cancelled || frames.length === 0) return

      // Read the preference per turn rather than caching it, so an OS-level
      // change mid-session is picked up on the next run without a listener.
      const reduceMotion = window.matchMedia(REDUCED_MOTION_QUERY).matches
      // The cycle is a palindrome, so its midpoint is the deepest dip.
      const still = frames[Math.floor(frames.length / 2)]

      let frame = 0
      const tick = () => {
        const link = getIconLink()
        // Track the most recent real icon href seen on the live link, so restore
        // works even if Next swapped the element (and its href) mid-turn.
        const current = link.getAttribute("href")
        if (current && !isGeneratedHref(current)) savedHref = current
        if (reduceMotion) {
          link.setAttribute("href", still)
          return
        }
        link.setAttribute("href", frames[frame])
        frame = (frame + 1) % frames.length
      }
      tick()
      timer = window.setInterval(tick, reduceMotion ? REASSERT_INTERVAL_MS : FRAME_INTERVAL_MS)
    })()

    return () => {
      cancelled = true
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
