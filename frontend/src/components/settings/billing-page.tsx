"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useLocale, useTranslations } from "next-intl"
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  Loader2,
  Sparkles,
} from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ApiError, apiFetch } from "@/lib/api"
import { useAuth } from "@/contexts/auth-context"
import { cn } from "@/lib/utils"
import { formatTokens } from "@/lib/format-tokens"
import { fireCelebrationConfetti } from "@/lib/celebration-confetti"

// ---------------------------------------------------------------------------
// Backend response shapes (mirror src/fim_one/web/schemas/billing.py)
// ---------------------------------------------------------------------------

interface PlanInfo {
  slug: string
  name: string
  monthly_token_quota: number
  stripe_price_id: string | null
  /** Pre-formatted by the backend, e.g. "$20.00 USD/month" or "Free". */
  price_display: string
  description: string | null
  features: string[]
  sort_order: number
  current: boolean
}

interface PlansResponse {
  plans: PlanInfo[]
}

interface SubscriptionInfo {
  plan_slug: string
  status: string
  cancel_at_period_end: boolean
  current_period_start: string
  current_period_end: string
  canceled_at: string | null
  stripe_subscription_id: string
}

interface UsageData {
  total_tokens: number
  // Usage over the billing-aligned quota window — what chat enforces
  // against. Prefer this over total_tokens for the quota display.
  window_tokens: number
  reset_at: string | null
  quota: number | null
  quota_used_pct: number | null
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format an ISO date string as a localized "Mon DD, YYYY" — gracefully
 *  falls back to the raw input when parsing fails. */
function formatLongDate(iso: string | null | undefined, locale: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

/** Map a subscription status string to a translated label + Badge variant. */
function statusBadgeProps(
  status: string,
  t: (k: string) => string,
): { label: string; variant: "default" | "secondary" | "destructive" | "outline" } {
  switch (status) {
    case "active":
    case "trialing":
      return { label: t(`status.${status}`), variant: "default" }
    case "past_due":
      return { label: t("status.pastDue"), variant: "destructive" }
    case "unpaid":
      return { label: t("status.unpaid"), variant: "destructive" }
    case "canceled":
      return { label: t("status.canceled"), variant: "secondary" }
    case "incomplete":
    case "incomplete_expired":
      return { label: t("status.incomplete"), variant: "outline" }
    default:
      return { label: t("status.unknown"), variant: "outline" }
  }
}

// ---------------------------------------------------------------------------
// BillingPage
// ---------------------------------------------------------------------------

/**
 * User-facing billing settings page.
 *
 * Architecture
 * - Loads `/api/billing/plans` + `/api/billing/subscription` in parallel.
 *   Each request is independent so a failed subscription fetch (e.g. 404
 *   on the free tier) does not block the plan list from rendering.
 * - Detects 503 once on either request and switches the whole page into
 *   the "billing not configured" state — this is what an operator sees
 *   when running self-hosted without `STRIPE_SECRET_KEY`.
 * - Does not import any usage state from `useAuth`; usage comes from
 *   `/api/me/usage?period=month` so the progress bar reflects the
 *   actual rolling counter the chat layer enforces against.
 *
 * Action flow
 * - "Upgrade / Switch" → POST `/api/billing/checkout` → `window.location`
 *   redirect to a Stripe-hosted Checkout Session.
 * - "Manage subscription" → POST `/api/billing/portal` → redirect to
 *   the Stripe Billing Portal.
 *
 * Both POSTs short-circuit on 503/400 with a toast — the user is never
 * left on a spinner.
 */
export function BillingPage() {
  const t = useTranslations("billing")
  const locale = useLocale()
  const router = useRouter()
  const { user, isLoading: authLoading } = useAuth()
  const searchParams = useSearchParams()

  const [plans, setPlans] = useState<PlanInfo[] | null>(null)
  const [subscription, setSubscription] = useState<SubscriptionInfo | null>(null)
  const [usage, setUsage] = useState<UsageData | null>(null)
  const [loading, setLoading] = useState(true)
  const [billingDisabled, setBillingDisabled] = useState(false)
  const [actionPending, setActionPending] = useState<string | null>(null)
  const [celebrationOpen, setCelebrationOpen] = useState(false)

  // ----- Checkout success celebration --------------------------------------
  // Stripe redirects back to `/settings?tab=billing&status=success&plan={slug}`
  // after a successful checkout. We open a dismissible Dialog (with confetti)
  // exactly once per page load — the URL is left untouched so the user can
  // refresh / share it during testing.
  const celebrationFiredRef = useRef(false)
  const purchasedPlanSlug = searchParams.get("plan")
  useEffect(() => {
    if (celebrationFiredRef.current) return
    if (searchParams.get("status") !== "success") return
    celebrationFiredRef.current = true
    setCelebrationOpen(true)
    // Fire-and-forget — no cleanup. In React Strict Mode (dev), an
    // effect cleanup would run between the simulated unmount/remount
    // and cancel the only scheduled confetti before it ever fired,
    // leaving the user with a silent dialog. The ref above already
    // dedupes the second strict-mode run, so a stray timer is impossible.
    setTimeout(() => fireCelebrationConfetti(), 150)
  }, [searchParams])

  // ----- Auth guard (mirrors the rest of /settings/*) ----------------------

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  // ----- Data loading -------------------------------------------------------

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      // Run all three requests in parallel and tolerate per-request failures —
      // a 404 on /subscription (free tier with no Stripe row) shouldn't kill
      // the plans list, and usage should render even without billing.
      const [plansRes, subRes, usageRes] = await Promise.allSettled([
        apiFetch<PlansResponse>("/api/billing/plans"),
        apiFetch<SubscriptionInfo | null>("/api/billing/subscription"),
        apiFetch<UsageData>("/api/me/usage?period=month"),
      ])
      if (cancelled) return

      // Detect "billing not configured" once. Any 503 from /plans or
      // /subscription means Stripe credentials are missing — the user
      // is on the free tier and there's nothing to render.
      const stripeUnavailable = [plansRes, subRes].some(
        (r) => r.status === "rejected" && r.reason instanceof ApiError && r.reason.status === 503,
      )
      setBillingDisabled(stripeUnavailable)

      if (plansRes.status === "fulfilled") {
        setPlans(plansRes.value.plans)
      } else if (!stripeUnavailable) {
        // Surface unexpected errors via toast — but only when billing IS
        // configured. When disabled, the dedicated banner is the right UI.
        toast.error(t("plans.loadFailed"))
      }

      if (subRes.status === "fulfilled") {
        setSubscription(subRes.value)
      } else if (!stripeUnavailable && subRes.reason instanceof ApiError) {
        // 404 == free tier with no Stripe row; treat as "no subscription".
        if (subRes.reason.status !== 404) {
          toast.error(t("subscription.loadFailed"))
        }
      }

      if (usageRes.status === "fulfilled") {
        setUsage(usageRes.value)
      } else {
        toast.error(t("usage.loadFailed"))
      }

      setLoading(false)
    }
    if (!authLoading && user) {
      load()
    }
    return () => {
      cancelled = true
    }
  }, [authLoading, user, t])

  // ----- Derived state ------------------------------------------------------

  const currentPlan = useMemo<PlanInfo | null>(
    () => plans?.find((p) => p.current) ?? null,
    [plans],
  )

  const usagePercent = useMemo<number | null>(() => {
    if (!usage || usage.quota == null || usage.quota <= 0) return null
    // Use the server's window-based percentage (measured over the quota
    // window chat enforces against) rather than recomputing from
    // total_tokens, which reflects the calendar month and would disagree
    // with the actual cut-off point.
    if (usage.quota_used_pct != null) {
      return Math.min(100, Math.round(usage.quota_used_pct))
    }
    return Math.min(100, Math.round((usage.window_tokens / usage.quota) * 100))
  }, [usage])

  const showCanceledBanner =
    subscription?.cancel_at_period_end === true && subscription.status !== "canceled"
  const showPastDueBanner = subscription?.status === "past_due"

  // Plan name to greet with — best-effort: ?plan=<slug> populated by the
  // backend's success_url. Falls back to the freshly-loaded subscription
  // if the URL didn't carry it (older redirects).
  const purchasedPlanName = useMemo<string | null>(() => {
    const slug = purchasedPlanSlug ?? subscription?.plan_slug ?? null
    if (!slug || !plans) return null
    return plans.find((p) => p.slug === slug)?.name ?? null
  }, [purchasedPlanSlug, subscription, plans])

  // ----- Actions ------------------------------------------------------------

  /** Start a Stripe Checkout session for the given plan and redirect. */
  const handleCheckout = useCallback(
    async (plan: PlanInfo) => {
      // Guard against double-clicks on the same button.
      if (actionPending) return
      setActionPending(`checkout:${plan.slug}`)
      try {
        const { url } = await apiFetch<{ url: string }>("/api/billing/checkout", {
          method: "POST",
          body: JSON.stringify({ plan_slug: plan.slug }),
        })
        // Full-page redirect — Stripe Checkout owns the rest of the flow.
        window.location.href = url
      } catch (err) {
        if (err instanceof ApiError && err.status === 503) {
          setBillingDisabled(true)
          toast.error(t("error.notConfiguredTitle"))
        } else {
          toast.error(t("error.checkoutFailed"))
        }
        setActionPending(null)
      }
    },
    [actionPending, t],
  )

  /** Open the Stripe-hosted Billing Portal. */
  const handlePortal = useCallback(async () => {
    if (actionPending) return
    setActionPending("portal")
    try {
      const { url } = await apiFetch<{ url: string }>("/api/billing/portal", {
        method: "POST",
        body: JSON.stringify({}),
      })
      window.location.href = url
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setBillingDisabled(true)
        toast.error(t("error.notConfiguredTitle"))
      } else if (err instanceof ApiError && err.status === 400) {
        // 400 here means "no Stripe customer" — the user has never
        // subscribed. Clearer than a generic failure message.
        toast.error(t("error.noStripeCustomer"))
      } else {
        toast.error(t("error.portalFailed"))
      }
      setActionPending(null)
    }
  }, [actionPending, t])

  // ----- Loading skeleton ---------------------------------------------------

  if (authLoading || !user) return null

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-base font-semibold">{t("page.title")}</h2>
        <p className="text-sm text-muted-foreground">
          {t("page.description")}
        </p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : billingDisabled ? (
        <NotConfiguredCard />
      ) : (
        <>
          {/* Banners (past_due / pending cancellation) — always at the
              top so the user can react before scrolling. */}
          {showPastDueBanner && (
            <Banner
              tone="destructive"
              title={t("banner.pastDueTitle")}
              body={t("banner.pastDueBody")}
            />
          )}
          {showCanceledBanner && subscription && (
            <Banner
              tone="warning"
              title={t("banner.canceledTitle")}
              body={t("banner.canceledBody", {
                date: formatLongDate(subscription.current_period_end, locale),
              })}
            />
          )}

          {/* Top row: current plan + usage side-by-side on wide screens.
              Each card stays self-contained on mobile. The Usage card is
              deliberately a thin progress + reset summary; deep
              per-agent breakdowns live on the Usage tab to avoid
              duplicating the same data twice in the same page. */}
          <div className="grid gap-4 md:grid-cols-2">
            <CurrentPlanCard plan={currentPlan} locale={locale} />
            <UsageCard
              usage={usage}
              percent={usagePercent}
              subscription={subscription}
              locale={locale}
            />
          </div>

          <SubscriptionStatusCard
            subscription={subscription}
            locale={locale}
            onManage={handlePortal}
            portalPending={actionPending === "portal"}
          />

          <PlansComparison
            plans={plans}
            currentPlanSlug={currentPlan?.slug ?? null}
            onCheckout={handleCheckout}
            pendingSlug={
              actionPending?.startsWith("checkout:")
                ? actionPending.slice("checkout:".length)
                : null
            }
            locale={locale}
          />
        </>
      )}

      <CheckoutSuccessDialog
        open={celebrationOpen}
        onOpenChange={(next) => {
          setCelebrationOpen(next)
          // On dismiss, strip the one-shot redirect params so a refresh
          // doesn't re-trigger the celebration. We only touch our own
          // params and preserve everything else (notably ?tab=billing).
          if (!next && searchParams.get("status") === "success") {
            const cleaned = new URLSearchParams(searchParams.toString())
            cleaned.delete("status")
            cleaned.delete("plan")
            cleaned.delete("session_id")
            const qs = cleaned.toString()
            router.replace(qs ? `/settings?${qs}` : "/settings?tab=billing")
          }
        }}
        planName={purchasedPlanName}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Checkout-success celebration dialog — opens once after Stripe redirects
