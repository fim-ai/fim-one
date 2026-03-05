"use client"

import { useState, useEffect, useCallback, useRef } from "react"
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
import { adminApi } from "@/lib/api"
import { useAuth } from "@/contexts/auth-context"
import type { AdminUser } from "@/types/admin"

export function AdminUsers() {
  const { user: currentUser } = useAuth()

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
      const msg = err instanceof Error ? err.message : "Failed to load users"
      toast.error(msg)
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

  // --- Helpers ---
  const errMsg = (err: unknown) =>
    err instanceof Error ? err.message : "Operation failed"

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
      toast.success("User created")
      setCreateOpen(false)
      setCreateUsername("")
      setCreatePassword("")
      setCreateEmail("")
      setCreateDisplayName("")
      await loadUsers()
    } catch (err: unknown) {
      toast.error(errMsg(err))
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
      toast.success("User updated")
      setEditTarget(null)
      await loadUsers()
    } catch (err: unknown) {
      toast.error(errMsg(err))
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
      toast.success("Password reset successfully")
      setResetTarget(null)
      setResetPassword("")
      await loadUsers()
    } catch (err: unknown) {
      toast.error(errMsg(err))
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
      toast.success("Admin status updated")
      setAdminToggleTarget(null)
      await loadUsers()
    } catch (err: unknown) {
      toast.error(errMsg(err))
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
      toast.success(`User "${deleteTarget.username}" deleted`)
      setDeleteTarget(null)
      await loadUsers()
    } catch (err: unknown) {
      toast.error(errMsg(err))
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
      toast.success("User status updated")
      setActiveToggleTarget(null)
      await loadUsers()
    } catch (err: unknown) {
      toast.error(errMsg(err))
    } finally {
      setIsMutating(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search users..."
            className="pl-9"
            onChange={(e) => handleSearchChange(e.target.value)}
          />
        </div>
        <Button onClick={() => setCreateOpen(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          Create User
        </Button>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : users.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          No users found.
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  Username
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  Email
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  Role
                </th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">
                  Status
                </th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {users.map((u) => {
                const isSelf = currentUser?.id === u.id
                return (
                  <tr key={u.id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 font-medium text-foreground">
                      {u.username}
                      {isSelf && (
                        <span className="ml-1.5 text-xs text-muted-foreground">
                          (you)
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {u.email ?? <span className="text-muted-foreground/50">--</span>}
                    </td>
                    <td className="px-4 py-3">
                      {u.is_admin ? (
                        <Badge variant="default">Admin</Badge>
                      ) : (
                        <Badge variant="secondary">User</Badge>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {u.is_active ? (
                        <Badge variant="outline" className="border-green-500/40 text-green-600 dark:text-green-400">
                          Active
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="border-red-500/40 text-red-600 dark:text-red-400">
                          Disabled
                        </Badge>
                      )}
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
                            Edit
                          </DropdownMenuItem>
                          {!isSelf && (
                            <>
                              <DropdownMenuItem onClick={() => openResetPassword(u)}>
                                <KeyRound className="mr-2 h-4 w-4" />
                                Reset Password
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem onClick={() => setAdminToggleTarget(u)}>
                                {u.is_admin ? (
                                  <>
                                    <ShieldOff className="mr-2 h-4 w-4" />
                                    Revoke Admin
                                  </>
                                ) : (
                                  <>
                                    <ShieldCheck className="mr-2 h-4 w-4" />
                                    Make Admin
                                  </>
                                )}
                              </DropdownMenuItem>
                              <DropdownMenuItem onClick={() => setActiveToggleTarget(u)}>
                                {u.is_active ? (
                                  <>
                                    <UserX className="mr-2 h-4 w-4" />
                                    Disable Account
                                  </>
                                ) : (
                                  <>
                                    <UserCheck className="mr-2 h-4 w-4" />
                                    Enable Account
                                  </>
                                )}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                className="text-destructive focus:text-destructive"
                                onClick={() => setDeleteTarget(u)}
                              >
                                <Trash2 className="mr-2 h-4 w-4" />
                                Delete User
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
          <span>{total} user{total !== 1 ? "s" : ""} total</span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <span>
              Page {page} of {pages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {/* --- Create User Dialog --- */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create User</DialogTitle>
            <DialogDescription>
              Add a new user account to the system.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">Username <span className="text-destructive">*</span></label>
              <Input
                value={createUsername}
                onChange={(e) => setCreateUsername(e.target.value)}
                placeholder="username"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Password <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={createPassword}
                onChange={(e) => setCreatePassword(e.target.value)}
                placeholder="password"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Email <span className="text-destructive">*</span></label>
              <Input
                type="email"
                value={createEmail}
                onChange={(e) => setCreateEmail(e.target.value)}
                placeholder="user@example.com"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Display Name</label>
              <Input
                value={createDisplayName}
                onChange={(e) => setCreateDisplayName(e.target.value)}
                placeholder="Display Name"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={isMutating || !createUsername.trim() || !createPassword.trim() || !createEmail.trim()}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create
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
            <DialogTitle>Edit User</DialogTitle>
            <DialogDescription>
              Update profile information for {editTarget?.username}.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">Display Name</label>
              <Input
                value={editDisplayName}
                onChange={(e) => setEditDisplayName(e.target.value)}
                placeholder="Display Name"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Email <span className="text-destructive">*</span></label>
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
              Cancel
            </Button>
            <Button onClick={handleEdit} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Save
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
            <DialogTitle>Reset Password</DialogTitle>
            <DialogDescription>
              Set a new password for {resetTarget?.username}.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">New Password <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={resetPassword}
                onChange={(e) => setResetPassword(e.target.value)}
                placeholder="New password"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setResetTarget(null)}>
              Cancel
            </Button>
            <Button
              onClick={handleResetPassword}
              disabled={isMutating || !resetPassword.trim()}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Reset Password
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
              {adminToggleTarget?.is_admin ? "Revoke admin privileges?" : "Grant admin privileges?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {adminToggleTarget?.is_admin
                ? `${adminToggleTarget.username} will lose admin access and become a regular user.`
                : `${adminToggleTarget?.username} will gain full admin access to the system.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleToggleAdmin} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {adminToggleTarget?.is_admin ? "Revoke Admin" : "Make Admin"}
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
              {activeToggleTarget?.is_active ? "Disable account?" : "Enable account?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {activeToggleTarget?.is_active
                ? `${activeToggleTarget.username} will be unable to log in until re-enabled.`
                : `${activeToggleTarget?.username} will be able to log in again.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleToggleActive} disabled={isMutating}>
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {activeToggleTarget?.is_active ? "Disable" : "Enable"}
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
            <AlertDialogTitle>Delete user "{deleteTarget?.username}"?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete the account and all associated data. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90"
              onClick={handleDeleteUser}
              disabled={isMutating}
            >
              {isMutating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Delete Permanently
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
