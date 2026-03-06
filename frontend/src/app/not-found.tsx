"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"

export default function NotFound() {
  const t = useTranslations("auth")

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <p className="select-none whitespace-nowrap text-8xl font-bold leading-none tracking-tight text-muted-foreground/80">
        ¯\_(ツ)_/¯
      </p>
      <p className="mt-8 text-2xl font-medium text-foreground">{t("pageNotFoundTitle")}</p>
      <p className="mt-2 text-muted-foreground">{t("pageNotFoundDescription")}</p>
      <Link
        href="/"
        className="mt-8 rounded-md bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        {t("backToHome")}
      </Link>
    </div>
  )
}
