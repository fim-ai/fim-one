import type { Metadata } from "next"
import { Inter, JetBrains_Mono } from "next/font/google"
import localFont from "next/font/local"
import { NextIntlClientProvider } from "next-intl"
import { getLocale, getMessages } from "next-intl/server"
import Script from "next/script"
import "./globals.css"
import { APP_NAME } from "@/lib/constants"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AppShell } from "@/components/layout/app-shell"
import { AuthProvider } from "@/contexts/auth-context"
import { ThemeProvider } from "@/components/theme-provider"
import { Toaster } from "sonner"

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
})

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
})

const cabinetGrotesk = localFont({
  src: "../../public/fonts/CabinetGrotesk-Bold.woff2",
  variable: "--font-cabinet",
  weight: "700",
  display: "swap",
})

export const metadata: Metadata = {
  title: {
    default: APP_NAME,
    template: `%s — ${APP_NAME}`,
  },
  description: "Intelligent agent framework with fill-in-the-middle capabilities",
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  const locale = await getLocale()
  const messages = await getMessages()

  return (
    <html lang={locale} suppressHydrationWarning>
      <head>
        {/* Google Analytics (GA4) — set NEXT_PUBLIC_GA_MEASUREMENT_ID=G-XXXXXXXXXX */}
        {process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID && (
          <>
            <Script
              src={`https://www.googletagmanager.com/gtag/js?id=${process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID}`}
              strategy="afterInteractive"
            />
            <Script id="ga4-init" strategy="afterInteractive">{`
              window.dataLayer=window.dataLayer||[];
              function gtag(){dataLayer.push(arguments)}
              gtag('js',new Date());
              gtag('config','${process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID}');
            `}</Script>
          </>
        )}
        {/* Umami — set NEXT_PUBLIC_UMAMI_SCRIPT_URL + NEXT_PUBLIC_UMAMI_WEBSITE_ID */}
        {process.env.NEXT_PUBLIC_UMAMI_SCRIPT_URL && process.env.NEXT_PUBLIC_UMAMI_WEBSITE_ID && (
          <Script
            defer
            src={process.env.NEXT_PUBLIC_UMAMI_SCRIPT_URL}
            data-website-id={process.env.NEXT_PUBLIC_UMAMI_WEBSITE_ID}
            strategy="afterInteractive"
          />
        )}
        {/* Plausible — set NEXT_PUBLIC_PLAUSIBLE_DOMAIN (e.g. yourdomain.com) */}
        {process.env.NEXT_PUBLIC_PLAUSIBLE_DOMAIN && (
          <Script
            defer
            data-domain={process.env.NEXT_PUBLIC_PLAUSIBLE_DOMAIN}
            src={process.env.NEXT_PUBLIC_PLAUSIBLE_SCRIPT_URL ?? "https://plausible.io/js/script.js"}
            strategy="afterInteractive"
          />
        )}
      </head>
      <body className={`${inter.variable} ${jetbrainsMono.variable} ${cabinetGrotesk.variable} font-sans antialiased`}>
        <NextIntlClientProvider messages={messages}>
          <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
            <AuthProvider>
              <TooltipProvider>
                <AppShell>{children}</AppShell>
                <Toaster theme="dark" position="top-center" richColors />
              </TooltipProvider>
            </AuthProvider>
          </ThemeProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  )
}
