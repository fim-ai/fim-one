'use client'

import { useState, useEffect, useCallback, useMemo, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { ShoppingBag, Globe, Building2, Layers, Puzzle, Search } from 'lucide-react'
import { EmptyState } from '@/components/shared/empty-state'
import { ListPagination, PAGE_SIZE } from '@/components/shared/list-pagination'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { api, orgApi, type MarketItem, type UserOrg } from '@/lib/api'
import { ResourceDetailModal } from '@/components/market/resource-detail-modal'
import { toast } from 'sonner'
import { usePageTitle } from '@/hooks/use-page-title'

const CATEGORY_MAP = {
  solutions: ['all', 'agent', 'skill', 'workflow'],
  components: ['all', 'connector', 'mcp_server'],
} as const

type MarketCategory = keyof typeof CATEGORY_MAP

function MarketContent() {
  const t = useTranslations('market')
  const tc = useTranslations('common')
  const searchParams = useSearchParams()
  const router = useRouter()
  const [items, setItems] = useState<MarketItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedItem, setSelectedItem] = useState<MarketItem | null>(null)
  const [orgs, setOrgs] = useState<UserOrg[]>([])
  const [searchQuery, setSearchQuery] = useState("")
  const [currentPage, setCurrentPage] = useState(1)

  usePageTitle(t('title'))

  const scope = searchParams.get('scope') || 'market'
  const category = (searchParams.get('category') || 'solutions') as MarketCategory
  const activeType = searchParams.get('type') || 'all'

  // Load user orgs on mount
  useEffect(() => {
    orgApi.list().then(setOrgs).catch(() => {})
  }, [])

  const fetchMarket = useCallback(async () => {
    setLoading(true)
    try {
      const params: Parameters<typeof api.browseMarket>[0] = {
        page: 1,
        size: 50,
        scope: scope === 'market' ? 'market' : `org:${scope}`,
        category,
      }
      if (activeType !== 'all') params.resource_type = activeType
      const res = await api.browseMarket(params)
      setItems(res?.items ?? [])
    } catch {
      toast.error(tc('error'))
    } finally {
      setLoading(false)
    }
  }, [scope, category, activeType, tc])

  useEffect(() => { fetchMarket() }, [fetchMarket])


  const updateParams = useCallback((updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString())
    for (const [key, value] of Object.entries(updates)) {
      if (value === null || value === '') {
        params.delete(key)
      } else {
        params.set(key, value)
      }
    }
    const qs = params.toString()
    router.replace(qs ? `?${qs}` : '?')
  }, [searchParams, router])

  const handleScopeChange = (newScope: string) => {
    // Keep category, reset type to 'all'
    updateParams({
      scope: newScope === 'market' ? null : newScope,
      type: null,
    })
  }

  const handleCategoryChange = (newCategory: MarketCategory) => {
    // Reset type to 'all' when category changes
    updateParams({
      category: newCategory === 'solutions' ? null : newCategory,
      type: null,
    })
  }

  const handleTypeChange = (type: string) => {
    updateParams({
      type: type === 'all' ? null : type,
    })
  }

  const validCategory = category in CATEGORY_MAP ? category : 'solutions'
  const typeOptions = CATEGORY_MAP[validCategory]

  const searchedItems = useMemo(() => {
    if (!searchQuery.trim()) return items
    const q = searchQuery.toLowerCase()
    return items.filter(item =>
      item.name.toLowerCase().includes(q) ||
      (item.description ?? '').toLowerCase().includes(q)
    )
  }, [items, searchQuery])

  const totalPages = Math.ceil(searchedItems.length / PAGE_SIZE)
  const paginatedItems = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return searchedItems.slice(start, start + PAGE_SIZE)
  }, [searchedItems, currentPage])

  useEffect(() => { setCurrentPage(1) }, [searchQuery, scope, category, activeType])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <ShoppingBag className="h-5 w-5" />
            {t('title')}
          </h1>
          <p className="text-sm text-muted-foreground">{t('description')}</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Scope selector — right side of header */}
          <Select value={scope} onValueChange={handleScopeChange}>
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="market">
                <span className="flex items-center gap-2">
                  <Globe className="h-4 w-4" />
                  {t('scopeGlobalMarket')}
                </span>
              </SelectItem>
              {orgs.map((org) => (
                <SelectItem key={org.id} value={org.id}>
                  <span className="flex items-center gap-2">
                    <Building2 className="h-4 w-4" />
                    {org.name}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Row 1: Category toggle — underline style (aligned with eval page) */}
      <div className="border-b px-6">
        <nav className="flex gap-4 -mb-px">
          {(["solutions", "components"] as const).map((cat) => (
            <button
              key={cat}
              onClick={() => handleCategoryChange(cat)}
              className={cn(
                "py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5",
                validCategory === cat
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {cat === "solutions" ? <Layers className="h-3.5 w-3.5" /> : <Puzzle className="h-3.5 w-3.5" />}
              {t(cat === "solutions" ? "categorySolutions" : "categoryComponents")}
            </button>
          ))}
        </nav>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {/* Row 2: Type filter — pill style (aligned with connectors ScopeFilter) + search */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            {typeOptions.map(type => (
              <button
                key={type}
                onClick={() => handleTypeChange(type)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  activeType === type
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
                )}
              >
                {t(`types.${type}`)}
              </button>
            ))}
          </div>
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              className="h-8 pl-8 text-xs"
              placeholder={tc("searchPlaceholder")}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>

        {loading ? (
          <div className="text-center py-12 text-muted-foreground">{tc('loading')}</div>
        ) : searchedItems.length === 0 ? (
          <EmptyState
            icon={<ShoppingBag />}
            title={searchQuery.trim() ? tc("noResultsTitle") : t("emptyTitle")}
            description={searchQuery.trim() ? tc("noResultsDescription") : t("emptyDescription")}
          />
        ) : (
          <>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {paginatedItems.map((item) => (
              <div
                key={item.id}
                className="border rounded-lg p-4 space-y-3 bg-card cursor-pointer hover:border-foreground/20 transition-colors"
                onClick={() => setSelectedItem(item)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-medium truncate">{item.name}</h3>
                    {item.description && (
                      <p className="text-sm text-muted-foreground line-clamp-2 mt-1">
                        {item.description}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {item.is_subscribed && (
                      <Badge variant="outline" className="text-xs">{t('subscribed')}</Badge>
                    )}
                    <Badge variant="secondary" className="text-xs">
                      {t(`types.${item.resource_type}`)}
                    </Badge>
                  </div>
                </div>

                {(item.owner_username || item.org_name) && (
                  <p className="text-xs text-muted-foreground">
                    {item.owner_username}{item.org_name ? ` / ${item.org_name}` : ''}
                  </p>
                )}
              </div>
            ))}
          </div>
          <ListPagination currentPage={currentPage} totalPages={totalPages} onPageChange={setCurrentPage} />
          </>
        )}
      </div>

      <ResourceDetailModal
        item={selectedItem}
        open={selectedItem !== null}
        onOpenChange={(open) => { if (!open) setSelectedItem(null) }}
        onSubscribeSuccess={() => { setSelectedItem(null); fetchMarket() }}
      />
    </div>
  )
}

export default function MarketPage() {
  return (
    <Suspense>
      <MarketContent />
    </Suspense>
  )
}
