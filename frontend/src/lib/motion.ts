/**
 * Shared motion scale.
 *
 * These are the single source of truth for animation timing across the portal.
 * The same values are mirrored as `--motion-duration-*` / `--motion-ease-*`
 * custom properties in `globals.css`, so CSS keyframes and Motion (framer)
 * components stay in step. Change a value here and in globals.css together.
 *
 * Durations are in seconds because that is what Motion's `transition` expects;
 * the CSS side carries the millisecond equivalents.
 */

/** Cubic-bezier control points, the shape Motion accepts for a custom curve. */
type Bezier = [number, number, number, number]

export const MOTION_DURATION = {
  /** Hover states, opacity swaps, anything that must feel immediate. */
  fast: 0.15,
  /** Default for entrances: list items, cards, panel content. */
  base: 0.24,
  /** Larger surfaces travelling further: dialogs, page-level content. */
  slow: 0.4,
} as const

/**
 * Decelerating curve for elements arriving on screen. Starts fast and settles
 * softly, which reads as responsive rather than floaty.
 */
export const MOTION_EASE_OUT: Bezier = [0.16, 1, 0.3, 1]

/** Symmetric curve for elements that move without entering or leaving. */
export const MOTION_EASE_IN_OUT: Bezier = [0.4, 0, 0.2, 1]

/** Gap between consecutive children in a staggered list entrance. */
export const MOTION_STAGGER = 0.04

/** Distance (px) an entering element travels upward into place. */
export const MOTION_RISE = 8