// the user back with `?status=success`. Confetti fires alongside the dialog
// (handled by the parent effect) so the celebration reads as one moment.

function CheckoutSuccessDialog({
  open,
  onOpenChange,
  planName,
}: {
  open: boolean
  onOpenChange: (next: boolean) => void
  planName: string | null
}) {
  const t = useTranslations("billing")
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
            <Sparkles className="h-6 w-6 text-primary" />
          </div>
          <DialogTitle className="text-center">
            {t("success.title")}
          </DialogTitle>
          <DialogDescription className="text-center">
            {planName
              ? t("success.bodyWithPlan", { plan: planName })
              : t("success.bodyGeneric")}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="sm:justify-center">
          <Button onClick={() => onOpenChange(false)} className="min-w-32">
            {t("success.cta")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function NotConfiguredCard() {
  const t = useTranslations("billing")
  return (
    <div className="rounded-md border border-border bg-muted/30 p-8 text-center">
      <AlertCircle className="mx-auto mb-2 h-8 w-8 text-muted-foreground/50" />
      <p className="text-sm font-medium">{t("error.notConfiguredTitle")}</p>
      <p className="mt-1 text-sm text-muted-foreground">
        {t("error.notConfiguredBody")}
      </p>
    </div>
  )
}

interface BannerProps {
  tone: "destructive" | "warning"
  title: string
  body: string
}

function Banner({ tone, title, body }: BannerProps) {
  // The two tones reuse the same shape but flip color tokens. Tailwind
  // can't generate `bg-${tone}` at runtime, so we hand-roll the classes.
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-md border p-4",
        tone === "destructive" &&
          "border-destructive/40 bg-destructive/10 text-destructive",
        tone === "warning" &&
          "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
      )}
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="space-y-0.5">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-sm opacity-90">{body}</p>
      </div>
    </div>
  )
}

