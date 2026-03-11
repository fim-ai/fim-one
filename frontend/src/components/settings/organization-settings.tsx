"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import {
  Building2,
  MoreHorizontal,
  Plus,
  Settings,
  Trash2,
  LogOut,
  Users,
  UserMinus,
  Shield,
} from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
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
import { orgApi, type UserOrg, type OrgMember } from "@/lib/api"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generateSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
}

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

// ---------------------------------------------------------------------------
// OrgFormDialog — create / edit
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

  const [name, setName] = useState("")
  const [slug, setSlug] = useState("")
  const [description, setDescription] = useState("")
  const [icon, setIcon] = useState("")
  const [slugEdited, setSlugEdited] = useState(false)
  const [saving, setSaving] = useState(false)
  const [nameError, setNameError] = useState("")
  const [dirty, setDirty] = useState(false)
  const [discardOpen, setDiscardOpen] = useState(false)

  // Reset form when dialog opens / initial changes
  useEffect(() => {
    if (open) {
      setName(initial?.name ?? "")
      setSlug(initial?.slug ?? "")
      setDescription(initial?.description ?? "")
      setIcon(initial?.icon ?? "")
      setSlugEdited(!!initial)
      setNameError("")
      setDirty(false)
    }
  }, [open, initial])

  const handleNameChange = (val: string) => {
    setName(val)
    setDirty(true)
    setNameError("")
    if (!slugEdited) {
      setSlug(generateSlug(val))
    }
  }

  const handleSlugChange = (val: string) => {
    setSlug(val)
    setSlugEdited(true)
    setDirty(true)
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
          icon: icon.trim() || null,
        })
        toast.success(t("orgUpdated"))
      } else {
        saved = await orgApi.create({
          name: name.trim(),
          slug: slug || generateSlug(name.trim()),
          description: description.trim() || null,
          icon: icon.trim() || null,
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

            {/* Slug — only shown on create */}
            {!isEdit && (
              <div className="space-y-1.5">
                <label className="text-sm font-medium">{t("slugLabel")}</label>
                <Input
                  value={slug}
                  onChange={(e) => handleSlugChange(e.target.value)}
                  placeholder={t("slugPlaceholder")}
                />
                <p className="text-xs text-muted-foreground">{t("slugHint")}</p>
              </div>
            )}

            {/* Description */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">{t("descriptionLabel")}</label>
              <Textarea
                value={description}
                onChange={(e) => { setDescription(e.target.value); setDirty(true) }}
                placeholder={t("descriptionPlaceholder")}
                rows={3}
                className="resize-none"
              />
            </div>

            {/* Icon */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">{t("iconLabel")}</label>
              <Input
                value={icon}
                onChange={(e) => { setIcon(e.target.value); setDirty(true) }}
                placeholder={t("iconPlaceholder")}
                className="max-w-[100px]"
              />
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

      {/* Discard confirmation — sibling, never nested */}
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

  const myRole = org.my_role
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
            {canManage && (
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
                              <span className="ml-1 text-xs text-muted-foreground">(you)</span>
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
// OrgCard
// ---------------------------------------------------------------------------

interface OrgCardProps {
  org: UserOrg
  currentUserId: string
  onEdit: (org: UserOrg) => void
  onDelete: (org: UserOrg) => void
  onLeave: (org: UserOrg) => void
  onManageMembers: (org: UserOrg) => void
}

function OrgCard({ org, currentUserId, onEdit, onDelete, onLeave, onManageMembers }: OrgCardProps) {
  const t = useTranslations("organizations")
  const tc = useTranslations("common")

  const isOwner = org.my_role === "owner"
  const isAdminOrOwner = org.my_role === "owner" || org.my_role === "admin"
  const canLeave = !isOwner

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
            <Badge variant="outline" className={roleBadgeClass(org.my_role)}>
              {t(`role${org.my_role.charAt(0).toUpperCase()}${org.my_role.slice(1)}` as "roleOwner" | "roleAdmin" | "roleMember")}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">/{org.slug}</p>
          {org.description && (
            <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{org.description}</p>
          )}
          <p className="text-xs text-muted-foreground mt-1">
            {org.member_count === 1
              ? t("memberCountOne")
              : t("memberCount", { count: org.member_count })}
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
          {isOwner && (
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
    </div>
  )
}
