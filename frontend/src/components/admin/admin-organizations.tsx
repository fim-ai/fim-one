"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  Loader2,
  Search,
  Plus,
  MoreHorizontal,
  Pencil,
  Trash2,
  Users,
  UserPlus,
  UserMinus,
  ShieldCheck,
  Crown,
  Building2,
  Store,
  ClipboardCheck,
  Check,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Separator } from "@/components/ui/separator"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { adminApi, orgApi, type ReviewItem } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { MARKET_ORG_ID } from "@/lib/constants"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import { EmojiPickerPopover } from "@/components/ui/emoji-picker-popover"
import type { AdminOrganization, OrgMember } from "@/types/admin"

const PAGE_SIZE = 20

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function resourceTypeLabel(type: string, t: (key: string) => string): string {
  switch (type) {
    case "agent": return t("resourceTypeAgent")
    case "connector": return t("resourceTypeConnector")
    case "knowledge_base": return t("resourceTypeKb")
    case "mcp_server": return t("resourceTypeMcpServer")
    case "workflow": return t("resourceTypeWorkflow")
    case "skill": return t("resourceTypeSkill")
    default: return type
  }
}

// ---------------------------------------------------------------------------
// AdminReviewsSheet -- system admin review management for any org
// ---------------------------------------------------------------------------

interface AdminReviewsSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  org: { id: string; name: string } | null
}

