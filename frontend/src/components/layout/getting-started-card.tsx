"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { Check, ChevronDown, ChevronUp, X, Bot, MessageSquare } from "lucide-react"
import { motion } from "motion/react"
import { cn } from "@/lib/utils"
import { useAuth } from "@/contexts/auth-context"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

const DISMISSED_KEY = "getting-started-dismissed"
const EXPANDED_KEY  = "getting-started-expanded"

// ── Confetti ──────────────────────────────────────────────────────────────────

const CONFETTI_COLORS = [
  "#f59e0b", "#fbbf24",               // amber
  "#6366f1", "#8b5cf6", "#a78bfa",    // purple / indigo
  "#ec4899", "#f472b6",               // pink
  "#10b981", "#34d399",               // green
  "#3b82f6", "#60a5fa",               // blue
  "#f97316", "#fb923c",               // orange
  "#ef4444",                          // red
]

interface ConfettiPiece {
  id: string
  // CSS anchor position (%)
  sx: number; sy: number
  // Framer Motion keyframe offsets (px) — [start, mid (burst), end (fall)]
  mx: number; my: number   // burst apex
  ex: number; ey: number   // final resting position (below card)
  rotate: number           // total rotation
  color: string
  w: number; h: number
  delay: number
  duration: number
  borderRadius: number     // 0 = rectangle, 50% = dot
}

// Two confetti cannons — all pieces originate from these tight points
const CANNONS = [
  { x: 28, y: 48 },   // left
  { x: 72, y: 48 },   // right
]

function buildConfetti(count = 60): ConfettiPiece[] {
  return Array.from({ length: count }, (_, i) => {
    const cannon = CANNONS[i % CANNONS.length]

    // Originate tightly around cannon mouth (±4px jitter only)
    const sx = cannon.x + (Math.random() - 0.5) * 4
    const sy = cannon.y + (Math.random() - 0.5) * 4

    // Burst: wide fan biased upward — pieces spray up & outward fast
    const angle     = (-Math.PI / 2) + (Math.random() - 0.5) * Math.PI * 1.7
    const burstDist = 55 + Math.random() * 65   // aggressive burst distance
    const mx        = Math.cos(angle) * burstDist
    const my        = Math.sin(angle) * burstDist

    // Gravity fall after apex
    const fallDist  = 85 + Math.random() * 80
    const drift     = (Math.random() - 0.5) * 55
    const ex        = mx + drift
    const ey        = my + fallDist

    // Shape: thin strips dominate (most realistic confetti), mix in squares + dots
    const shape = Math.random()
    let w: number, h: number, borderRadius: number
    if (shape < 0.55) {
      w = 7 + Math.random() * 5; h = 3 + Math.random() * 2; borderRadius = 1
    } else if (shape < 0.85) {
      const s = 4 + Math.random() * 4; w = s; h = s; borderRadius = 1
    } else {
      const s = 4 + Math.random() * 3; w = s; h = s; borderRadius = 50
    }

    const dir = Math.random() > 0.5 ? 1 : -1
    return {
      id:           String(i),
      sx, sy, mx, my, ex, ey,
      rotate:       dir * (240 + Math.random() * 420),
      color:        CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)],
      w, h, borderRadius,
      delay:        Math.random() * 0.18,   // tight delay — all fire nearly together
      duration:     0.9 + Math.random() * 0.6,
    }
  })
}

