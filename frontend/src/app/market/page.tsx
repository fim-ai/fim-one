'use client'

import { useState, useEffect, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { ShoppingBag } from 'lucide-react'
import { EmptyState } from '@/components/shared/empty-state'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, type MarketItem } from '@/lib/api'
import { toast } from 'sonner'

const RESOURCE_TYPES = ['all', 'agent', 'connector', 'knowledge_base', 'mcp_server', 'skill', 'workflow'] as const

function MarketContent() {
  const t = useTranslations('market')
  const tc = useTranslations('common')
  const searchParams = useSearchParams()
  const router = useRouter()
  const [items, setItems] = useState<MarketItem[]>([])
  const [loading, setLoading] = useState(true)
  const [subscribing, setSubscribing] = useState<string | null>(null)

  const activeType = searchParams.get('type') || 'all'

  const fetchMarket = async () => {
    setLoading(true)
    try {
      const params: Parameters<typeof api.browseMarket>[0] = { page: 1, size: 50 }
      if (activeType !== 'all') params.resource_type = activeType
      const res = await api.browseMarket(params)
      setItems(res?.items ?? [])
    } catch {
      toast.error(tc('error'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchMarket() }, [activeType]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSubscribe = async (item: MarketItem) => {
    setSubscribing(item.id)
    try {
      if (item.is_subscribed) {
        await api.unsubscribeResource({ resource_type: item.resource_type, resource_id: item.id, org_id: item.org_id })
        toast.success(t('unsubscribeSuccess'))
      } else {
        await api.subscribeResource({ resource_type: item.resource_type, resource_id: item.id, org_id: item.org_id })
        toast.success(t('subscribeSuccess'))
      }
      fetchMarket()
    } catch {
      toast.error(tc('error'))
    } finally {
      setSubscribing(null)
    }
  }

  const handleTypeChange = (type: string) => {
    const params = new URLSearchParams(searchParams.toString())
    if (type === 'all') {
      params.delete('type')
    } else {
      params.set('type', type)
    }
    router.replace(`?${params.toString()}`)
  }

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
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        <Tabs value={activeType} onValueChange={handleTypeChange}>
          <TabsList>
            {RESOURCE_TYPES.map(type => (
              <TabsTrigger key={type} value={type}>
                {t(`types.${type}`)}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        {loading ? (
          <div className="text-center py-12 text-muted-foreground">{tc('loading')}</div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<ShoppingBag />}
            title={t("emptyTitle")}
            description={t("emptyDescription")}
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {items.map((item) => (
              <div key={item.id} className="border rounded-lg p-4 space-y-3 bg-card">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-medium truncate">{item.name}</h3>
                    {item.description && (
                      <p className="text-sm text-muted-foreground line-clamp-2 mt-1">
                        {item.description}
                      </p>
                    )}
                  </div>
                  <Badge variant="secondary" className="shrink-0 text-xs">
                    {t(`types.${item.resource_type}`)}
                  </Badge>
                </div>

                {(item.owner_username || item.org_name) && (
                  <p className="text-xs text-muted-foreground">
                    {item.owner_username}{item.org_name ? ` / ${item.org_name}` : ''}
                  </p>
                )}

                <div className="flex items-center justify-between">
                  {item.is_subscribed && (
                    <Badge variant="outline" className="text-xs">{t('subscribed')}</Badge>
                  )}
                  <Button
                    size="sm"
                    variant={item.is_subscribed ? 'outline' : 'default'}
                    disabled={subscribing === item.id}
                    onClick={() => handleSubscribe(item)}
                    className="ml-auto"
                  >
                    {item.is_subscribed ? t('unsubscribe') : t('subscribe')}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
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
