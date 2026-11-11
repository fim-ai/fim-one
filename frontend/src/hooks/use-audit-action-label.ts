import { useTranslations } from "next-intl"

import { auditActionKey } from "@/lib/audit-records"

/**
 * The label of an audit action name.
 *
 * Registered actions have theirs under `admin.audit.actionLabels`. The other
 * record tables read as actions too: a sign-in (`auth.login`) and a call
 * (`call.connector`) under `sourceActionLabels`, a review entry
 * (`review.approved`) under `reviewActionLabels`. A name with no label is
 * shown as it is.
 */
export function useAuditActionLabel(): (action: string) => string {
  const tActions = useTranslations("admin.audit.actionLabels")
  const tSources = useTranslations("admin.audit.sourceActionLabels")
  const tReviews = useTranslations("admin.audit.reviewActionLabels")
  return (action: string) => {
    try {
      if (action.startsWith("review.")) {
        const key = action.slice("review.".length)
        return tReviews.has(key as Parameters<typeof tReviews.has>[0])
          ? tReviews(key as Parameters<typeof tReviews>[0])
          : action
      }
      const key = auditActionKey(action)
      if (tSources.has(key as Parameters<typeof tSources.has>[0])) {
        return tSources(key as Parameters<typeof tSources>[0])
      }
      return tActions.has(key as Parameters<typeof tActions.has>[0])
        ? tActions(key as Parameters<typeof tActions>[0])
        : action
    } catch {
      return action
    }
  }
}
