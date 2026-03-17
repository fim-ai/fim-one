import { getTranslations } from "next-intl/server"

export async function generateMetadata() {
  const t = await getTranslations("admin")
  return { title: t("panelTitle") }
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
