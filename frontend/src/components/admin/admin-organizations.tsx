"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslations, useLocale } from "next-intl"
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
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
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
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { EmojiPickerPopover } from "@/components/ui/emoji-picker-popover"
import type { AdminOrganization, OrgMember } from "@/types/admin"

const PAGE_SIZE = 20

export function AdminOrganizations() {
  const t = useTranslations("organizations")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")
  const locale = useLocale()

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
      })
      toast.success(t("createSuccess"))
      setCreateOpen(false)
      setCreateName("")
      setCreateDescription("")
      setCreateIcon(null)
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
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {new Date(org.created_at).toLocaleDateString(locale)}
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
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() => setDeleteTarget(org)}
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          {t("forceDelete")}
                        </DropdownMenuItem>
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
      <Dialog open={createOpen} onOpenChange={(open) => { if (!open) { setCreateOpen(false); setCreateIcon(null); setFieldErrors({}) } }}>
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
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setCreateOpen(false); setCreateIcon(null); setFieldErrors({}) }}>
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
            {/* Add member button */}
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
