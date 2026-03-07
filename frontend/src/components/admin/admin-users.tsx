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
  KeyRound,
  ShieldCheck,
  ShieldOff,
  UserCheck,
  UserX,
  Trash2,
  LogOut,
  Gauge,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
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
import { Label } from "@/components/ui/label"
import { adminApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { useAuth } from "@/contexts/auth-context"
import type { AdminUser } from "@/types/admin"

export function AdminUsers() {
  const { user: currentUser } = useAuth()
  const t = useTranslations("admin.users")
  const tc = useTranslations("common")
  const tError = useTranslations("errors")

  // --- List state ---
  const [users, setUsers] = useState<AdminUser[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [search, setSearch] = useState("")
  const [isLoading, setIsLoading] = useState(true)

  // --- Debounce ref ---
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // --- Dialog states ---
  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<AdminUser | null>(null)
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null)
  const [adminToggleTarget, setAdminToggleTarget] = useState<AdminUser | null>(null)
  const [activeToggleTarget, setActiveToggleTarget] = useState<AdminUser | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null)
  const [quotaTarget, setQuotaTarget] = useState<AdminUser | null>(null)
  const [quotaValue, setQuotaValue] = useState("")

  // --- Form fields ---
  const [createUsername, setCreateUsername] = useState("")
  const [createPassword, setCreatePassword] = useState("")
  const [createEmail, setCreateEmail] = useState("")
  const [createDisplayName, setCreateDisplayName] = useState("")
  const [editDisplayName, setEditDisplayName] = useState("")
  const [editEmail, setEditEmail] = useState("")
  const [resetPassword, setResetPassword] = useState("")

  // --- Mutation loading ---
  const [isMutating, setIsMutating] = useState(false)

  // --- Load users ---
  const loadUsers = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await adminApi.listUsers(page, 20, search || undefined)
      setUsers(data.items)
      setTotal(data.total)
      setPages(data.pages)
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsLoading(false)
    }
  }, [page, search])

  useEffect(() => {
    loadUsers()
  }, [loadUsers])

  // --- Search with debounce ---
  const handleSearchChange = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(value)
      setPage(1)
    }, 300)
  }

  // --- Create user ---
  const handleCreate = async () => {
    if (!createUsername.trim() || !createPassword.trim() || !createEmail.trim()) return
    setIsMutating(true)
    try {
      await adminApi.createUser({
        username: createUsername.trim(),
        password: createPassword,
        email: createEmail.trim(),
        display_name: createDisplayName.trim() || undefined,
      })
      toast.success(t("userCreated"))
      setCreateOpen(false)
      setCreateUsername("")
      setCreatePassword("")
      setCreateEmail("")
      setCreateDisplayName("")
      await loadUsers()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Edit user ---
  const openEdit = (u: AdminUser) => {
    setEditTarget(u)
    setEditDisplayName(u.display_name ?? "")
    setEditEmail(u.email ?? "")
  }

  const handleEdit = async () => {
    if (!editTarget) return
    setIsMutating(true)
    try {
      await adminApi.updateUser(editTarget.id, {
        display_name: editDisplayName.trim() || null,
        email: editEmail.trim() || null,
      })
      toast.success(t("userUpdated"))
      setEditTarget(null)
      await loadUsers()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Reset password ---
  const openResetPassword = (u: AdminUser) => {
    setResetTarget(u)
    setResetPassword("")
  }

  const handleResetPassword = async () => {
    if (!resetTarget || !resetPassword.trim()) return
    setIsMutating(true)
    try {
      await adminApi.resetPassword(resetTarget.id, resetPassword)
      toast.success(t("passwordResetSuccess"))
      setResetTarget(null)
      setResetPassword("")
      await loadUsers()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Toggle admin ---
  const handleToggleAdmin = async () => {
    if (!adminToggleTarget) return
    setIsMutating(true)
    try {
      await adminApi.toggleAdmin(adminToggleTarget.id, !adminToggleTarget.is_admin)
      toast.success(t("adminStatusUpdated"))
      setAdminToggleTarget(null)
      await loadUsers()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Delete user ---
  const handleDeleteUser = async () => {
    if (!deleteTarget) return
    setIsMutating(true)
    try {
      await adminApi.deleteUser(deleteTarget.id)
      toast.success(t("userDeleted", { username: deleteTarget.username || deleteTarget.email || "" }))
      setDeleteTarget(null)
      await loadUsers()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Force logout single user ---
  const handleForceLogout = async (u: AdminUser) => {
    try {
      await adminApi.forceLogoutUser(u.id)
      toast.success(t("userLoggedOut"))
      await loadUsers()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    }
  }

  // --- Set quota ---
  const openQuota = (u: AdminUser) => {
    setQuotaTarget(u)
    setQuotaValue(u.token_quota !== null ? String(u.token_quota) : "")
  }

  const handleSetQuota = async () => {
    if (!quotaTarget) return
    setIsMutating(true)
    try {
      const parsed = quotaValue.trim() === "" || quotaValue.trim() === "0" ? null : parseInt(quotaValue, 10)
      await adminApi.setUserQuota(quotaTarget.id, parsed)
      toast.success(t("quotaUpdated"))
      setQuotaTarget(null)
      await loadUsers()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  // --- Toggle active ---
  const handleToggleActive = async () => {
    if (!activeToggleTarget) return
    setIsMutating(true)
    try {
      await adminApi.toggleActive(activeToggleTarget.id, !activeToggleTarget.is_active)
      toast.success(t("userStatusUpdated"))
      setActiveToggleTarget(null)
      await loadUsers()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, tError))
    } finally {
      setIsMutating(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Button onClick={() => setCreateOpen(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          {t("createUser")}
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
      ) : users.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          {t("noUsersFound")}
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("username")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("email")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("role")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {tc("status")}
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  {t("usageQuota")}
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  {tc("actions")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {users.map((u) => {
                const isSelf = currentUser?.id === u.id
                return (
                  <tr key={u.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 font-medium text-foreground">
                      <div className="flex items-center gap-1.5">
                        {u.username || u.email}
                        {isSelf && (
                          <span className="text-xs text-muted-foreground">
                            {t("you")}
                          </span>
                        )}
                        {u.has_active_session && (
                          <Badge variant="secondary" className="bg-green-500/10 text-green-600 border-green-500/20 text-[10px] px-1.5 py-0">
                            {t("online")}
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {u.email ?? <span className="text-muted-foreground/50">--</span>}
                    </td>
                    <td className="px-4 py-3">
                      {u.is_admin ? (
                        <Badge variant="default">{t("admin")}</Badge>
                      ) : (
                        <Badge variant="secondary">{t("user")}</Badge>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {u.is_active ? (
                        <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">
                          {tc("active")}
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="border-red-500/40 text-red-600 dark:text-red-400">
                          {tc("disabled")}
                        </Badge>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      <div className="space-y-0.5">
                        {u.monthly_tokens > 0 && (
                          <p className="text-muted-foreground text-xs">{t("tokensLabel", { count: u.monthly_tokens.toLocaleString() })}</p>
                        )}
                        <p className="text-xs text-muted-foreground/70">
                          {u.token_quota !== null ? t("quotaValue", { value: u.token_quota.toLocaleString() }) : t("unlimited")}
                        </p>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => openEdit(u)}>
                            <Pencil className="mr-2 h-4 w-4" />
                            {tc("edit")}
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => openQuota(u)}>
                            <Gauge className="mr-2 h-4 w-4" />
                            {t("setQuota")}
                          </DropdownMenuItem>
                          {!isSelf && (
                            <>
                              <DropdownMenuItem onClick={() => openResetPassword(u)}>
                                <KeyRound className="mr-2 h-4 w-4" />
                                {t("resetPassword")}
                              </DropdownMenuItem>
                              {u.has_active_session && (
                                <DropdownMenuItem onClick={() => handleForceLogout(u)}>
                                  <LogOut className="mr-2 h-4 w-4" />
                                  {t("forceLogout")}
                                </DropdownMenuItem>
                              )}
                              <DropdownMenuSeparator />
                              <DropdownMenuItem onClick={() => setAdminToggleTarget(u)}>
                                {u.is_admin ? (
                                  <>
                                    <ShieldOff className="mr-2 h-4 w-4" />
                                    {t("revokeAdmin")}
                                  </>
                                ) : (
                                  <>
                                    <ShieldCheck className="mr-2 h-4 w-4" />
                                    {t("makeAdmin")}
                                  </>
                                )}
                              </DropdownMenuItem>
                              <DropdownMenuItem onClick={() => setActiveToggleTarget(u)}>
                                {u.is_active ? (
                                  <>
                                    <UserX className="mr-2 h-4 w-4" />
                                    {t("disableAccount")}
                                  </>
                                ) : (
                                  <>
                                    <UserCheck className="mr-2 h-4 w-4" />
                                    {t("enableAccount")}
                                  </>
                                )}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                variant="destructive"
                                onClick={() => setDeleteTarget(u)}
                              >
                                <Trash2 className="mr-2 h-4 w-4" />
                                {t("deleteUser")}
                              </DropdownMenuItem>
                            </>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {!isLoading && users.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t("totalUsers", { count: total })}</span>
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

      {/* --- Create User Dialog --- */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("createTitle")}</DialogTitle>
            <DialogDescription>
              {t("createDesc")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("username")} <span className="text-destructive">*</span></label>
              <Input
                value={createUsername}
                onChange={(e) => setCreateUsername(e.target.value)}
                placeholder="username"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("password")} <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={createPassword}
                onChange={(e) => setCreatePassword(e.target.value)}
                placeholder="password"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("email")} <span className="text-destructive">*</span></label>
              <Input
                type="email"
                value={createEmail}
                onChange={(e) => setCreateEmail(e.target.value)}
                placeholder="user@example.com"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("displayName")}</label>
              <Input
                value={createDisplayName}
                onChange={(e) => setCreateDisplayName(e.target.value)}
                placeholder={t("displayName")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleCreate}
              disabled={isMutating || !createUsername.trim() || !createPassword.trim() || !createEmail.trim()}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Edit User Dialog --- */}
      <Dialog
        open={editTarget !== null}
        onOpenChange={(open) => { if (!open) setEditTarget(null) }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("editTitle")}</DialogTitle>
            <DialogDescription>
              {t("editDesc", { username: editTarget?.username || editTarget?.email || "" })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("displayName")}</label>
              <Input
                value={editDisplayName}
                onChange={(e) => setEditDisplayName(e.target.value)}
                placeholder={t("displayName")}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("email")} <span className="text-destructive">*</span></label>
              <Input
                type="email"
                value={editEmail}
                onChange={(e) => setEditEmail(e.target.value)}
                placeholder="user@example.com"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditTarget(null)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleEdit} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Reset Password Dialog --- */}
      <Dialog
        open={resetTarget !== null}
        onOpenChange={(open) => { if (!open) setResetTarget(null) }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("resetPassword")}</DialogTitle>
            <DialogDescription>
              {t("resetPasswordDesc", { username: resetTarget?.username || resetTarget?.email || "" })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("newPassword")} <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={resetPassword}
                onChange={(e) => setResetPassword(e.target.value)}
                placeholder={t("newPassword")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setResetTarget(null)}>
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleResetPassword}
              disabled={isMutating || !resetPassword.trim()}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("resetPassword")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* --- Toggle Admin AlertDialog --- */}
      <AlertDialog
        open={adminToggleTarget !== null}
        onOpenChange={(open) => { if (!open) setAdminToggleTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {adminToggleTarget?.is_admin ? t("revokeAdminTitle") : t("grantAdminTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {adminToggleTarget?.is_admin
                ? t("revokeAdminDesc", { username: adminToggleTarget.username || adminToggleTarget.email || "" })
                : t("grantAdminDesc", { username: adminToggleTarget?.username || adminToggleTarget?.email || "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleToggleAdmin} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {adminToggleTarget?.is_admin ? t("revokeAdmin") : t("makeAdmin")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- Toggle Active AlertDialog --- */}
      <AlertDialog
        open={activeToggleTarget !== null}
        onOpenChange={(open) => { if (!open) setActiveToggleTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {activeToggleTarget?.is_active ? t("disableAccountTitle") : t("enableAccountTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {activeToggleTarget?.is_active
                ? t("disableAccountDesc", { username: activeToggleTarget.username || activeToggleTarget.email || "" })
                : t("enableAccountDesc", { username: activeToggleTarget?.username || activeToggleTarget?.email || "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleToggleActive} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {activeToggleTarget?.is_active ? tc("disable") : tc("enable")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- Delete User AlertDialog --- */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteUserTitle", { username: deleteTarget?.username || deleteTarget?.email || "" })}</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="text-sm text-muted-foreground">
                <p>{t("deleteUserDesc")}</p>
                <ul className="mt-2 list-disc pl-5 space-y-1">
                  <li>{t("deleteUserItem1")}</li>
                  <li>{t("deleteUserItem2")}</li>
                  <li>{t("deleteUserItem3")}</li>
                  <li>{t("deleteUserItem4")}</li>
                  <li>{t("deleteUserItem5")}</li>
                </ul>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={handleDeleteUser}
              disabled={isMutating}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("deletePermanently")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* --- Set Quota Dialog --- */}
      <Dialog
        open={quotaTarget !== null}
        onOpenChange={(open) => { if (!open) setQuotaTarget(null) }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("setQuotaTitle")}</DialogTitle>
            <DialogDescription>
              {t("setQuotaDesc", { username: quotaTarget?.username || quotaTarget?.email || "" })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label className="text-sm font-medium">{t("monthlyTokenQuota")}</Label>
              <Input
                type="number"
                min={0}
                value={quotaValue}
                onChange={(e) => setQuotaValue(e.target.value)}
                placeholder={t("quotaPlaceholder")}
              />
              <p className="text-xs text-muted-foreground">
                {t("quotaHint")}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setQuotaTarget(null)}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleSetQuota} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
