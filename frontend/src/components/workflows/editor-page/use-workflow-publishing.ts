import { useCallback, useState } from "react"
import type { Dispatch, SetStateAction } from "react"
import { toast } from "sonner"
import { useTranslations } from "next-intl"

import { orgApi, workflowApi } from "@/lib/api"
import type { UserOrg } from "@/lib/api"
import type { WorkflowResponse } from "@/types/workflow"

/** Publishing a workflow to an organisation, unpublishing, and resubmitting after a rejection. */
export function useWorkflowPublishing(
  workflow: WorkflowResponse | null,
  setWorkflow: Dispatch<SetStateAction<WorkflowResponse | null>>,
) {
  const t = useTranslations("workflows")
  const to = useTranslations("organizations")

  const [showPublishDialog, setShowPublishDialog] = useState(false)
  const [showUnpublishDialog, setShowUnpublishDialog] = useState(false)
  const [publishOrgId, setPublishOrgId] = useState<string>("")
  const [userOrgs, setUserOrgs] = useState<UserOrg[]>([])
  const [orgsLoading, setOrgsLoading] = useState(false)

  const openPublishDialog = useCallback(() => {
    setShowPublishDialog(true)
    setPublishOrgId("")
    setOrgsLoading(true)
    orgApi.list().then((orgs) => {
      setUserOrgs(orgs)
      if (orgs.length > 0) setPublishOrgId(orgs[0].id)
    }).catch(() => {}).finally(() => setOrgsLoading(false))
  }, [])

  const confirmPublish = useCallback(async () => {
    if (!workflow || !publishOrgId) return
    setShowPublishDialog(false)
    try {
      const updated = await workflowApi.publish(workflow.id, {
        scope: "org",
        org_id: publishOrgId,
      })
      setWorkflow(updated)
      toast.success(t("workflowPublished"))
    } catch {
      toast.error(t("workflowPublishFailed"))
    }
  }, [workflow, publishOrgId, setWorkflow, t])

  const openUnpublishDialog = useCallback(() => {
    setShowUnpublishDialog(true)
  }, [])

  const confirmUnpublish = useCallback(async () => {
    if (!workflow) return
    setShowUnpublishDialog(false)
    try {
      const updated = await workflowApi.unpublish(workflow.id)
      setWorkflow(updated)
      toast.success(t("workflowUnpublished"))
    } catch {
      toast.error(t("workflowUnpublishFailed"))
    }
  }, [workflow, setWorkflow, t])

  const resubmit = useCallback(async () => {
    if (!workflow) return
    try {
      const updated = await workflowApi.resubmit(workflow.id)
      setWorkflow(updated)
      toast.success(to("resubmitSuccess"))
    } catch {
      toast.error(to("resubmitFailed"))
    }
  }, [workflow, setWorkflow, to])

  // Find selected org for review notice
  const selectedOrg = publishOrgId
    ? userOrgs.find((o) => o.id === publishOrgId)
    : null

  return {
    showPublishDialog,
    setShowPublishDialog,
    showUnpublishDialog,
    setShowUnpublishDialog,
    publishOrgId,
    setPublishOrgId,
    userOrgs,
    orgsLoading,
    selectedOrg,
    openPublishDialog,
    confirmPublish,
    openUnpublishDialog,
    confirmUnpublish,
    resubmit,
  }
}
