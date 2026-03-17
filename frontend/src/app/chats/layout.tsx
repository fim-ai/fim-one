import { getTranslations } from "next-intl/server"

export async function generateMetadata() {
  const t = await getTranslations("layout")
  return { title: t("allChats") }
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
