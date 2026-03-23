"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { useDateFormatter } from "@/hooks/use-date-formatter"
import {
  Building2,
  Store,
  MoreHorizontal,
  Plus,
  Settings,
  Trash2,
  LogOut,
  Users,
  UserMinus,
  Shield,
  ClipboardCheck,
  Check,
  X,
  ShieldCheck,
  History,
  Loader2,
  RefreshCw,
} from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/contexts/auth-context"
import { orgApi, type UserOrg, type OrgMember, type ReviewItem } from "@/lib/api"
import type { ReviewLogItem } from "@/types/admin"
import { MARKET_ORG_ID } from "@/lib/constants"
import { EmojiPickerPopover } from "@/components/ui/emoji-picker-popover"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function roleBadgeClass(role: "owner" | "admin" | "member"): string {
  switch (role) {
    case "owner":
      return "border-purple-400 text-purple-600 dark:text-purple-400"
    case "admin":
      return "border-blue-400 text-blue-600 dark:text-blue-400"
    default:
      return "border-muted-foreground/40 text-muted-foreground"
  }
}

function resourceTypeLabel(type: string, t: (key: string) => string): string {
  switch (type) {
    case "agent": return t("resourceTypeAgent")
    case "connector": return t("resourceTypeConnector")
    case "knowledge_base": return t("resourceTypeKb")
    case "mcp_server": return t("resourceTypeMcpServer")
    case "workflow": return t("resourceTypeWorkflow")
    default: return type
  }
}

// ---------------------------------------------------------------------------
// OrgFormDialog -- create / edit
// ---------------------------------------------------------------------------

interface OrgFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  initial?: UserOrg | null
  onSaved: (org: UserOrg) => void
}

