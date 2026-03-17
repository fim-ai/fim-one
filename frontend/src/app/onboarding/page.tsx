"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { motion } from "motion/react"
import confetti from "canvas-confetti"
import { useAuth } from "@/contexts/auth-context"
import { authApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Loader2, Check, ArrowRight } from "lucide-react"
import {
  Code,
  Layout,
  BarChart3,
  Palette,
  Server,
  BookOpen,
  Megaphone,
  Briefcase,
  GraduationCap,
  Sparkles,
  Smartphone,
  FileText,
  Workflow,
  Search,
  Users,
  Plug,
  Wrench,
  Zap,
  MessageSquare,
  ListChecks,
  Swords,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { usePageTitle } from "@/hooks/use-page-title"

const TOTAL_STEPS = 5

// --- Role options ---
const ROLE_OPTIONS = [
  { key: "roleSoftwareEngineer", icon: Code, value: "Software Engineer" },
  { key: "roleProductManager", icon: Layout, value: "Product Manager" },
  { key: "roleDataAnalyst", icon: BarChart3, value: "Data Analyst" },
  { key: "roleDesigner", icon: Palette, value: "Designer" },
  { key: "roleDevOps", icon: Server, value: "DevOps / SRE" },
  { key: "roleResearcher", icon: BookOpen, value: "Researcher" },
  { key: "roleMarketing", icon: Megaphone, value: "Marketing / Sales" },
  { key: "roleExecutive", icon: Briefcase, value: "Executive" },
  { key: "roleStudent", icon: GraduationCap, value: "Student" },
  { key: "roleOther", icon: Sparkles, value: "other" },
] as const

// --- Project options ---
const PROJECT_OPTIONS = [
  { key: "projectWebApp", icon: Smartphone, value: "Web/Mobile App" },
  { key: "projectDataAnalysis", icon: BarChart3, value: "Data Analysis" },
  { key: "projectWriting", icon: FileText, value: "Writing & Content" },
  { key: "projectAutomation", icon: Workflow, value: "Automating Workflows" },
  { key: "projectResearch", icon: Search, value: "Research & Learning" },
  { key: "projectManagement", icon: Users, value: "Team/Project Management" },
  { key: "projectAPI", icon: Plug, value: "API Integrations" },
  { key: "projectInternalTools", icon: Wrench, value: "Internal Tools" },
  { key: "projectOther", icon: Sparkles, value: "Other" },
] as const

// --- AI style options ---
const STYLE_OPTIONS = [
  { key: "styleConcise", descKey: "styleConciseDesc", icon: Zap, value: "Keep responses short and action-oriented." },
  { key: "styleDetailed", descKey: "styleDetailedDesc", icon: MessageSquare, value: "Provide thorough explanations with context." },
  { key: "styleStepByStep", descKey: "styleStepByStepDesc", icon: ListChecks, value: "Break down problems step by step before answering." },
  { key: "styleChallenge", descKey: "styleChallengeDesc", icon: Swords, value: "Play devil's advocate and question assumptions." },
] as const

// ── Celebration Confetti (canvas-confetti) ────────────────────────────────────
// Fires a rich multi-burst sequence: two side cannons + a center star burst,
// using real physics simulation via canvas-confetti.

const CONFETTI_COLORS = [
  "#f59e0b", "#fbbf24",   // amber
  "#6366f1", "#8b5cf6",   // purple / indigo
  "#ec4899", "#f472b6",   // pink
  "#10b981", "#34d399",   // green
  "#3b82f6", "#60a5fa",   // blue
  "#f97316", "#fb923c",   // orange
  "#ef4444",              // red
]

function fireCelebrationConfetti() {
  const defaults = {
    colors: CONFETTI_COLORS,
    disableForReducedMotion: true,
    zIndex: 9999,
  }

  // Wave 1: two side cannons (left + right), angled inward
  confetti({ ...defaults, particleCount: 50, spread: 65, angle: 60,  origin: { x: 0, y: 0.65 }, startVelocity: 50 })
  confetti({ ...defaults, particleCount: 50, spread: 65, angle: 120, origin: { x: 1, y: 0.65 }, startVelocity: 50 })

  // Wave 2 (200ms later): center burst upward
  setTimeout(() => {
    confetti({ ...defaults, particleCount: 60, spread: 90, origin: { x: 0.5, y: 0.7 }, startVelocity: 45 })
  }, 200)

  // Wave 3 (800ms): gentle drifting pieces from center
  setTimeout(() => {
    confetti({ ...defaults, particleCount: 30, spread: 140, origin: { x: 0.5, y: 0.5 }, startVelocity: 25, gravity: 0.6, scalar: 1.2, ticks: 300 })
  }, 800)
}

export default function OnboardingPage() {
  const t = useTranslations("onboarding")
  const tError = useTranslations("errors")
  const router = useRouter()
  const { user, isLoading: authLoading, updateUser } = useAuth()

  usePageTitle(t("welcomeTitle"))

  const [step, setStep] = useState(1)
  const [direction, setDirection] = useState<"forward" | "backward">("forward")

  // Step 1: display name
  const [displayName, setDisplayName] = useState("")

  // Step 2: role
  const [selectedRole, setSelectedRole] = useState("")
  const [customRole, setCustomRole] = useState("")

  // Step 3: projects (multi-select, max 3)
  const [selectedProjects, setSelectedProjects] = useState<string[]>([])

  // Step 4: AI style
  const [selectedStyle, setSelectedStyle] = useState("")

  // Submission
  const [submitting, setSubmitting] = useState(false)

  // Step 5: celebration
  const [countdown, setCountdown] = useState(5)
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Protection: redirect if not logged in or already completed (but not during celebration)
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    } else if (!authLoading && user?.onboarding_completed && step !== 5) {
      router.replace("/")
    }
  }, [authLoading, user, router, step])

  const goNext = useCallback(() => {
    if (step < TOTAL_STEPS) {
      setDirection("forward")
      setStep((s) => s + 1)
    }
  }, [step])

  const goBack = useCallback(() => {
    if (step > 1) {
      setDirection("backward")
      setStep((s) => s - 1)
    }
  }, [step])

  const handleSkip = useCallback(async () => {
    setSubmitting(true)
    try {
      const updated = await authApi.updateProfile({ onboarding_completed: true })
      updateUser(updated)
      router.replace("/")
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setSubmitting(false)
    }
  }, [updateUser, router, tError])

  const handleComplete = useCallback(async () => {
    setSubmitting(true)
    try {
      // Build system instructions from selections
      const parts: string[] = []
      const role = selectedRole === "other" ? customRole.trim() : selectedRole
      if (role) {
        parts.push(`I am a ${role}`)
      }
      if (selectedProjects.length > 0) {
        parts.push(`working on ${selectedProjects.join(", ")}`)
      }
      const systemParts = parts.length > 0 ? parts.join(" ") + "." : ""
      const styleStr = selectedStyle ? `\nI prefer: ${selectedStyle}` : ""
      const systemInstructions = (systemParts + styleStr).trim() || undefined

      const updated = await authApi.updateProfile({
        display_name: displayName.trim() || undefined,
        system_instructions: systemInstructions ?? undefined,
        onboarding_completed: true,
      })
      updateUser(updated)

      // Advance to celebration step and fire confetti
      setDirection("forward")
      setStep(5)
      fireCelebrationConfetti()
    } catch (err) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setSubmitting(false)
    }
  }, [displayName, selectedRole, customRole, selectedProjects, selectedStyle, updateUser, tError])

  // Countdown timer for step 5 auto-redirect
  // Keep countdown in a ref so the interval callback always sees the latest value
  // without needing router.replace inside a setState updater (which triggers
  // "Cannot update Router while rendering" in React 18 strict mode).
  const countdownValueRef = useRef(5)

  useEffect(() => {
    if (step !== 5) return

    countdownValueRef.current = 5
    setCountdown(5)

    countdownRef.current = setInterval(() => {
      const next = countdownValueRef.current - 1
      countdownValueRef.current = next
      setCountdown(next)

      if (next <= 0) {
        if (countdownRef.current) clearInterval(countdownRef.current)
        router.replace("/")
      }
    }, 1000)

    return () => {
      if (countdownRef.current) clearInterval(countdownRef.current)
    }
  }, [step, router])

  const toggleProject = (value: string) => {
    setSelectedProjects((prev) => {
      if (prev.includes(value)) {
        return prev.filter((p) => p !== value)
      }
      if (prev.length >= 3) return prev
      return [...prev, value]
    })
  }

  // Show loading while checking auth
  if (authLoading || !user) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const isCelebrationStep = step === 5
  const isSubmitStep = step === 4

  // If already completed and not on celebration step, don't render (will redirect)
  if (user.onboarding_completed && !isCelebrationStep) return null

  const canContinue = (() => {
    switch (step) {
      case 1: return displayName.trim().length > 0
      case 2: return selectedRole !== "" && (selectedRole !== "other" || customRole.trim().length > 0)
      case 3: return selectedProjects.length > 0
      case 4: return selectedStyle !== ""
      default: return true
    }
  })()

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-[560px]">
        {/* Progress dots — hidden on celebration step, only show 4 dots for steps 1-4 */}
        {!isCelebrationStep && (
          <div className="mb-10 flex items-center justify-center gap-2">
            {Array.from({ length: 4 }, (_, i) => (
              <div
                key={i}
                className={cn(
                  "h-2 rounded-full transition-all duration-300",
                  i + 1 === step
                    ? "w-8 bg-primary"
                    : i + 1 < step
                      ? "w-2 bg-primary/50"
                      : "w-2 bg-muted-foreground/20",
                )}
              />
            ))}
          </div>
        )}

        {/* Step content with transition */}
        <div
          key={step}
          className="animate-in fade-in slide-in-from-right-4 duration-300"
          style={{
            animationDirection: direction === "backward" ? "reverse" : "normal",
          }}
        >
          {/* Step 1: Display Name */}
          {step === 1 && (
            <div className="space-y-6">
              <div className="text-center">
                <h1 className="text-2xl font-semibold tracking-tight">
                  {t("step1Title")}
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">
                  {t("step1Subtitle")}
                </p>
              </div>
              <div className="mx-auto max-w-xs">
                <Input
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder={t("displayNamePlaceholder")}
                  autoFocus
                  className="text-center text-lg h-12"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && displayName.trim().length > 0) goNext()
                  }}
                />
              </div>
            </div>
          )}

          {/* Step 2: Role */}
          {step === 2 && (
            <div className="space-y-6">
              <div className="text-center">
                <h1 className="text-2xl font-semibold tracking-tight">
                  {t("step2Title")}
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">
                  {t("step2Subtitle")}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {ROLE_OPTIONS.map((opt) => {
                  const Icon = opt.icon
                  const isSelected = selectedRole === opt.value
                  return (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => setSelectedRole(isSelected ? "" : opt.value)}
                      className={cn(
                        "relative flex flex-col items-center gap-2 rounded-xl border px-3 py-4 text-sm transition-all",
                        "hover:border-primary/50 hover:bg-primary/5",
                        "focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2",
                        isSelected
                          ? "border-primary bg-primary/5 text-primary"
                          : "border-border text-muted-foreground",
                      )}
                    >
                      {isSelected && (
                        <div className="absolute top-2 right-2">
                          <Check className="h-3.5 w-3.5 text-primary" />
                        </div>
                      )}
                      <Icon className="h-5 w-5" />
                      <span className="text-center leading-tight">{t(opt.key)}</span>
                    </button>
                  )
                })}
              </div>
              {selectedRole === "other" && (
                <div className="mx-auto max-w-xs">
                  <Input
                    value={customRole}
                    onChange={(e) => setCustomRole(e.target.value)}
                    placeholder={t("roleOtherPlaceholder")}
                    autoFocus
                    className="text-center"
                  />
                </div>
              )}
            </div>
          )}

          {/* Step 3: Projects */}
          {step === 3 && (
            <div className="space-y-6">
              <div className="text-center">
                <h1 className="text-2xl font-semibold tracking-tight">
                  {t("step3Title")}
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">
                  {t("step3Subtitle")}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {PROJECT_OPTIONS.map((opt) => {
                  const Icon = opt.icon
                  const isSelected = selectedProjects.includes(opt.value)
                  const isDisabled = !isSelected && selectedProjects.length >= 3
                  return (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => toggleProject(opt.value)}
                      disabled={isDisabled}
                      className={cn(
                        "relative flex flex-col items-center gap-2 rounded-xl border px-3 py-4 text-sm transition-all",
                        "hover:border-primary/50 hover:bg-primary/5",
                        "focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2",
                        isSelected
                          ? "border-primary bg-primary/5 text-primary"
                          : "border-border text-muted-foreground",
                        isDisabled && "opacity-40 cursor-not-allowed hover:border-border hover:bg-transparent",
                      )}
                    >
                      {isSelected && (
                        <div className="absolute top-2 right-2">
                          <Check className="h-3.5 w-3.5 text-primary" />
                        </div>
                      )}
                      <Icon className="h-5 w-5" />
                      <span className="text-center leading-tight">{t(opt.key)}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {/* Step 4: AI Style */}
          {step === 4 && (
            <div className="space-y-6">
              <div className="text-center">
                <h1 className="text-2xl font-semibold tracking-tight">
                  {t("step4Title")}
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">
                  {t("step4Subtitle")}
                </p>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {STYLE_OPTIONS.map((opt) => {
                  const Icon = opt.icon
                  const isSelected = selectedStyle === opt.value
                  return (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => setSelectedStyle(isSelected ? "" : opt.value)}
                      className={cn(
                        "relative flex items-start gap-3 rounded-xl border px-4 py-4 text-left transition-all",
                        "hover:border-primary/50 hover:bg-primary/5",
                        "focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2",
                        isSelected
                          ? "border-primary bg-primary/5"
                          : "border-border",
                      )}
                    >
                      {isSelected && (
                        <div className="absolute top-3 right-3">
                          <Check className="h-3.5 w-3.5 text-primary" />
                        </div>
                      )}
                      <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", isSelected ? "text-primary" : "text-muted-foreground")} />
                      <div>
                        <p className={cn("text-sm font-medium", isSelected ? "text-primary" : "text-foreground")}>
                          {t(opt.key)}
                        </p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {t(opt.descKey)}
                        </p>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {/* Step 5: Celebration */}
          {step === 5 && (
            <div className="flex flex-col items-center justify-center text-center space-y-8">
              {/* Animated checkmark circle */}
              <motion.div
                initial={{ scale: 0, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ type: "spring", stiffness: 200, damping: 15, delay: 0.1 }}
                className="flex h-20 w-20 items-center justify-center rounded-full bg-primary/10"
              >
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: "spring", stiffness: 250, damping: 12, delay: 0.3 }}
                >
                  <Check className="h-10 w-10 text-primary" strokeWidth={2.5} />
                </motion.div>
              </motion.div>

              {/* Title and subtitle */}
              <motion.div
                initial={{ y: 16, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ duration: 0.5, delay: 0.4 }}
                className="space-y-3"
              >
                <h1 className="text-3xl font-bold tracking-tight">
                  {t("completionTitle")}
                </h1>
                <p className="text-base text-muted-foreground">
                  {t("completionSubtitle", { name: displayName.trim() || user?.display_name || "" })}
                </p>
              </motion.div>

              {/* Go to Dashboard button with glow */}
              <motion.div
                initial={{ y: 16, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ duration: 0.5, delay: 0.6 }}
                className="flex flex-col items-center gap-4"
              >
                <Button
                  size="lg"
                  onClick={() => {
                    if (countdownRef.current) clearInterval(countdownRef.current)
                    router.replace("/")
                  }}
                  className="min-w-[200px] text-base font-medium shadow-[0_0_20px_hsl(var(--primary)/0.3)] hover:shadow-[0_0_28px_hsl(var(--primary)/0.45)] transition-shadow duration-300 focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2"
                >
                  {t("goToDashboard")}
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
                <p className="text-sm text-muted-foreground">
                  {t("redirectingIn", { seconds: countdown })}
                </p>
              </motion.div>
            </div>
          )}

        </div>

        {/* Bottom actions — hidden on celebration step */}
        {!isCelebrationStep && (
          <div className="mt-10 flex items-center justify-between">
            <button
              type="button"
              onClick={handleSkip}
              disabled={submitting}
              className="text-sm text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
            >
              {t("skip")}
            </button>
            <div className="flex items-center gap-6">
              {step > 1 && (
                <button
                  type="button"
                  onClick={goBack}
                  disabled={submitting}
                  className="text-sm text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
                >
                  {t("back")}
                </button>
              )}
              <Button
                onClick={isSubmitStep ? handleComplete : goNext}
                disabled={!canContinue || submitting}
                className="min-w-[120px]"
              >
                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {submitting ? t("settingUp") : isSubmitStep ? t("getStarted") : t("continue")}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
