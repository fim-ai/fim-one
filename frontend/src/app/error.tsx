"use client"

import { useTranslations } from "next-intl"

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  const t = useTranslations("auth")
  const tc = useTranslations("common")

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <p className="select-none whitespace-nowrap text-8xl font-bold leading-none tracking-tight text-muted-foreground/80">
        (╥﹏╥)
      </p>
      <p className="mt-8 text-2xl font-medium text-foreground">{t("somethingWentWrong")}</p>
      <p className="mt-2 max-w-md text-center text-muted-foreground">
        {error.message || t("unexpectedError")}
      </p>
      <button
        onClick={reset}
        className="mt-8 rounded-md bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        {tc("retry")}
      </button>
    </div>
  )
}