function OrgFormDialog({ open, onOpenChange, initial, onSaved }: OrgFormDialogProps) {
  const t = useTranslations("organizations")
  const tc = useTranslations("common")
  const isMarketOrg = initial?.id === MARKET_ORG_ID

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [icon, setIcon] = useState<string | null>(null)
  const [reviewAgents, setReviewAgents] = useState(false)
  const [reviewConnectors, setReviewConnectors] = useState(false)
  const [reviewKbs, setReviewKbs] = useState(false)
  const [reviewMcpServers, setReviewMcpServers] = useState(false)
  const [reviewWorkflows, setReviewWorkflows] = useState(false)
  const [saving, setSaving] = useState(false)
  const [nameError, setNameError] = useState("")
  const [dirty, setDirty] = useState(false)
  const [discardOpen, setDiscardOpen] = useState(false)

  // Reset form when dialog opens / initial changes
  useEffect(() => {
    if (open) {
      setName(initial?.name ?? "")
      setDescription(initial?.description ?? "")
      setIcon(initial?.icon ?? null)
      setReviewAgents(initial?.review_agents ?? false)
      setReviewConnectors(initial?.review_connectors ?? false)
      setReviewKbs(initial?.review_kbs ?? false)
      setReviewMcpServers(initial?.review_mcp_servers ?? false)
      setReviewWorkflows(initial?.review_workflows ?? false)
      setNameError("")
      setDirty(false)
    }
  }, [open, initial])

  const handleNameChange = (val: string) => {
    setName(val)
    setDirty(true)
    setNameError("")
  }

  const handleClose = () => {
    if (dirty) {
      setDiscardOpen(true)
    } else {
      onOpenChange(false)
    }
  }

  const handleSubmit = async () => {
    if (!name.trim()) {
      setNameError(t("nameRequired"))
      return
    }
    setSaving(true)
    try {
      let saved: UserOrg
      if (initial) {
        saved = await orgApi.update(initial.id, {
          name: name.trim(),
          description: description.trim() || null,
          icon: icon || null,
          review_agents: reviewAgents,
          review_connectors: reviewConnectors,
          review_kbs: reviewKbs,
          review_mcp_servers: reviewMcpServers,
          review_workflows: reviewWorkflows,
        })
        toast.success(t("orgUpdated"))
      } else {
        saved = await orgApi.create({
          name: name.trim(),
          description: description.trim() || null,
          icon: icon || null,
          review_agents: reviewAgents,
          review_connectors: reviewConnectors,
          review_kbs: reviewKbs,
          review_mcp_servers: reviewMcpServers,
          review_workflows: reviewWorkflows,
        })
        toast.success(t("orgCreated", { name: saved.name }))
      }
      onSaved(saved)
      onOpenChange(false)
    } catch (err: unknown) {
      const msg = (err as { message?: string })?.message ?? ""
      toast.error(initial ? t("updateFailed") : t("createFailed") + (msg ? `: ${msg}` : ""))
    } finally {
      setSaving(false)
    }
  }

  const isEdit = !!initial

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(v) => {
          if (!v) handleClose()
          else onOpenChange(true)
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {isEdit ? t("editDialogTitle") : t("createDialogTitle")}
            </DialogTitle>
            <DialogDescription className="sr-only">
              {isEdit ? t("editDialogTitle") : t("createDialogTitle")}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            {/* Name */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">
                {t("nameLabel")} <span className="text-destructive">*</span>
              </label>
              <Input
                value={name}
                onChange={(e) => handleNameChange(e.target.value)}
                placeholder={t("namePlaceholder")}
                aria-invalid={!!nameError}
              />
              {nameError && (
                <p className="text-sm text-destructive">{nameError}</p>
              )}
            </div>

            {/* Icon + Description row */}
            <div className="flex items-start gap-3">
              <div className="space-y-1.5 shrink-0">
                <label className="text-sm font-medium">{t("iconLabel")}</label>
                <EmojiPickerPopover
                  value={icon}
                  onChange={(val) => { setIcon(val); setDirty(true) }}
                  fallbackIcon={<Building2 className="h-5 w-5" />}
                />
              </div>
              <div className="space-y-1.5 flex-1 min-w-0">
                <label className="text-sm font-medium">{t("descriptionLabel")}</label>
                <Textarea
                  value={description}
                  onChange={(e) => { setDescription(e.target.value); setDirty(true) }}
                  placeholder={t("descriptionPlaceholder")}
                  rows={3}
                  className="resize-none"
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
              {isMarketOrg && (
                <p className="text-xs text-amber-600 dark:text-amber-400">{t("marketplaceReviewLocked")}</p>
              )}
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewAgentsLabel")}</label>
                  <Switch
                    checked={isMarketOrg ? true : reviewAgents}
                    onCheckedChange={(v) => { setReviewAgents(v); setDirty(true) }}
                    disabled={isMarketOrg}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewConnectorsLabel")}</label>
                  <Switch
                    checked={isMarketOrg ? true : reviewConnectors}
                    onCheckedChange={(v) => { setReviewConnectors(v); setDirty(true) }}
                    disabled={isMarketOrg}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewKbsLabel")}</label>
                  <Switch
                    checked={isMarketOrg ? true : reviewKbs}
                    onCheckedChange={(v) => { setReviewKbs(v); setDirty(true) }}
                    disabled={isMarketOrg}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewMcpServersLabel")}</label>
                  <Switch
                    checked={isMarketOrg ? true : reviewMcpServers}
                    onCheckedChange={(v) => { setReviewMcpServers(v); setDirty(true) }}
                    disabled={isMarketOrg}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <label className="text-sm">{t("reviewWorkflowsLabel")}</label>
                  <Switch
                    checked={isMarketOrg ? true : reviewWorkflows}
                    onCheckedChange={(v) => { setReviewWorkflows(v); setDirty(true) }}
                    disabled={isMarketOrg}
                  />
                </div>
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={handleClose} disabled={saving}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleSubmit} disabled={saving}>
              {saving ? tc("saving") : isEdit ? tc("save") : tc("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Discard confirmation -- sibling, never nested */}
      <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("discardTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("discardDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setDiscardOpen(false)
                onOpenChange(false)
              }}
            >
              {t("discardConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// ---------------------------------------------------------------------------
// MembersSheet
// ---------------------------------------------------------------------------

interface MembersSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  org: UserOrg
  currentUserId: string
}

function MembersSheet({ open, onOpenChange, org, currentUserId }: MembersSheetProps) {
  const t = useTranslations("organizations")
  const tc = useTranslations("common")

  const [members, setMembers] = useState<OrgMember[]>([])
  const [loading, setLoading] = useState(false)
  const [usernameOrEmail, setUsernameOrEmail] = useState("")
  const [addRole, setAddRole] = useState<string>("member")
  const [addError, setAddError] = useState("")
  const [adding, setAdding] = useState(false)
  const [removeTarget, setRemoveTarget] = useState<OrgMember | null>(null)

  const isMarketOrg = org.id === MARKET_ORG_ID
  const myRole = org.role
  const canManage = myRole === "owner" || myRole === "admin"

  const loadMembers = useCallback(async () => {
    setLoading(true)
    try {
      const data = await orgApi.listMembers(org.id)
      setMembers(data)
    } catch {
      toast.error(t("membersLoadFailed"))
    } finally {
      setLoading(false)
    }
  }, [org.id, t])

  useEffect(() => {
    if (open) {
      loadMembers()
      setUsernameOrEmail("")
      setAddError("")
      setAddRole("member")
    }
  }, [open, loadMembers])

  const handleAdd = async () => {
    if (!usernameOrEmail.trim()) {
      setAddError(t("usernameOrEmailRequired"))
      return
    }
    setAdding(true)
    try {
      await orgApi.addMember(org.id, {
        username_or_email: usernameOrEmail.trim(),
        role: addRole,
      })
      toast.success(t("memberAdded"))
      setUsernameOrEmail("")
      setAddRole("member")
      setAddError("")
      await loadMembers()
    } catch (err: unknown) {
      const msg = (err as { message?: string })?.message ?? t("addMemberFailed")
      toast.error(msg)
    } finally {
      setAdding(false)
    }
  }

  const handleChangeRole = async (member: OrgMember, newRole: string) => {
    try {
      const updated = await orgApi.changeRole(org.id, member.user_id, newRole)
      setMembers((prev) =>
        prev.map((m) => (m.user_id === member.user_id ? { ...m, role: updated.role } : m)),
      )
      toast.success(t("roleChanged"))
    } catch {
      toast.error(t("changeRoleFailed"))
    }
  }

  const handleRemove = async () => {
    if (!removeTarget) return
    try {
      await orgApi.removeMember(org.id, removeTarget.user_id)
      setMembers((prev) => prev.filter((m) => m.user_id !== removeTarget.user_id))
      toast.success(t("memberRemoved"))
    } catch {
      toast.error(t("removeMemberFailed"))
    } finally {
      setRemoveTarget(null)
    }
  }

  // Determine which roles the current user can assign to a specific member
  const assignableRoles = (targetRole: "owner" | "admin" | "member"): string[] => {
    if (myRole === "owner") {
      // Owner can set admin or member (cannot set owner via this UI)
      return ["admin", "member"].filter((r) => r !== targetRole)
    }
    if (myRole === "admin") {
      // Admin can only set member (cannot touch owner or other admins)
      return targetRole !== "member" && targetRole !== "owner" ? ["member"] : []
    }
    return []
  }

  const displayName = (m: OrgMember) =>
    m.display_name ?? m.username ?? m.email ?? m.user_id

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className="w-full sm:max-w-lg flex flex-col">
          <SheetHeader className="shrink-0">
            <SheetTitle>{t("membersSheetTitle", { name: org.name })}</SheetTitle>
            <SheetDescription>{t("membersSheetDescription")}</SheetDescription>
          </SheetHeader>

          <div className="flex-1 overflow-y-auto space-y-4 mt-4">
            {/* Add member form */}
            {canManage && isMarketOrg && (
              <div className="rounded-md border border-amber-400/40 bg-amber-500/5 p-3">
                <p className="text-sm text-amber-600 dark:text-amber-400">{t("cannotAddMemberToMarketOrg")}</p>
              </div>
            )}
            {canManage && !isMarketOrg && (
              <div className="space-y-2">
                <p className="text-sm font-medium">{t("addMemberTitle")}</p>
                <div className="flex gap-2">
                  <div className="flex-1 space-y-1">
                    <Input
                      value={usernameOrEmail}
                      onChange={(e) => {
                        setUsernameOrEmail(e.target.value)
                        setAddError("")
                      }}
                      placeholder={t("usernameOrEmailPlaceholder")}
                      aria-invalid={!!addError}
                    />
                    {addError && (
                      <p className="text-sm text-destructive">{addError}</p>
                    )}
                  </div>
                  <Select value={addRole} onValueChange={setAddRole}>
                    <SelectTrigger className="w-[110px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="admin">{t("roleAdmin")}</SelectItem>
                      <SelectItem value="member">{t("roleMember")}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button onClick={handleAdd} disabled={adding} size="sm">
                    {adding ? t("adding") : t("addMemberButton")}
                  </Button>
                </div>
                <Separator className="mt-2" />
              </div>
            )}

            {/* Member list */}
            {loading ? (
              <p className="text-sm text-muted-foreground py-4 text-center">{tc("loading")}</p>
            ) : members.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">{t("noMembers")}</p>
            ) : (
              <div className="space-y-2">
                {members.map((member) => {
                  const isSelf = member.user_id === currentUserId
                  const rolesCanAssign = assignableRoles(member.role)
                  const canRemove = canManage && !isSelf && member.role !== "owner"
                  const canChangeRole = canManage && !isSelf && rolesCanAssign.length > 0 && member.role !== "owner"

                  return (
                    <div
                      key={member.user_id}
                      className="flex items-center justify-between py-2 px-1 rounded-md hover:bg-accent/50 transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center text-sm font-medium shrink-0">
                          {displayName(member).charAt(0).toUpperCase()}
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium truncate">
                            {displayName(member)}
                            {isSelf && (
                              <Badge variant="secondary" className="ml-1 text-[10px] px-1.5 py-0 font-normal align-middle">{tc("you")}</Badge>
                            )}
                          </p>
                          {member.email && (
                            <p className="text-xs text-muted-foreground truncate">{member.email}</p>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <Badge variant="outline" className={roleBadgeClass(member.role)}>
                          {t(`role${member.role.charAt(0).toUpperCase()}${member.role.slice(1)}` as "roleOwner" | "roleAdmin" | "roleMember")}
                        </Badge>

                        {(canChangeRole || canRemove) && (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 p-0"
                              >
                                <MoreHorizontal className="h-4 w-4" />
                                <span className="sr-only">{tc("actions")}</span>
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              {canChangeRole &&
                                rolesCanAssign.map((r) => (
                                  <DropdownMenuItem
                                    key={r}
                                    onClick={() => handleChangeRole(member, r)}
                                  >
                                    <Shield className="mr-2 h-4 w-4" />
                                    {t("changeRole")}: {t(`role${r.charAt(0).toUpperCase()}${r.slice(1)}` as "roleOwner" | "roleAdmin" | "roleMember")}
                                  </DropdownMenuItem>
                                ))}
                              {canChangeRole && canRemove && <DropdownMenuSeparator />}
                              {canRemove && (
                                <DropdownMenuItem
                                  variant="destructive"
                                  onClick={() => setRemoveTarget(member)}
                                >
                                  <UserMinus className="mr-2 h-4 w-4" />
                                  {t("removeMember")}
                                </DropdownMenuItem>
                              )}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>

      {/* Remove member confirmation */}
      <AlertDialog open={!!removeTarget} onOpenChange={(v) => { if (!v) setRemoveTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("removeMemberTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget
                ? t("removeMemberDescription", {
                    username: displayName(removeTarget),
                  })
                : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleRemove}>
              {t("removeMemberConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// ---------------------------------------------------------------------------
// ReviewsSheet -- admin/owner review management
// ---------------------------------------------------------------------------

interface ReviewsSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  org: UserOrg
}

function ReviewsSheet({ open, onOpenChange, org }: ReviewsSheetProps) {
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
  }, [org.id, resourceTypeFilter, statusFilter, t])

  useEffect(() => {
    if (open) {
      loadReviews()
    }
  }, [open, loadReviews])

  const handleApprove = async (item: ReviewItem) => {
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
    if (!rejectTarget) return
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
            {org.id === MARKET_ORG_ID && (
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

      {/* Reject confirmation dialog -- sibling */}
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

// ---------------------------------------------------------------------------
// ReviewHistorySheet -- audit trail of review decisions
// ---------------------------------------------------------------------------

const REVIEW_HISTORY_PAGE_SIZE = 50

const HISTORY_REVIEW_ACTIONS = ["submitted", "approved", "rejected", "unpublished", "resubmitted"] as const
const HISTORY_REVIEW_RESOURCE_TYPES = ["agent", "connector", "kb", "mcp_server"] as const

type HistoryReviewAction = (typeof HISTORY_REVIEW_ACTIONS)[number]

// Maps review action values to organizations.json i18n keys
const HISTORY_ACTION_I18N_KEY: Record<HistoryReviewAction, string> = {
  submitted: "pendingReview",
  approved: "approved",
  rejected: "rejected",
  unpublished: "reviewHistoryActionUnpublished",
  resubmitted: "reviewHistoryActionResubmitted",
}

const HISTORY_ACTION_BADGE: Record<HistoryReviewAction, string> = {
  approved: "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
  rejected: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
  submitted: "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30",
  resubmitted: "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30",
  unpublished: "bg-muted text-muted-foreground border-border",
}

function historyActionBadgeClass(action: string): string {
  return HISTORY_ACTION_BADGE[action as HistoryReviewAction] ?? "bg-muted text-muted-foreground border-border"
}

interface ReviewHistorySheetProps {
  open: boolean
  onClose: () => void
  orgs: UserOrg[]
  initialOrgId: string
}

function ReviewHistorySheet({ open, onClose, orgs, initialOrgId }: ReviewHistorySheetProps) {
  const t = useTranslations("organizations")
  const tc = useTranslations("common")
  const { formatDateTime } = useDateFormatter()

  const [selectedOrgId, setSelectedOrgId] = useState(initialOrgId)
  const [items, setItems] = useState<ReviewLogItem[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [resourceTypeFilter, setResourceTypeFilter] = useState("__all__")
  const [actionFilter, setActionFilter] = useState("__all__")
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)

  const page = Math.floor(offset / REVIEW_HISTORY_PAGE_SIZE) + 1
  const pages = Math.max(1, Math.ceil(total / REVIEW_HISTORY_PAGE_SIZE))

  // Sync selectedOrgId when initialOrgId changes (sheet re-opened for different org)
  useEffect(() => {
    if (open) {
      setSelectedOrgId(initialOrgId)
      setResourceTypeFilter("__all__")
      setActionFilter("__all__")
      setOffset(0)
    }
  }, [open, initialOrgId])

  const load = useCallback(async () => {
    if (!selectedOrgId) return
    setIsLoading(true)
    try {
      const params: { resource_type?: string; action?: string; limit: number; offset: number } = {
        limit: REVIEW_HISTORY_PAGE_SIZE,
        offset,
      }
      if (resourceTypeFilter !== "__all__") params.resource_type = resourceTypeFilter
      if (actionFilter !== "__all__") params.action = actionFilter
      const data = await orgApi.listReviewLog(selectedOrgId, params)
      // Backend wraps in { data: [...] } but listReviewLog already unwraps it
      // However the endpoint might return a paginated shape — treat as array for now
      if (Array.isArray(data)) {
        setItems(data)
        setTotal(data.length < REVIEW_HISTORY_PAGE_SIZE && offset === 0 ? data.length : offset + data.length)
      } else {
        setItems([])
        setTotal(0)
      }
    } catch {
      toast.error(t("reviewHistoryLoadFailed"))
    } finally {
      setIsLoading(false)
    }
  }, [selectedOrgId, resourceTypeFilter, actionFilter, offset, t])

  useEffect(() => {
    if (open && selectedOrgId) {
      load()
    }
  }, [open, load, selectedOrgId])

  const handleOrgChange = (val: string) => {
    setSelectedOrgId(val)
    setOffset(0)
  }

  const handleResourceTypeChange = (val: string) => {
    setResourceTypeFilter(val)
    setOffset(0)
  }

  const handleActionChange = (val: string) => {
    setActionFilter(val)
    setOffset(0)
  }

  return (
    <Sheet open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
        <SheetHeader className="mb-4">
          <SheetTitle>{t("reviewHistoryTitle")}</SheetTitle>
          <SheetDescription>{t("reviewHistoryDescription")}</SheetDescription>
        </SheetHeader>

        <div className="space-y-4">
          {/* Org selector */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">{t("reviewHistorySelectOrg")}</label>
            <Select value={selectedOrgId} onValueChange={handleOrgChange}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {orgs.map((org) => (
                  <SelectItem key={org.id} value={org.id}>
                    {org.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Filter bar */}
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">{t("reviewHistoryColType")}</label>
              <Select value={resourceTypeFilter} onValueChange={handleResourceTypeChange}>
                <SelectTrigger className="w-[160px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">{t("filterAll")}</SelectItem>
                  {HISTORY_REVIEW_RESOURCE_TYPES.map((rt) => (
                    <SelectItem key={rt} value={rt}>
                      {resourceTypeLabel(rt, (key) => t(key as Parameters<typeof t>[0]))}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">{t("reviewHistoryColAction")}</label>
              <Select value={actionFilter} onValueChange={handleActionChange}>
                <SelectTrigger className="w-[160px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">{t("filterAll")}</SelectItem>
                  {HISTORY_REVIEW_ACTIONS.map((a) => (
                    <SelectItem key={a} value={a}>
                      {t(HISTORY_ACTION_I18N_KEY[a] as Parameters<typeof t>[0])}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Button variant="outline" size="sm" onClick={load} disabled={isLoading} className="h-9 self-end">
              <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isLoading ? "animate-spin" : ""}`} />
              {tc("refresh")}
            </Button>
          </div>

          {/* Table */}
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : items.length === 0 ? (
            <div className="rounded-md border border-border bg-muted/30 p-6 text-sm text-muted-foreground text-center">
              {t("reviewHistoryEmpty")}
            </div>
          ) : (
            <>
              <div className="rounded-md border border-border overflow-x-auto">
                <table className="w-full min-w-max text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/40">
                      <th className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">{t("reviewHistoryColTime")}</th>
                      <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("reviewHistoryColType")}</th>
                      <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("reviewHistoryColResource")}</th>
                      <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">{t("reviewHistoryColAction")}</th>
                      <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">{t("reviewHistoryColActor")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {items.map((entry) => (
                      <tr key={entry.id} className="hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                          {formatDateTime(entry.created_at)}
                        </td>
                        <td className="px-4 py-2.5 text-xs text-muted-foreground">
                          {resourceTypeLabel(entry.resource_type, (key) => t(key as Parameters<typeof t>[0]))}
                        </td>
                        <td className="px-4 py-2.5 text-xs">
                          {entry.resource_name
                            ? <span className="font-medium text-foreground">{entry.resource_name}</span>
                            : <span className="font-mono text-muted-foreground">{entry.resource_id.slice(0, 8)}</span>}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${historyActionBadgeClass(entry.action)}`}>
                            {HISTORY_ACTION_I18N_KEY[entry.action as HistoryReviewAction]
                              ? t(HISTORY_ACTION_I18N_KEY[entry.action as HistoryReviewAction] as Parameters<typeof t>[0])
                              : entry.action}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-xs text-muted-foreground text-right">
                          {entry.actor_name ?? <span className="text-muted-foreground/50">&mdash;</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between text-sm text-muted-foreground">
                <span>{total}</span>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={offset === 0}
                    onClick={() => setOffset((o) => Math.max(0, o - REVIEW_HISTORY_PAGE_SIZE))}
                  >
                    {t("previous")}
                  </Button>
                  <span>{t("pageOf", { page, pages })}</span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={items.length < REVIEW_HISTORY_PAGE_SIZE}
                    onClick={() => setOffset((o) => o + REVIEW_HISTORY_PAGE_SIZE)}
                  >
                    {tc("next")}
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

// ---------------------------------------------------------------------------
// OrgCard
// ---------------------------------------------------------------------------

interface OrgCardProps {
  org: UserOrg
  currentUserId: string
  onEdit: (org: UserOrg) => void
  onDelete: (org: UserOrg) => void
  onLeave: (org: UserOrg) => void
  onManageMembers: (org: UserOrg) => void
  onManageReviews: (org: UserOrg) => void
  onViewReviewHistory: (org: UserOrg) => void
}

function OrgCard({ org, onEdit, onDelete, onLeave, onManageMembers, onManageReviews, onViewReviewHistory }: OrgCardProps) {
  const t = useTranslations("organizations")
  const tc = useTranslations("common")

  const isPlatform = org.id === MARKET_ORG_ID
  const isOwner = org.role === "owner"
  const isAdminOrOwner = org.role === "owner" || org.role === "admin"
  const canLeave = !isOwner

  // --- Platform org: special card ---
  if (isPlatform) {
    return (
      <div className="rounded-lg border border-primary/20 bg-gradient-to-r from-primary/[0.04] to-transparent p-4 flex items-start gap-3">
        <div className="h-10 w-10 rounded-md bg-primary/10 flex items-center justify-center shrink-0">
          <Store className="h-5 w-5 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm font-semibold truncate">{t("marketOrgName")}</p>
            <Badge variant="outline" className="border-primary/30 text-primary text-[10px] px-1.5 py-0">
              {t(`role${org.role.charAt(0).toUpperCase()}${org.role.slice(1)}` as "roleOwner" | "roleAdmin" | "roleMember")}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-1">{t("marketOrgDescription")}</p>
        </div>
        {/* Only admin/owner see the menu — limited to member management */}
        {isAdminOrOwner && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-7 w-7 p-0 shrink-0">
                <MoreHorizontal className="h-4 w-4" />
                <span className="sr-only">{tc("actions")}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {isOwner && (
                <DropdownMenuItem onClick={() => onEdit(org)}>
                  <Settings className="mr-2 h-4 w-4" />
                  {tc("edit")}
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => onManageMembers(org)}>
                <Users className="mr-2 h-4 w-4" />
                {t("manageMembers")}
              </DropdownMenuItem>
              {(org.review_agents || org.review_connectors || org.review_kbs || org.review_mcp_servers || org.review_workflows) && (
                <DropdownMenuItem onClick={() => onManageReviews(org)}>
                  <ClipboardCheck className="mr-2 h-4 w-4" />
                  {t("reviewManagement")}
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => onViewReviewHistory(org)}>
                <History className="mr-2 h-4 w-4" />
                {t("reviewHistory")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    )
  }

  // --- Regular org card ---
  return (
    <div className="rounded-lg border border-border bg-card p-4 flex items-start justify-between gap-3">
      <div className="flex items-start gap-3 min-w-0">
        {/* Icon */}
        <div className="h-10 w-10 rounded-md bg-muted flex items-center justify-center text-lg shrink-0">
          {org.icon ?? <Building2 className="h-5 w-5 text-muted-foreground" />}
        </div>

        {/* Info */}
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm font-semibold truncate">{org.name}</p>
            <Badge variant="outline" className={roleBadgeClass(org.role)}>
              {t(`role${org.role.charAt(0).toUpperCase()}${org.role.slice(1)}` as "roleOwner" | "roleAdmin" | "roleMember")}
            </Badge>
            {(org.review_agents || org.review_connectors || org.review_kbs || org.review_mcp_servers || org.review_workflows) && (
              <Badge variant="outline" className="border-amber-400/40 text-amber-600 dark:text-amber-400 text-[10px] px-1.5 py-0 h-5 gap-0.5">
                <ShieldCheck className="h-3 w-3" />
                {[org.review_agents, org.review_connectors, org.review_kbs, org.review_mcp_servers, org.review_workflows].filter(Boolean).length}
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">/{org.slug}</p>
          {org.description && (
            <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{org.description}</p>
          )}
          <p className="text-xs text-muted-foreground mt-1">
            {t(
              (org.member_count ?? 0) === 1 ? "memberCountOne" : "memberCount",
              { count: org.member_count ?? 0 }
            )}
          </p>
        </div>
      </div>

      {/* Actions */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="h-7 w-7 p-0 shrink-0">
            <MoreHorizontal className="h-4 w-4" />
            <span className="sr-only">{tc("actions")}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {isAdminOrOwner && (
            <DropdownMenuItem onClick={() => onManageMembers(org)}>
              <Users className="mr-2 h-4 w-4" />
              {t("manageMembers")}
            </DropdownMenuItem>
          )}
          {isAdminOrOwner && (org.review_agents || org.review_connectors || org.review_kbs || org.review_mcp_servers || org.review_workflows) && (
            <DropdownMenuItem onClick={() => onManageReviews(org)}>
              <ClipboardCheck className="mr-2 h-4 w-4" />
              {t("reviewManagement")}
            </DropdownMenuItem>
          )}
          {isAdminOrOwner && (
            <DropdownMenuItem onClick={() => onViewReviewHistory(org)}>
              <History className="mr-2 h-4 w-4" />
              {t("reviewHistory")}
            </DropdownMenuItem>
          )}
          {isAdminOrOwner && (
            <DropdownMenuItem onClick={() => onEdit(org)}>
              <Settings className="mr-2 h-4 w-4" />
              {tc("edit")}
            </DropdownMenuItem>
          )}
          {canLeave && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onClick={() => onLeave(org)}
              >
                <LogOut className="mr-2 h-4 w-4" />
                {t("leaveOrganization")}
              </DropdownMenuItem>
            </>
          )}
          {isOwner && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onClick={() => onDelete(org)}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                {tc("delete")}
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

// ---------------------------------------------------------------------------
// OrganizationSettings (main export)
// ---------------------------------------------------------------------------

export function OrganizationSettings() {
  const t = useTranslations("organizations")
  const tc = useTranslations("common")
  const { user } = useAuth()

  const [orgs, setOrgs] = useState<UserOrg[]>([])
  const [loading, setLoading] = useState(true)

  // Dialogs / Sheets
  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<UserOrg | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<UserOrg | null>(null)
  const [leaveTarget, setLeaveTarget] = useState<UserOrg | null>(null)
  const [membersTarget, setMembersTarget] = useState<UserOrg | null>(null)
  const [membersOpen, setMembersOpen] = useState(false)
  const [reviewsTarget, setReviewsTarget] = useState<UserOrg | null>(null)
  const [reviewsOpen, setReviewsOpen] = useState(false)
  const [reviewHistoryTarget, setReviewHistoryTarget] = useState<UserOrg | null>(null)
  const [reviewHistoryOpen, setReviewHistoryOpen] = useState(false)

  const loadOrgs = useCallback(async () => {
    setLoading(true)
    try {
      const data = await orgApi.list()
      setOrgs(data)
    } catch {
      toast.error(t("loadFailed"))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    loadOrgs()
  }, [loadOrgs])

  const handleCreated = (org: UserOrg) => {
    setOrgs((prev) => [org, ...prev])
  }

  const handleEdited = (updated: UserOrg) => {
    setOrgs((prev) => prev.map((o) => (o.id === updated.id ? updated : o)))
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await orgApi.delete(deleteTarget.id)
      toast.success(t("orgDeleted", { name: deleteTarget.name }))
      setOrgs((prev) => prev.filter((o) => o.id !== deleteTarget.id))
    } catch {
      toast.error(t("deleteFailed"))
    } finally {
      setDeleteTarget(null)
    }
  }

  const handleLeave = async () => {
    if (!leaveTarget || !user) return
    try {
      await orgApi.removeMember(leaveTarget.id, user.id)
      toast.success(t("leftOrganization", { name: leaveTarget.name }))
      setOrgs((prev) => prev.filter((o) => o.id !== leaveTarget.id))
    } catch {
      toast.error(t("leaveFailed"))
    } finally {
      setLeaveTarget(null)
    }
  }

  const handleManageMembers = (org: UserOrg) => {
    setMembersTarget(org)
    setMembersOpen(true)
  }

  const handleManageReviews = (org: UserOrg) => {
    setReviewsTarget(org)
    setReviewsOpen(true)
  }

  const handleViewReviewHistory = (org: UserOrg) => {
    setReviewHistoryTarget(org)
    setReviewHistoryOpen(true)
  }

  const adminOrOwnerOrgs = orgs.filter((o) => o.role === "owner" || o.role === "admin")

  return (
    <div className="space-y-6">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-medium">{t("myOrganizations")}</h3>
          <p className="text-sm text-muted-foreground">{t("description")}</p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="mr-2 h-4 w-4" />
          {t("createOrganization")}
        </Button>
      </div>

      {/* List */}
      {loading ? (
        <p className="text-sm text-muted-foreground py-8 text-center">{tc("loading")}</p>
      ) : orgs.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
          <Building2 className="h-12 w-12 text-muted-foreground/40" />
          <p className="text-sm font-medium">{t("noOrganizations")}</p>
          <p className="text-xs text-muted-foreground max-w-xs">{t("noOrganizationsHint")}</p>
          <Button size="sm" variant="outline" onClick={() => setCreateOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            {t("createOrganization")}
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          {orgs.map((org) => (
            <OrgCard
              key={org.id}
              org={org}
              currentUserId={user?.id ?? ""}
              onEdit={(o) => { setEditTarget(o); setEditOpen(true) }}
              onDelete={(o) => setDeleteTarget(o)}
              onLeave={(o) => setLeaveTarget(o)}
              onManageMembers={handleManageMembers}
              onManageReviews={handleManageReviews}
              onViewReviewHistory={handleViewReviewHistory}
            />
          ))}
        </div>
      )}

      {/* Create dialog */}
      <OrgFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        initial={null}
        onSaved={handleCreated}
      />

      {/* Edit dialog */}
      <OrgFormDialog
        open={editOpen}
        onOpenChange={(v) => { setEditOpen(v); if (!v) setEditTarget(null) }}
        initial={editTarget}
        onSaved={handleEdited}
      />

      {/* Delete confirmation */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(v) => { if (!v) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteOrgTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget ? t("deleteOrgDescription", { name: deleteTarget.name }) : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete}>
              {t("deleteOrgConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Leave confirmation */}
      <AlertDialog open={!!leaveTarget} onOpenChange={(v) => { if (!v) setLeaveTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("leaveOrgTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {leaveTarget ? t("leaveOrgDescription", { name: leaveTarget.name }) : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleLeave}>
              {t("leaveOrgConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Members sheet */}
      {membersTarget && (
        <MembersSheet
          open={membersOpen}
          onOpenChange={(v) => { setMembersOpen(v); if (!v) setMembersTarget(null) }}
          org={membersTarget}
          currentUserId={user?.id ?? ""}
        />
      )}

      {/* Reviews sheet */}
      {reviewsTarget && (
        <ReviewsSheet
          open={reviewsOpen}
          onOpenChange={(v) => { setReviewsOpen(v); if (!v) setReviewsTarget(null) }}
          org={reviewsTarget}
        />
      )}

      {/* Review history sheet */}
      {adminOrOwnerOrgs.length > 0 && (
        <ReviewHistorySheet
          open={reviewHistoryOpen}
          onClose={() => { setReviewHistoryOpen(false); setReviewHistoryTarget(null) }}
          orgs={adminOrOwnerOrgs}
          initialOrgId={reviewHistoryTarget?.id ?? adminOrOwnerOrgs[0]?.id ?? ""}
        />
      )}
    </div>
  )
}
