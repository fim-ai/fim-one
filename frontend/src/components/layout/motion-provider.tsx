"use client"

import { MotionConfig } from "motion/react"

/**
 * Makes every Motion component in the tree honour the OS "reduce motion"
 * setting: transform and layout animations are dropped while opacity fades are
 * kept, so a reduced-motion user still sees content arrive, just without the
 * travel.
 *
 * This is the JS-side counterpart to the `@media (prefers-reduced-motion)`
 * block at the end of globals.css. That block only reaches CSS keyframes;
 * Motion drives its animations from JavaScript and would otherwise ignore the
 * setting entirely. Mounted above AppShell so it also covers the routes that
 * render outside the authenticated layout, such as /login and /onboarding.
 */
export function MotionProvider({ children }: { children: React.ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>
}
