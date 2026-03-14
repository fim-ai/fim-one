"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"

export default function SkillEditorRedirect() {
  const router = useRouter()
  useEffect(() => {
    router.replace("/skills")
  }, [router])
  return null
}
