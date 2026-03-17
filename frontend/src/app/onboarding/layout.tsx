import { getTranslations } from "next-intl/server"

export async function generateMetadata() {
  const t = await getTranslations("onboarding")
  return { title: t("welcomeTitle") }
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
