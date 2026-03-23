'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { AlertTriangle, CheckCircle2, Database, Server } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { api, type DependencyManifest, type MarketItem } from '@/lib/api'
import { useDateFormatter } from '@/hooks/use-date-formatter'

const RESOURCE_ROUTES: Record<string, string> = {
  agent: '/agents',
  connector: '/connectors',
  knowledge_base: '/kb',
  mcp_server: '/mcp',
  skill: '/skills',
  workflow: '/workflows',
}

interface ResourceDetailModalProps {
  item: MarketItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubscribeSuccess: () => void
}

export function ResourceDetailModal({
  item,
  open,
  onOpenChange,
  onSubscribeSuccess,
}: ResourceDetailModalProps) {
  const t = useTranslations('market')
  const tc = useTranslations('common')
  const router = useRouter()
  const { formatDate } = useDateFormatter()
  const [subscribing, setSubscribing] = useState(false)
  const [subscribeSuccess, setSubscribeSuccess] = useState(false)
  const [subscribeDeps, setSubscribeDeps] = useState<DependencyManifest | null>(null)
  const [showUnsubConfirm, setShowUnsubConfirm] = useState(false)

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      setSubscribeSuccess(false)
      setSubscribeDeps(null)
    }
    onOpenChange(nextOpen)
  }

  const handleSubscribe = async () => {
    if (!item) return
    setSubscribing(true)
    try {
      const res = await api.subscribeResource({
        resource_type: item.resource_type,
        resource_id: item.id,
        org_id: item.org_id,
      })
      // Extract dependency manifest from response
      if (res?.data?.dependencies) {
        setSubscribeDeps(res.data.dependencies)
      }
      setSubscribeSuccess(true)
    } catch {
      toast.error(tc('error'))
    } finally {
      setSubscribing(false)
    }
  }

  const handleUnsubscribe = async () => {
    if (!item) return
    setSubscribing(true)
    try {
      await api.unsubscribeResource({
        resource_type: item.resource_type,
        resource_id: item.id,
        org_id: item.org_id,
      })
      toast.success(t('unsubscribeSuccess'))
      onSubscribeSuccess()
    } catch {
      toast.error(tc('error'))
    } finally {
      setSubscribing(false)
    }
  }

  const handleGoConfigure = () => {
    if (!item) return
    const route = RESOURCE_ROUTES[item.resource_type]
    if (route) {
      router.push(route)
    }
    handleOpenChange(false)
    onSubscribeSuccess()
  }

  const handleStayHere = () => {
    handleOpenChange(false)
    onSubscribeSuccess()
  }

  if (!item) return null

  const depsNeedingSetup = subscribeDeps?.connection_deps.filter(d => !d.allow_fallback) ?? []

  const isSolution = ['agent', 'skill', 'workflow'].includes(item.resource_type)
  const categoryLabel = isSolution ? t('categorySolutions') : t('categoryComponents')
  const authorParts: string[] = []
  if (item.owner_username) authorParts.push(item.owner_username)
  if (item.org_name) authorParts.push(item.org_name)
  const authorText = authorParts.join(' / ')

  // Success state after subscribing
  if (subscribeSuccess) {
    return (
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="sm:max-w-md">
          <div className="flex flex-col items-center gap-4 py-6 text-center">
            <CheckCircle2 className="h-12 w-12 text-emerald-500" />
            <div className="space-y-2">
              <DialogTitle className="text-center">
                {t('subscribeSuccessTitle')}
              </DialogTitle>
              <DialogDescription className="text-center">
                {t('subscribeSuccessDescription', { name: item.name })}
              </DialogDescription>
            </div>
          </div>

          {/* Connection deps needing credential setup */}
          {depsNeedingSetup.length > 0 && (
            <div className="space-y-2 border-t pt-4">
              <div className="flex items-center gap-2 text-sm font-medium text-amber-600 dark:text-amber-400">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <span>{t('credentialSetupRequired')}</span>
              </div>
              {depsNeedingSetup.map((dep) => (
                <Link
                  key={`${dep.resource_type}:${dep.resource_id}`}
                  href={dep.resource_type === 'mcp_server' ? `/mcp/${dep.resource_id}` : `/connectors/${dep.resource_id}`}
                  className="flex items-center justify-between gap-2 rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-sm transition-colors hover:bg-amber-100 dark:hover:bg-amber-900/30"
                  onClick={() => { handleOpenChange(false); onSubscribeSuccess() }}
                >
                  <span className="flex items-center gap-2 text-amber-700 dark:text-amber-300">
                    {dep.resource_type === 'mcp_server' ? <Server className="h-3.5 w-3.5 shrink-0" /> : <Database className="h-3.5 w-3.5 shrink-0" />}
                    {dep.resource_name}
                  </span>
                  <span className="text-xs text-amber-600 dark:text-amber-400">{t('configureDep')} &rarr;</span>
                </Link>
              ))}
              <p className="text-xs text-muted-foreground">{t('credentialSetupWarning')}</p>
            </div>
          )}

          <DialogFooter className="sm:justify-center gap-2">
            <Button onClick={handleGoConfigure}>
              {t('goToConfigure')} &rarr;
            </Button>
            <Button variant="ghost" onClick={handleStayHere}>
              {t('stayHere')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }

  // Detail view
  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-xs">
              {t(`typeSingular.${item.resource_type}`)}
            </Badge>
            <Badge variant="outline" className="text-xs">
              {categoryLabel}
            </Badge>
          </div>
          <DialogTitle className="flex items-center gap-2">
            {item.icon && <span className="text-xl leading-none">{item.icon}</span>}
            {item.name}
          </DialogTitle>
          {authorText && (
            <DialogDescription>
              {t('detailBy', { author: authorText })}
            </DialogDescription>
          )}
        </DialogHeader>

        <div className="space-y-4">
          {/* Description */}
          {item.description && (
            <p className="text-sm text-muted-foreground leading-relaxed">
              {item.description}
            </p>
          )}

          <Separator />

          {/* Details section */}
          <div className="space-y-2">
            <h4 className="text-sm font-medium">{t('detailDetails')}</h4>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">{t('detailType')}</dt>
              <dd>{t(`typeSingular.${item.resource_type}`)}</dd>
              {item.created_at && (
                <>
                  <dt className="text-muted-foreground">{t('detailCreated')}</dt>
                  <dd>{formatDate(item.created_at)}</dd>
                </>
              )}
            </dl>
          </div>
        </div>

        <DialogFooter>
          {item.is_own ? (
            <Badge variant="outline" className="text-sm text-blue-600 px-3 py-1.5">
              {t('yours')}
            </Badge>
          ) : item.is_subscribed ? (
            <Button
              variant="outline"
              disabled={subscribing}
              onClick={() => setShowUnsubConfirm(true)}
            >
              {t('unsubscribe')}
            </Button>
          ) : (
            <Button disabled={subscribing} onClick={handleSubscribe}>
              {t('subscribe')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>

      {/* Unsubscribe Confirmation */}
      <AlertDialog open={showUnsubConfirm} onOpenChange={setShowUnsubConfirm}>
        <AlertDialogContent className="sm:max-w-sm">
          <AlertDialogHeader>
            <AlertDialogTitle>{t('unsubscribeConfirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('unsubscribeConfirmDescription', { name: item.name })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleUnsubscribe}
            >
              {t('unsubscribe')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  )
}
