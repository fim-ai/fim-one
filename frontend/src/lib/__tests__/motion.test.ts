import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import {
  MOTION_DURATION,
  MOTION_EASE_IN_OUT,
  MOTION_EASE_OUT,
} from "@/lib/motion"

/**
 * The motion scale is declared twice on purpose: once here for Motion
 * components, once as custom properties in globals.css for CSS keyframes.
 * Nothing at runtime forces the two to agree, and a silent drift would show up
 * as CSS and JS animations running at visibly different speeds. These tests
 * are the thing that keeps them honest.
 */

const css = readFileSync(
  path.resolve(__dirname, "../../app/globals.css"),
  "utf8",
)

function cssVar(name: string): string {
  const match = css.match(new RegExp(`${name}:\\s*([^;]+);`))
  if (!match) throw new Error(`${name} is not declared in globals.css`)
  return match[1].trim()
}

function bezierToCss(points: readonly number[]): string {
  return `cubic-bezier(${points.join(", ")})`
}

describe("motion scale", () => {
  it.each([
    ["--motion-duration-fast", MOTION_DURATION.fast],
    ["--motion-duration-base", MOTION_DURATION.base],
    ["--motion-duration-slow", MOTION_DURATION.slow],
  ])("%s matches the TS value", (name, seconds) => {
    // CSS carries milliseconds, lib/motion.ts carries seconds.
    expect(cssVar(name)).toBe(`${Math.round(seconds * 1000)}ms`)
  })

  it.each([
    ["--motion-ease-out", MOTION_EASE_OUT],
    ["--motion-ease-in-out", MOTION_EASE_IN_OUT],
  ])("%s matches the TS curve", (name, points) => {
    expect(cssVar(name)).toBe(bezierToCss(points))
  })

  it("orders the durations from fast to slow", () => {
    expect(MOTION_DURATION.fast).toBeLessThan(MOTION_DURATION.base)
    expect(MOTION_DURATION.base).toBeLessThan(MOTION_DURATION.slow)
  })
})

describe("reduced motion coverage", () => {
  // Every keyframe animation declared in globals.css needs a matching
  // reduced-motion override. Enumerate the selectors that carry an `animation`
  // shorthand and assert each one is named somewhere inside a
  // prefers-reduced-motion block, with the documented exceptions.
  const reduceBlocks = css.match(
    /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\n\}/g,
  )

  it("declares at least one reduced-motion block", () => {
    expect(reduceBlocks).not.toBeNull()
  })

  it.each([
    ".login-mesh-bg",
    ".hero-title-line",
    ".hero-line",
    ".inject-breathe",
    ".streaming-cursor",
    ".nav-bar-loading",
    ".nav-bar-done",
    ".step-title-in",
    ".streaming-fade",
  ])("%s is addressed under prefers-reduced-motion", (selector) => {
    expect(reduceBlocks!.join("\n")).toContain(selector)
  })

  it("lands the hero in its finished state rather than only cancelling it", () => {
    // .hero-title-line starts at opacity:0 and depends on `forwards` to become
    // visible. Cancelling the animation alone would hide the text for good.
    const heroRule = reduceBlocks!
      .join("\n")
      .match(/\.hero-title-line[\s\S]*?\{([\s\S]*?)\}/)
    expect(heroRule).not.toBeNull()
    expect(heroRule![1]).toContain("opacity: 1")
  })
})
