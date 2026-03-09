import useSWR from "swr"
import { apiFetch } from "@/lib/api"

export interface ToolMeta {
  name: string
  display_name: string
  category: string
  description: string
  available?: boolean
  unavailable_reason?: string
  disabled?: boolean
}

export interface ToolCatalog {
  tools: ToolMeta[]
  categories: string[]
}

const fetcher = (url: string) => apiFetch<ToolCatalog>(url)

export function useToolCatalog() {
  return useSWR<ToolCatalog>("/api/tools/catalog", fetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 300_000, // 5 min — tool list rarely changes
  })
}