function AdminReviewsSheet({ open, onOpenChange, org }: AdminReviewsSheetProps) {
  const t = useTranslations("organizations")
  const tc = useTranslations("common")

  const [reviews, setReviews] = useState<ReviewItem[]>([])
  const [loading, setLoading] = useState(false)
  const [resourceTypeFilter, setResourceTypeFilter] = useState<string>("__default__")
  const [statusFilter, setStatusFilter] = useState<string>("pending_review")

  // Reject dialog state
  const [rejectTarget, setRejectTarget] = useState<ReviewItem | null>(null)
  const [rejectNote, setRejectNote] = useState("")

  const loadReviews = useCallback(async () => {
    if (!org) return
    setLoading(true)
    try {
      const params: { resource_type?: string; status?: string } = {}
      if (resourceTypeFilter !== "__default__") params.resource_type = resourceTypeFilter
      if (statusFilter !== "__default__") params.status = statusFilter
      const data = await orgApi.listReviews(org.id, params)
      setReviews(data)
    } catch {
      toast.error(t("reviewLoadFailed"))
    } finally {
      setLoading(false)
    }
  }, [org, resourceTypeFilter, statusFilter, t])

  useEffect(() => {
    if (open && org) {
      loadReviews()
    }
  }, [open, loadReviews, org])

  const handleApprove = async (item: ReviewItem) => {
    if (!org) return
    try {
      await orgApi.approveReview(org.id, {
        resource_type: item.resource_type,
        resource_id: item.resource_id,
      })
      toast.success(t("reviewApproved"))
      setReviews((prev) => prev.filter((r) => r.resource_id !== item.resource_id))
    } catch {
      toast.error(t("reviewApproveFailed"))
    }
  }

  const handleReject = async () => {
    if (!rejectTarget || !org) return
    try {
      await orgApi.rejectReview(org.id, {
        resource_type: rejectTarget.resource_type,
        resource_id: rejectTarget.resource_id,
        note: rejectNote.trim() || undefined,
      })
      toast.success(t("reviewRejected"))
      setReviews((prev) => prev.filter((r) => r.resource_id !== rejectTarget.resource_id))
    } catch {
      toast.error(t("reviewRejectFailed"))
    } finally {
      setRejectTarget(null)
      setRejectNote("")
    }
  }

  if (!org) return null

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className="w-full sm:max-w-lg flex flex-col">
          <SheetHeader className="shrink-0">
            <SheetTitle>{t("reviewManagement")}</SheetTitle>
            <SheetDescription>{t("reviewsSheetDescription")}</SheetDescription>
          </SheetHeader>

          <div className="flex-1 overflow-y-auto space-y-4 mt-4">
            {/* Explanation note — only for Market org */}
            {org && org.id === MARKET_ORG_ID && (
              <div className="rounded-md border border-blue-400/30 bg-blue-500/5 p-3">
                <p className="text-xs text-muted-foreground">{t("reviewBypassExplanation")}</p>
              </div>
            )}

            {/* Filters */}
            <div className="flex gap-2">
              <Select value={resourceTypeFilter} onValueChange={setResourceTypeFilter}>
                <SelectTrigger className="w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">{t("filterAll")}</SelectItem>
                  <SelectItem value="agent">{t("filterAgents")}</SelectItem>
                  <SelectItem value="connector">{t("filterConnectors")}</SelectItem>
                  <SelectItem value="knowledge_base">{t("filterKBs")}</SelectItem>
                  <SelectItem value="mcp_server">{t("filterMcpServers")}</SelectItem>
                  <SelectItem value="workflow">{t("filterWorkflows")}</SelectItem>
                  <SelectItem value="skill">{t("filterSkills")}</SelectItem>
                </SelectContent>
              </Select>
              <Select value={statusFilter} onValueChange={setStatusFilter}>
                <SelectTrigger className="w-[130px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">{t("filterAll")}</SelectItem>
                  <SelectItem value="pending_review">{t("filterPending")}</SelectItem>
                  <SelectItem value="rejected">{t("filterRejected")}</SelectItem>
                  <SelectItem value="approved">{t("filterApproved")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <Separator />

            {/* Review list */}
            {loading ? (
              <p className="text-sm text-muted-foreground py-4 text-center">{tc("loading")}</p>
            ) : reviews.length === 0 ? (
              <p className="text-sm text-muted-foreground py-8 text-center">{t("noReviewsPending")}</p>
            ) : (
              <div className="space-y-2">
                {reviews.map((item) => (
                  <div
                    key={`${item.resource_type}-${item.resource_id}`}
                    className="rounded-md border border-border p-3 space-y-2"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <div className="h-8 w-8 rounded-md bg-muted flex items-center justify-center text-sm shrink-0">
                          {item.resource_icon ?? <Building2 className="h-4 w-4 text-muted-foreground" />}
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium truncate">{item.resource_name}</p>
                          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                            <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4">
                              {resourceTypeLabel(item.resource_type, t)}
                            </Badge>
                            {item.owner_username && (
                              <span>{t("submittedBy", { owner: item.owner_username })}</span>
                            )}
                          </div>
                        </div>
                      </div>

                      {/* Status badge */}
                      {item.publish_status === "pending_review" && (
                        <Badge variant="outline" className="border-amber-400 text-amber-600 dark:text-amber-400 shrink-0">
                          {t("pendingReview")}
                        </Badge>
                      )}
                      {item.publish_status === "approved" && (
                        <Badge variant="outline" className="border-emerald-400 text-emerald-600 dark:text-emerald-400 shrink-0">
                          {t("approved")}
                        </Badge>
                      )}
                      {item.publish_status === "rejected" && (
                        <Badge variant="outline" className="border-destructive text-destructive shrink-0">
                          {t("rejected")}
                        </Badge>
                      )}
                    </div>

                    {/* Review note for rejected items */}
                    {item.publish_status === "rejected" && item.review_note && (
                      <p className="text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1">
                        {t("rejectedNote", { note: item.review_note })}
                      </p>
                    )}

                    {/* Action buttons -- only for pending items */}
                    {item.publish_status === "pending_review" && (
                      <div className="flex items-center gap-2 pt-1">
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 text-xs gap-1 text-emerald-600 dark:text-emerald-400 border-emerald-400/40 hover:bg-emerald-500/10"
                          onClick={() => handleApprove(item)}
                        >
                          <Check className="h-3.5 w-3.5" />
                          {t("approveResource")}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 text-xs gap-1 text-destructive border-destructive/40 hover:bg-destructive/10"
                          onClick={() => { setRejectTarget(item); setRejectNote("") }}
                        >
                          <X className="h-3.5 w-3.5" />
                          {t("rejectResource")}
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>

      {/* Reject confirmation dialog -- sibling of Sheet */}
      <Dialog open={!!rejectTarget} onOpenChange={(v) => { if (!v) { setRejectTarget(null); setRejectNote("") } }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("rejectDialogTitle")}</DialogTitle>
            <DialogDescription>{t("rejectDialogDescription")}</DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Textarea
              value={rejectNote}
              onChange={(e) => setRejectNote(e.target.value)}
              placeholder={t("rejectReasonPlaceholder")}
              rows={3}
              className="resize-none"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setRejectTarget(null); setRejectNote("") }}>
              {tc("cancel")}
            </Button>
            <Button variant="destructive" onClick={handleReject}>
              {t("rejectResource")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function AdminOrganizations() {
  const t = useTranslations("organizations")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const { formatDate } = useDateFormatter()

  // --- List state ---
  const [orgs, setOrgs] = useState<AdminOrganization[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [search, setSearch] = useState("")
  const [isLoading, setIsLoading] = useState(true)

  // --- Debounce ref ---
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // --- Dialog states ---
  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<AdminOrganization | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AdminOrganization | null>(null)

  // --- Reviews sheet ---
  const [reviewsTarget, setReviewsTarget] = useState<AdminOrganization | null>(null)

  // --- Members sheet ---
  const [membersOrg, setMembersOrg] = useState<AdminOrganization | null>(null)
  const [members, setMembers] = useState<OrgMember[]>([])
  const [membersLoading, setMembersLoading] = useState(false)

  // --- Add member dialog ---
  const [addMemberOpen, setAddMemberOpen] = useState(false)
  const [addMemberIdentifier, setAddMemberIdentifier] = useState("")
  const [addMemberRole, setAddMemberRole] = useState("member")

  // --- Remove member confirmation ---
  const [removeMemberTarget, setRemoveMemberTarget] = useState<OrgMember | null>(null)

  // --- Change role dialog ---
  const [changeRoleTarget, setChangeRoleTarget] = useState<OrgMember | null>(null)
  const [changeRoleValue, setChangeRoleValue] = useState("")

  // --- Create form fields ---
  const [createName, setCreateName] = useState("")
  const [createDescription, setCreateDescription] = useState("")
  const [createIcon, setCreateIcon] = useState<string | null>(null)

  // --- Edit form fields ---
  const [editName, setEditName] = useState("")
  const [editDescription, setEditDescription] = useState("")
  const [editIcon, setEditIcon] = useState<string | null>(null)
  const [editReviewAgents, setEditReviewAgents] = useState(false)
  const [editReviewConnectors, setEditReviewConnectors] = useState(false)
  const [editReviewKbs, setEditReviewKbs] = useState(false)
  const [editReviewMcpServers, setEditReviewMcpServers] = useState(false)
  const [editReviewWorkflows, setEditReviewWorkflows] = useState(false)
  const [editReviewSkills, setEditReviewSkills] = useState(false)

  // --- Create review fields ---
  const [createReviewAgents, setCreateReviewAgents] = useState(false)
  const [createReviewConnectors, setCreateReviewConnectors] = useState(false)
  const [createReviewKbs, setCreateReviewKbs] = useState(false)
  const [createReviewMcpServers, setCreateReviewMcpServers] = useState(false)
  const [createReviewWorkflows, setCreateReviewWorkflows] = useState(false)
  const [createReviewSkills, setCreateReviewSkills] = useState(false)

  // --- Field errors ---
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  // --- Mutation loading ---
  const [isMutating, setIsMutating] = useState(false)

  // --- Clear field error on change ---
  const clearFieldError = (field: string) => {
    setFieldErrors((prev) => {
      if (!prev[field]) return prev
      const next = { ...prev }
      delete next[field]
      return next
    })
  }

  // --- Load organizations ---
  const loadOrgs = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await adminApi.listOrganizations(page, PAGE_SIZE, search || undefined)
      setOrgs(data.items)
      setTotal(data.total)
      setPages(Math.max(1, Math.ceil(data.total / PAGE_SIZE)))
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, search])

  useEffect(() => {
    loadOrgs()
  }, [loadOrgs])

  // --- Search with debounce ---
  const handleSearchChange = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(value)
      setPage(1)
    }, 300)
  }

  // --- Create organization ---
  const handleCreate = async () => {
    const errors: Record<string, string> = {}
    if (!createName.trim()) {
      errors.name = t("orgName") + " " + tc("required").toLowerCase()
    }
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setIsMutating(true)
    try {
      await adminApi.createOrganization({
        name: createName.trim(),
        description: createDescription.trim() || undefined,
        icon: createIcon || undefined,
        review_agents: createReviewAgents,
        review_connectors: createReviewConnectors,
        review_kbs: createReviewKbs,
        review_mcp_servers: createReviewMcpServers,
        review_workflows: createReviewWorkflows,
        review_skills: createReviewSkills,
      })
      toast.success(t("createSuccess"))
      setCreateOpen(false)
      setCreateName("")
      setCreateDescription("")
      setCreateIcon(null)
      setCreateReviewAgents(false)
      setCreateReviewConnectors(false)
      setCreateReviewKbs(false)
      setCreateReviewMcpServers(false)
      setCreateReviewWorkflows(false)
      setCreateReviewSkills(false)
      setFieldErrors({})
      await loadOrgs()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Edit organization ---
  const openEdit = (org: AdminOrganization) => {
    setEditTarget(org)
    setEditName(org.name)
    setEditDescription(org.description ?? "")
    setEditIcon(org.icon ?? null)
    setEditReviewAgents(org.review_agents ?? false)
    setEditReviewConnectors(org.review_connectors ?? false)
    setEditReviewKbs(org.review_kbs ?? false)
    setEditReviewMcpServers(org.review_mcp_servers ?? false)
    setEditReviewWorkflows(org.review_workflows ?? false)
    setEditReviewSkills(org.review_skills ?? false)
    setFieldErrors({})
  }

  const handleEdit = async () => {
    if (!editTarget) return
    const errors: Record<string, string> = {}
    if (!editName.trim()) {
      errors.name = t("orgName") + " " + tc("required").toLowerCase()
    }
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setIsMutating(true)
    try {
      await adminApi.updateOrganization(editTarget.id, {
        name: editName.trim(),
        description: editDescription.trim() || undefined,
        icon: editIcon || undefined,
        review_agents: editReviewAgents,
        review_connectors: editReviewConnectors,
        review_kbs: editReviewKbs,
        review_mcp_servers: editReviewMcpServers,
        review_workflows: editReviewWorkflows,
        review_skills: editReviewSkills,
      })
      toast.success(t("updateSuccess"))
      setEditTarget(null)
      setFieldErrors({})
      await loadOrgs()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Delete organization ---
  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsMutating(true)
    try {
      await adminApi.adminDeleteOrganization(deleteTarget.id)
      toast.success(t("deleteSuccess"))
      setDeleteTarget(null)
      await loadOrgs()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Load members ---
  const openMembers = async (org: AdminOrganization) => {
    setMembersOrg(org)
    setMembers([])
    setMembersLoading(true)
    try {
      const data = await adminApi.listOrgMembers(org.id)
      setMembers(data)
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setMembersLoading(false)
    }
  }

  const reloadMembers = async () => {
    if (!membersOrg) return
    try {
      const data = await adminApi.listOrgMembers(membersOrg.id)
      setMembers(data)
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  // --- Add member ---
  const handleAddMember = async () => {
    if (!membersOrg || !addMemberIdentifier.trim()) return
    setIsMutating(true)
    try {
      await adminApi.addOrgMember(membersOrg.id, {
        username_or_email: addMemberIdentifier.trim(),
        role: addMemberRole,
      })
      toast.success(t("memberAdded"))
      setAddMemberOpen(false)
      setAddMemberIdentifier("")
      setAddMemberRole("member")
      await reloadMembers()
      await loadOrgs()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Remove member ---
  const handleRemoveMember = async () => {
    if (!membersOrg || !removeMemberTarget) return
    setIsMutating(true)
    try {
      await adminApi.removeOrgMember(membersOrg.id, removeMemberTarget.user_id)
      toast.success(t("memberRemoved"))
      setRemoveMemberTarget(null)
      await reloadMembers()
      await loadOrgs()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Change role ---
  const openChangeRole = (member: OrgMember) => {
    setChangeRoleTarget(member)
    setChangeRoleValue(member.role)
  }

  const handleChangeRole = async () => {
    if (!membersOrg || !changeRoleTarget || !changeRoleValue) return
    if (changeRoleValue === changeRoleTarget.role) {
      setChangeRoleTarget(null)
      return
    }
    setIsMutating(true)
    try {
      await adminApi.updateOrgMemberRole(membersOrg.id, changeRoleTarget.user_id, {
        role: changeRoleValue,
      })
      toast.success(t("roleUpdated"))
      setChangeRoleTarget(null)
      await reloadMembers()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Role badge color ---
  const roleBadge = (role: string) => {
    switch (role) {
      case "owner":
        return <Badge variant="default">{t("roles.owner")}</Badge>
      case "admin":
        return <Badge variant="secondary" className="bg-blue-500/10 text-blue-600 border-blue-500/20">{t("roles.admin")}</Badge>
      default:
        return <Badge variant="outline">{t("roles.member")}</Badge>
    }
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("adminTitle")}</h2>
          <p className="text-sm text-muted-foreground">{t("adminDescription")}</p>
        </div>
        <Button onClick={() => setCreateOpen(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("createOrg")}
        </Button>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            className="pl-9"
            onChange={(e) => handleSearchChange(e.target.value)}
          />
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : orgs.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noOrgs")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full min-w-max text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("orgName")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("orgSlug")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("owner")}
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  {t("members")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {tc("status")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("reviewSettings")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("createdAt")}
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  {tc("actions")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {orgs.map((org) => (
                <tr key={org.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">
                    <div className="flex items-center gap-2">
                      <span className="flex items-center justify-center h-7 w-7 rounded-md bg-muted shrink-0 text-base leading-none">
                        {org.icon ?? <Building2 className="h-4 w-4 text-muted-foreground" />}
                      </span>
                      {org.name}
                      {org.id === MARKET_ORG_ID && (
                        <Store className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs font-mono">
                    {org.slug}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {org.owner_username || org.owner_email || "--"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {org.member_count}
                  </td>
                  <td className="px-4 py-3">
                    {org.is_active ? (
                      <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">
                        {t("active")}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="border-red-500/40 text-red-600 dark:text-red-400">
                        {t("inactive")}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {(org.review_agents || org.review_connectors || org.review_kbs || org.review_mcp_servers || org.review_workflows || org.review_skills) ? (
                      <Badge variant="outline" className="border-amber-500/40 text-amber-600 dark:text-amber-400 gap-1">
                        <ShieldCheck className="h-3 w-3" />
                        {[
                          org.review_agents && t("reviewAgentsLabel"),
                          org.review_connectors && t("reviewConnectorsLabel"),
                          org.review_kbs && t("reviewKbsLabel"),
                          org.review_mcp_servers && t("reviewMcpServersLabel"),
                          org.review_workflows && t("reviewWorkflowsLabel"),
                          org.review_skills && t("reviewSkillsLabel"),
                        ].filter(Boolean).length}
                      </Badge>
                    ) : (
                      <span className="text-muted-foreground text-xs">--</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {formatDate(org.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => openMembers(org)}>
                          <Users className="mr-2 h-4 w-4" />
                          {t("viewMembers")}
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => openEdit(org)}>
                          <Pencil className="mr-2 h-4 w-4" />
                          {tc("edit")}
                        </DropdownMenuItem>
                        {(org.review_agents || org.review_connectors || org.review_kbs || org.review_mcp_servers || org.review_workflows || org.review_skills) && (
                          <DropdownMenuItem onClick={() => setReviewsTarget(org)}>
                            <ClipboardCheck className="mr-2 h-4 w-4" />
                            {t("reviewManagement")}
                          </DropdownMenuItem>
                        )}
                        {org.id !== MARKET_ORG_ID && (
                          <>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() => setDeleteTarget(org)}
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              {t("forceDelete")}
                            </DropdownMenuItem>
                          </>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {!isLoading && orgs.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t("totalOrgs", { count: total })}</span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              {t("previous")}
            </Button>
            <span>
              {t("pageOf", { page, pages })}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            >
              {tc("next")}
            </Button>
          </div>
        </div>
      )}

      {/* --- Create Organization Dialog --- */}
      <Dialog open={createOpen} onOpenChange={(open) => { if (!open) { setCreateOpen(false); setCreateIcon(null); setCreateReviewAgents(false); setCreateReviewConnectors(false); setCreateReviewKbs(false); setCreateReviewMcpServers(false); setCreateReviewWorkflows(false); setCreateReviewSkills(false); setFieldErrors({}) } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("createTitle")}</DialogTitle>
            <DialogDescription>{t("createDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t("nameLabel")} <span className="text-destructive">*</span>
              </label>
              <Input
                value={createName}
                onChange={(e) => {
                  setCreateName(e.target.value)
                  clearFieldError("name")
                }}
                placeholder={t("namePlaceholder")}
                aria-invalid={!!fieldErrors.name}
              />
              {fieldErrors.name && (
                <p className="text-sm text-destructive">{fieldErrors.name}</p>
              )}
            </div>
            <div className="flex items-start gap-3">
              <div className="space-y-2 shrink-0">
                <label className="text-sm font-medium">{t("iconLabel")}</label>
                <EmojiPickerPopover
                  value={createIcon}
                  onChange={setCreateIcon}
                  fallbackIcon={<Building2 className="h-5 w-5" />}
                />
              </div>
              <div className="space-y-2 flex-1 min-w-0">
                <label className="text-sm font-medium">{t("descriptionLabel")}</label>
                <Textarea
                  value={createDescription}
                  onChange={(e) => setCreateDescription(e.target.value)}
                  placeholder={t("descriptionPlaceholder")}
                  rows={3}
                />
              </div>
            </div>

            {/* Review settings */}
            <Separator />
            <div className="space-y-3">
              <div className="space-y-0.5">
                <label className="text-sm font-medium">{t("reviewSettings")}</label>
                <p className="text-xs text-muted-foreground">{t("reviewSettingsDescription")}</p>
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewAgentsLabel")}</label>
                  <Switch checked={createReviewAgents} onCheckedChange={setCreateReviewAgents} />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewConnectorsLabel")}</label>
                  <Switch checked={createReviewConnectors} onCheckedChange={setCreateReviewConnectors} />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewKbsLabel")}</label>
                  <Switch checked={createReviewKbs} onCheckedChange={setCreateReviewKbs} />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewMcpServersLabel")}</label>
                  <Switch checked={createReviewMcpServers} onCheckedChange={setCreateReviewMcpServers} />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewWorkflowsLabel")}</label>
                  <Switch checked={createReviewWorkflows} onCheckedChange={setCreateReviewWorkflows} />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewSkillsLabel")}</label>
                  <Switch checked={createReviewSkills} onCheckedChange={setCreateReviewSkills} />
                </div>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setCreateOpen(false); setCreateIcon(null); setCreateReviewAgents(false); setCreateReviewConnectors(false); setCreateReviewKbs(false); setCreateReviewMcpServers(false); setCreateReviewWorkflows(false); setCreateReviewSkills(false); setFieldErrors({}) }}>
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleCreate}
              disabled={isMutating || !createName.trim()}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Edit Organization Dialog --- */}
      <Dialog
        open={editTarget !== null}
        onOpenChange={(open) => { if (!open) { setEditTarget(null); setFieldErrors({}) } }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("editTitle")}</DialogTitle>
            <DialogDescription>{t("editDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t("nameLabel")} <span className="text-destructive">*</span>
              </label>
              <Input
                value={editName}
                onChange={(e) => {
                  setEditName(e.target.value)
                  clearFieldError("name")
                }}
                placeholder={t("namePlaceholder")}
                aria-invalid={!!fieldErrors.name}
              />
              {fieldErrors.name && (
                <p className="text-sm text-destructive">{fieldErrors.name}</p>
              )}
            </div>
            <div className="flex items-start gap-3">
              <div className="space-y-2 shrink-0">
                <label className="text-sm font-medium">{t("iconLabel")}</label>
                <EmojiPickerPopover
                  value={editIcon}
                  onChange={setEditIcon}
                  fallbackIcon={<Building2 className="h-5 w-5" />}
                />
              </div>
              <div className="space-y-2 flex-1 min-w-0">
                <label className="text-sm font-medium">{t("descriptionLabel")}</label>
                <Textarea
                  value={editDescription}
                  onChange={(e) => setEditDescription(e.target.value)}
                  placeholder={t("descriptionPlaceholder")}
                  rows={3}
                />
              </div>
            </div>

            {/* Review settings */}
            <Separator />
            <div className="space-y-3">
              <div className="space-y-0.5">
                <label className="text-sm font-medium">{t("reviewSettings")}</label>
                <p className="text-xs text-muted-foreground">{t("reviewSettingsDescription")}</p>
              </div>
              {editTarget?.id === MARKET_ORG_ID && (
                <p className="text-xs text-amber-600 dark:text-amber-400">{t("marketplaceReviewLocked")}</p>
              )}
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewAgentsLabel")}</label>
                  <Switch
                    checked={editTarget?.id === MARKET_ORG_ID ? true : editReviewAgents}
                    onCheckedChange={setEditReviewAgents}
                    disabled={editTarget?.id === MARKET_ORG_ID}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewConnectorsLabel")}</label>
                  <Switch
                    checked={editTarget?.id === MARKET_ORG_ID ? true : editReviewConnectors}
                    onCheckedChange={setEditReviewConnectors}
                    disabled={editTarget?.id === MARKET_ORG_ID}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewKbsLabel")}</label>
                  <Switch
                    checked={editTarget?.id === MARKET_ORG_ID ? true : editReviewKbs}
                    onCheckedChange={setEditReviewKbs}
                    disabled={editTarget?.id === MARKET_ORG_ID}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewMcpServersLabel")}</label>
                  <Switch
                    checked={editTarget?.id === MARKET_ORG_ID ? true : editReviewMcpServers}
                    onCheckedChange={setEditReviewMcpServers}
                    disabled={editTarget?.id === MARKET_ORG_ID}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewWorkflowsLabel")}</label>
                  <Switch
                    checked={editTarget?.id === MARKET_ORG_ID ? true : editReviewWorkflows}
                    onCheckedChange={setEditReviewWorkflows}
                    disabled={editTarget?.id === MARKET_ORG_ID}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewSkillsLabel")}</label>
                  <Switch
                    checked={editTarget?.id === MARKET_ORG_ID ? true : editReviewSkills}
                    onCheckedChange={setEditReviewSkills}
                    disabled={editTarget?.id === MARKET_ORG_ID}
                  />
                </div>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setEditTarget(null); setFieldErrors({}) }}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleEdit} disabled={isMutating || !editName.trim()}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Delete Organization AlertDialog --- */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteDesc", { name: deleteTarget?.name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={handleDelete}
              disabled={isMutating}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("forceDelete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- Members Sheet --- */}
      <Sheet open={membersOrg !== null} onOpenChange={(open) => { if (!open) setMembersOrg(null) }}>
        <SheetContent className="sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{t("membersTitle")}</SheetTitle>
            <SheetDescription>
              {t("membersSubtitle", { name: membersOrg?.name ?? "", count: members.length })}
            </SheetDescription>
          </SheetHeader>

          <div className="mt-6 space-y-4">
            {/* Add member button (disabled for Market org) */}
            {membersOrg?.id === MARKET_ORG_ID ? (
              <div className="rounded-md border border-amber-400/40 bg-amber-500/5 p-3">
                <p className="text-sm text-amber-600 dark:text-amber-400">{t("cannotAddMemberToMarketOrg")}</p>
              </div>
            ) : (
              <div className="flex justify-end">
                <Button
                  size="sm"
                  className="gap-1.5"
                  onClick={() => setAddMemberOpen(true)}
                >
                  <UserPlus className="h-4 w-4" />
                  {t("addMember")}
                </Button>
              </div>
            )}

            {/* Members list */}
            {membersLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : members.length === 0 ? (
              <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
                {t("noMembers")}
              </div>
            ) : (
              <div className="rounded-md border border-border overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/40">
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                        {tc("name")}
                      </th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                        {t("role")}
                      </th>
                      <th className="px-3 py-2 text-right font-medium text-muted-foreground">
                        {tc("actions")}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {members.map((member) => (
                      <tr key={member.id} className="hover:bg-muted/20 transition-colors">
                        <td className="px-3 py-2.5">
                          <div className="font-medium text-foreground">
                            {member.display_name || member.username || member.email}
                          </div>
                          <div className="text-xs text-muted-foreground">{member.email}</div>
                        </td>
                        <td className="px-3 py-2.5">
                          {roleBadge(member.role)}
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          {member.role !== "owner" && (
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                                  <MoreHorizontal className="h-4 w-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <DropdownMenuItem onClick={() => openChangeRole(member)}>
                                  <ShieldCheck className="mr-2 h-4 w-4" />
                                  {t("changeRole")}
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                  variant="destructive"
                                  onClick={() => setRemoveMemberTarget(member)}
                                >
                                  <UserMinus className="mr-2 h-4 w-4" />
                                  {t("removeMember")}
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          )}
                          {member.role === "owner" && (
                            <Crown className="inline h-4 w-4 text-amber-500" />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>

      {/* --- Add Member Dialog --- */}
      <Dialog open={addMemberOpen} onOpenChange={(open) => { if (!open) { setAddMemberOpen(false); setAddMemberIdentifier(""); setAddMemberRole("member") } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("addMemberTitle")}</DialogTitle>
            <DialogDescription>{t("addMemberDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t("usernameOrEmail")} <span className="text-destructive">*</span>
              </label>
              <Input
                value={addMemberIdentifier}
                onChange={(e) => setAddMemberIdentifier(e.target.value)}
                placeholder={t("usernameOrEmail")}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("role")}</label>
              <Select value={addMemberRole} onValueChange={setAddMemberRole}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("selectRole")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">{t("roles.admin")}</SelectItem>
                  <SelectItem value="member">{t("roles.member")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setAddMemberOpen(false); setAddMemberIdentifier(""); setAddMemberRole("member") }}>
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleAddMember}
              disabled={isMutating || !addMemberIdentifier.trim()}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("add")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Remove Member AlertDialog --- */}
      <AlertDialog
        open={removeMemberTarget !== null}
        onOpenChange={(open) => { if (!open) setRemoveMemberTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("removeMemberTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("removeMemberDesc", { name: removeMemberTarget?.display_name || removeMemberTarget?.username || removeMemberTarget?.email || "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={handleRemoveMember}
              disabled={isMutating}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("remove")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- Reviews Sheet --- */}
      <AdminReviewsSheet
        open={reviewsTarget !== null}
        onOpenChange={(open) => { if (!open) setReviewsTarget(null) }}
        org={reviewsTarget}
      />

      {/* --- Change Role Dialog --- */}
      <Dialog
        open={changeRoleTarget !== null}
        onOpenChange={(open) => { if (!open) setChangeRoleTarget(null) }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("changeRoleTitle")}</DialogTitle>
            <DialogDescription>
              {t("changeRoleDesc", { name: changeRoleTarget?.display_name || changeRoleTarget?.username || changeRoleTarget?.email || "" })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("role")}</label>
              <Select value={changeRoleValue} onValueChange={setChangeRoleValue}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("selectRole")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">{t("roles.admin")}</SelectItem>
                  <SelectItem value="member">{t("roles.member")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setChangeRoleTarget(null)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleChangeRole} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