function CurrentPlanCard({
  plan,
  locale,
}: {
  plan: PlanInfo | null
  locale: string
}) {
  const t = useTranslations("billing")
  return (
    <div className="rounded-md border border-border p-5 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">
          {t("currentPlan.title")}
        </p>
        {plan && (
          <Badge variant="default" className="font-medium">
            {t("currentPlan.currentBadge")}
          </Badge>
        )}
      </div>
      <div>
        <p className="text-3xl font-bold">
          {plan?.name ?? t("currentPlan.freeFallback")}
        </p>
        {plan?.price_display && (
          <p className="mt-1 text-xs text-muted-foreground">
            {plan.price_display}
          </p>
        )}
      </div>
      <div className="border-t border-border pt-3">
        <p className="text-xs font-medium text-muted-foreground">
          {t("currentPlan.monthlyQuota")}
        </p>
        <p className="mt-1 text-sm font-medium tabular-nums">
          {plan && plan.monthly_token_quota > 0
            ? formatTokens(plan.monthly_token_quota, locale)
            : t("currentPlan.unlimited")}
        </p>
      </div>
    </div>
  )
}

interface UsageCardProps {
  usage: UsageData | null
  percent: number | null
  subscription: SubscriptionInfo | null
  locale: string
}

function UsageCard({ usage, percent, subscription, locale }: UsageCardProps) {
  const t = useTranslations("billing")

  // The "usage this period" number resets on the quota window, which is
  // not always the billing period: an annually-billed plan refills its
  // monthly quota every month, not at the (yearly) billing boundary. So
  // when there is a cap, show the quota-window reset; for unlimited
  // users fall back to the subscription's billing boundary, then to the
  // generic free-tier line.
  const resetText =
    usage?.quota != null && usage.quota > 0 && usage.reset_at
      ? t("usage.resetOn", {
          date: formatLongDate(usage.reset_at, locale),
        })
      : subscription?.current_period_end
        ? t("usage.periodEndsOn", {
            date: formatLongDate(subscription.current_period_end, locale),
          })
        : t("usage.noPeriod")

  // Color the bar in three bands so the user can read severity without
  // squinting at the digits: green < 75%, amber < 90%, red beyond.
  const barColor =
    percent == null
      ? "bg-primary"
      : percent >= 90
        ? "bg-destructive"
        : percent >= 75
          ? "bg-amber-500"
          : "bg-primary"

  return (
    <div className="rounded-md border border-border p-5 space-y-3">
      <p className="text-xs font-medium text-muted-foreground">
        {t("usage.title")}
      </p>
      {usage ? (
        <>
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-3xl font-bold tabular-nums">
              {formatTokens(usage.window_tokens, locale)}
            </p>
            {usage.quota != null && usage.quota > 0 && (
              <p className="text-xs text-muted-foreground">
                {t("usage.ofQuota", {
                  quota: formatTokens(usage.quota, locale),
                })}
              </p>
            )}
          </div>

          {usage.quota != null && usage.quota > 0 && percent != null ? (
            <div className="space-y-1.5">
              <div
                role="progressbar"
                aria-valuenow={percent}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={t("usage.percentLabel", { percent })}
                className="h-2 w-full overflow-hidden rounded-full bg-muted"
              >
                <div
                  className={cn("h-full transition-all", barColor)}
                  style={{ width: `${percent}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {t("usage.percentLabel", { percent })}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              {t("usage.unlimited")}
            </p>
          )}

          <p className="text-xs text-muted-foreground">{resetText}</p>

          <div className="pt-1">
            <Button
              asChild
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
            >
              <Link href="/settings?tab=usage">
                {t("usage.viewDetailed")}
                <ArrowRight className="ml-1 h-3 w-3" />
              </Link>
            </Button>
          </div>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">{t("usage.loadFailed")}</p>
      )}
    </div>
  )
}

interface SubscriptionStatusCardProps {
  subscription: SubscriptionInfo | null
  locale: string
  onManage: () => void
  portalPending: boolean
}

function SubscriptionStatusCard({
  subscription,
  locale,
  onManage,
  portalPending,
}: SubscriptionStatusCardProps) {
  const t = useTranslations("billing")

  if (!subscription) {
    return (
      <div className="space-y-3">
        <h3 className="text-sm font-medium">{t("subscription.title")}</h3>
        <div className="rounded-md border border-border p-5">
          <p className="text-sm text-muted-foreground">
            {t("subscription.noSubscription")}
          </p>
        </div>
      </div>
    )
  }

  const badge = statusBadgeProps(subscription.status, t)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{t("subscription.title")}</h3>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </div>
      <div className="rounded-md border border-border p-5 space-y-3">
        <div>
          <p className="text-xs font-medium text-muted-foreground">
            {t("subscription.currentPeriodEnd")}
          </p>
          <p className="mt-1 text-sm font-medium">
            {formatLongDate(subscription.current_period_end, locale)}
          </p>
        </div>
        <Button onClick={onManage} disabled={portalPending} variant="outline">
          {portalPending ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("action.portalLoading")}
            </>
          ) : (
            <>
              <ExternalLink className="mr-2 h-4 w-4" />
              {t("action.manage")}
            </>
          )}
        </Button>
      </div>
    </div>
  )
}

interface PlansComparisonProps {
  plans: PlanInfo[] | null
  currentPlanSlug: string | null
  onCheckout: (plan: PlanInfo) => void
  pendingSlug: string | null
  locale: string
}

function PlansComparison({
  plans,
  currentPlanSlug,
  onCheckout,
  pendingSlug,
  locale,
}: PlansComparisonProps) {
  const t = useTranslations("billing")

  if (!plans || plans.length === 0) {
    return (
      <div className="space-y-3">
        <h3 className="text-sm font-medium">{t("plans.title")}</h3>
        <div className="rounded-md border border-border p-5">
          <p className="text-sm text-muted-foreground">{t("plans.empty")}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-medium">{t("plans.title")}</h3>
        <p className="text-xs text-muted-foreground">
          {t("plans.description")}
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {plans.map((plan) => (
          <PlanCard
            key={plan.slug}
            plan={plan}
            isCurrent={plan.slug === currentPlanSlug}
            onCheckout={onCheckout}
            isPending={pendingSlug === plan.slug}
            locale={locale}
          />
        ))}
      </div>
    </div>
  )
}

interface PlanCardProps {
  plan: PlanInfo
  isCurrent: boolean
  onCheckout: (plan: PlanInfo) => void
  isPending: boolean
  locale: string
}

function PlanCard({
  plan,
  isCurrent,
  onCheckout,
  isPending,
  locale,
}: PlanCardProps) {
  const t = useTranslations("billing")

  // Free tier has no `stripe_price_id`, so it can't be checked out — we
  // render it as a static card with a disabled "current" state when
  // applicable, but no upgrade button.
  const isPurchasable = !!plan.stripe_price_id

  return (
    <div
      className={cn(
        "flex flex-col rounded-lg border p-5 transition-colors",
        isCurrent
          ? "border-primary bg-primary/5"
          : "border-border bg-card hover:bg-muted/30",
      )}
    >
      <div className="space-y-1">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-base font-semibold">{plan.name}</h3>
          {isCurrent && (
            <Badge variant="default" className="shrink-0">
              {t("plans.currentBadge")}
            </Badge>
          )}
        </div>
        {plan.price_display && (
          <p className="text-sm font-medium text-muted-foreground">
            {plan.price_display}
          </p>
        )}
        {plan.description && (
          <p className="text-xs text-muted-foreground">{plan.description}</p>
        )}
      </div>

      <div className="mt-4 space-y-2">
        <p className="text-sm font-medium tabular-nums">
          {plan.monthly_token_quota > 0
            ? t("plans.monthlyQuotaLabel", {
                quota: formatTokens(plan.monthly_token_quota, locale),
              })
            : t("currentPlan.unlimited")}
        </p>

        {plan.features.length > 0 && (
          <ul className="space-y-1 text-xs text-muted-foreground">
            {plan.features.map((f, i) => (
              <li key={i} className="flex items-start gap-1.5">
                <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
                <span>{f}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mt-auto pt-4">
        {isCurrent ? (
          <Button variant="outline" disabled className="w-full">
            {t("plans.currentBadge")}
          </Button>
        ) : isPurchasable ? (
          <Button
            onClick={() => onCheckout(plan)}
            disabled={isPending}
            className="w-full disabled:opacity-100"
            aria-label={isPending ? t("action.checkoutLoading") : undefined}
          >
            {isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              t("action.switchPlan", { plan: plan.name })
            )}
          </Button>
        ) : (
          // Free / non-purchasable tier — no checkout, just a quiet label.
          <Button variant="ghost" disabled className="w-full">
            {plan.price_display || t("currentPlan.freeFallback")}
          </Button>
        )}
      </div>
    </div>
  )
}
