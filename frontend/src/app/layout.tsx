import type { Metadata } from "next"
import { Inter } from "next/font/google"
import "./globals.css"
import { APP_NAME } from "@/lib/constants"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AppShell } from "@/components/layout/app-shell"
import { AuthProvider } from "@/contexts/auth-context"
import { ThemeProvider } from "@/components/theme-provider"

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
})

export const metadata: Metadata = {
  title: APP_NAME,
  description: "Intelligent agent framework with fill-in-the-middle capabilities",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} font-sans antialiased`}>
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
          <AuthProvider>
            <TooltipProvider>
              <AppShell>{children}</AppShell>
            </TooltipProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