function Confetti({ active }: { active: boolean }) {
  // Build once when first activated (one-way latch — useMemo won't rebuild)
  const pieces = useMemo(
    () => (active ? buildConfetti() : []),
    [active],
  )

  if (!active) return null

  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      {pieces.map((p) => (
        <motion.span
          key={p.id}
          style={{
            position:     "absolute",
            left:         `${p.sx}%`,
            top:          `${p.sy}%`,
            width:        p.w,
            height:       p.h,
            marginLeft:   -p.w / 2,
            marginTop:    -p.h / 2,
            background:   p.color,
            borderRadius: p.borderRadius,
            transformOrigin: "center center",
          }}
          initial={{ x: 0, y: 0, opacity: 1, rotate: 0, scale: 1 }}
          animate={{
            x:       [0, p.mx, p.ex],
            y:       [0, p.my, p.ey],
            rotate:  [0, p.rotate * 0.5, p.rotate],
            opacity: [1, 1, 0],
            scale:   [1, 1, 0.7],
          }}
          transition={{
            duration: p.duration,
            delay:    p.delay,
            times:    [0, 0.22, 1],
            ease:     ["easeOut", "easeIn"],
          }}
        />
      ))}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function GettingStartedCard({ collapsed }: { collapsed: boolean }) {
  const t      = useTranslations("layout")
  const router = useRouter()
  const { user, meLoaded } = useAuth()

  const [dismissed, setDismissed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false
    return localStorage.getItem(DISMISSED_KEY) === "true"
  })

  const [expanded, setExpanded] = useState<boolean>(() => {
    if (typeof window === "undefined") return true
    const stored = localStorage.getItem(EXPANDED_KEY)
    return stored === null ? true : stored === "true"
  })

  const [allDoneShown, setAllDoneShown] = useState(false)

  const steps = [
    { key: "gsAgent",     icon: Bot,           href: "/agents",     done: !!user?.has_agent },
    { key: "gsChat",      icon: MessageSquare, href: "/new",        done: !!user?.has_conversation },
  ]

  const doneCount = steps.filter((s) => s.done).length
  const allDone   = doneCount === steps.length

  // Trigger confetti then auto-dismiss — MUST be before any early return
  useEffect(() => {
    if (!dismissed && allDone && !allDoneShown) {
      setAllDoneShown(true)
      const timer = setTimeout(() => {
        localStorage.setItem(DISMISSED_KEY, "true")
        setDismissed(true)
      }, 3200)
      return () => clearTimeout(timer)
    }
  }, [dismissed, allDone, allDoneShown])

  // Wait for fresh server data — prevents false 0/3 flash on login/incognito
  // allDone stays renderable so confetti can play; dismissed handles final hide
  if (!user || !meLoaded || dismissed) return null

  const handleToggleExpanded = () => {
    setExpanded((v) => {
      localStorage.setItem(EXPANDED_KEY, String(!v))
      return !v
    })
  }

  const handleDismiss = () => {
    localStorage.setItem(DISMISSED_KEY, "true")
    setDismissed(true)
  }

  // ── Collapsed: mini ring ───────────────────────────────────────────────────
  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            onClick={() => router.push(steps.find((s) => !s.done)?.href ?? "/")}
            className="relative flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
          >
            <svg className="h-5 w-5" viewBox="0 0 20 20">
              <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" strokeWidth="2" className="opacity-20" />
              <circle
                cx="10" cy="10" r="8" fill="none"
                stroke="currentColor" strokeWidth="2"
                strokeDasharray={`${(doneCount / steps.length) * 50.27} 50.27`}
                strokeLinecap="round"
                transform="rotate(-90 10 10)"
                style={{ stroke: "hsl(var(--primary))" }}
              />
            </svg>
            <span className="absolute text-[9px] font-semibold leading-none" style={{ color: "hsl(var(--primary))" }}>
              {doneCount}/{steps.length}
            </span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="right" sideOffset={8}>
          {t("gsTitle")} ({doneCount}/{steps.length})
        </TooltipContent>
      </Tooltip>
    )
  }

  // ── Expanded card ──────────────────────────────────────────────────────────
  return (
    <div className={cn(
      "relative rounded-lg border border-border bg-card/50 overflow-hidden transition-all duration-500",
      allDone && "border-primary/40 bg-primary/5",
    )}>
      {/* Confetti — clipped by overflow:hidden */}
      <Confetti active={allDoneShown} />

      {/* Header */}
      <div className="relative flex items-center gap-2 px-3 py-2">
        <div className="flex-1 min-w-0">
          <p className={cn("text-xs font-medium truncate", allDone ? "text-primary" : "text-foreground")}>
            {allDone ? t("gsAllDone") : t("gsTitle")}
          </p>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {doneCount}/{steps.length} {t("gsProgress")}
          </p>
        </div>
        <button
          onClick={handleToggleExpanded}
          className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground transition-colors"
        >
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
        </button>
        <button
          onClick={handleDismiss}
          className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground transition-colors"
          aria-label={t("dismiss")}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Progress bar */}
      <div className="relative h-0.5 bg-muted mx-3">
        <div
          className="h-full bg-primary rounded-full transition-all duration-500"
          style={{ width: `${(doneCount / steps.length) * 100}%` }}
        />
      </div>

      {/* Steps */}
      {expanded && (
        <div className="relative px-3 py-2 space-y-1">
          {steps.map((step, i) => {
            const Icon   = step.icon
            const isNext = !step.done && steps.slice(0, i).every((s) => s.done)
            return (
              <button
                key={step.key}
                onClick={() => !step.done && router.push(step.href)}
                disabled={step.done}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                  step.done
                    ? "text-muted-foreground cursor-default"
                    : isNext
                      ? "text-foreground hover:bg-accent/60 font-medium"
                      : "text-muted-foreground hover:bg-accent/40",
                )}
              >
                <span className={cn(
                  "flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded-full border transition-all",
                  step.done
                    ? "border-primary bg-primary text-primary-foreground"
                    : isNext
                      ? "border-primary/60 bg-primary/10 animate-pulse"
                      : "border-border bg-background",
                )}>
                  {step.done
                    ? <Check className="h-2.5 w-2.5" />
                    : <Icon  className="h-2.5 w-2.5" />
                  }
                </span>
                <span className={cn("flex-1 truncate", step.done && "line-through opacity-50")}>{t(step.key)}</span>
                {isNext && (
                  <span className="shrink-0 text-[10px] text-primary font-medium">{t("gsStart")}</span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
