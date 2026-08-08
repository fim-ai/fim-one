"use client";

import { useCallback, useState } from "react";
import { motion, type HTMLMotionProps, type Variants } from "motion/react";
import {
  MOTION_DURATION,
  MOTION_EASE_OUT,
  MOTION_RISE,
  MOTION_STAGGER,
} from "@/lib/motion";

/**
 * Entrance primitives. Timing comes from `@/lib/motion` so these stay in step
 * with the CSS keyframes in globals.css.
 *
 * Reduced motion is handled once, globally, by the `<MotionConfig
 * reducedMotion="user">` wrapper in `components/layout/app-shell.tsx`: it
 * drops the transform half of every animation below and keeps the opacity
 * fade, so nothing here needs its own media-query branch.
 */

type MotionTag = keyof typeof motion;

interface FadeInProps extends HTMLMotionProps<"div"> {
  delay?: number;
  duration?: number;
  y?: number;
  as?: MotionTag;
}

function FadeIn({
  children,
  delay = 0,
  duration = MOTION_DURATION.slow,
  y = MOTION_RISE,
  as = "div",
  className,
  ...props
}: FadeInProps) {
  const Comp = motion[as] as typeof motion.div;
  return (
    <Comp
      initial={{ opacity: 0, y }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration, delay, ease: MOTION_EASE_OUT }}
      className={className}
      {...props}
    >
      {children}
    </Comp>
  );
}

const staggerContainerVariants = (staggerDelay: number): Variants => ({
  hidden: {},
  visible: { transition: { staggerChildren: staggerDelay } },
});

const staggerItemVariants: Variants = {
  hidden: { opacity: 0, y: MOTION_RISE },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: MOTION_DURATION.base, ease: MOTION_EASE_OUT },
  },
};

interface StaggerOnceProps extends HTMLMotionProps<"div"> {
  staggerDelay?: number;
}

/**
 * Staggers its children into place on first mount, then stops animating.
 *
 * The distinction matters for list pages. The container outlives its children:
 * filtering a search box or turning a page unmounts every card and mounts a
 * new set. A plain always-on stagger container would replay the entrance for
 * each of those, so a list would flicker on every keystroke. Once the opening
 * run finishes, `initial={false}` makes later children mount straight into
 * their resting state.
 */
function StaggerOnce({
  children,
  staggerDelay = MOTION_STAGGER,
  className,
  ...props
}: StaggerOnceProps) {
  const [settled, setSettled] = useState(false);

  // Fires after the container's children have finished their variant run,
  // because variant completion propagates up from the subtree.
  const handleComplete = useCallback(() => setSettled(true), []);

  return (
    <motion.div
      variants={staggerContainerVariants(staggerDelay)}
      initial={settled ? false : "hidden"}
      animate="visible"
      onAnimationComplete={handleComplete}
      className={className}
      {...props}
    >
      {children}
    </motion.div>
  );
}

/**
 * One staggered child. Must be a direct child of `StaggerOnce`.
 *
 * Inside a card grid, pass `className="grid"`. The wrapper takes over as the
 * grid item, and grid items stretch to the tallest cell in their row, so
 * without it the card would size to its own content and same-row cards would
 * no longer match heights. `display: grid` on the wrapper hands that stretch
 * down to the single card inside.
 */
function StaggerItem({
  children,
  className,
  ...props
}: HTMLMotionProps<"div">) {
  return (
    <motion.div variants={staggerItemVariants} className={className} {...props}>
      {children}
    </motion.div>
  );
}

export { FadeIn, StaggerOnce, StaggerItem };
