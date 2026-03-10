"use client"

import { FileSpreadsheet } from "lucide-react"
import { useTranslations } from "next-intl"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

interface SchemaImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SchemaImportDialog({ open, onOpenChange }: SchemaImportDialogProps) {
  const t = useTranslations("connectors")

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileSpreadsheet className="h-4 w-4" />
            {t("schemaImportTitle")}
          </DialogTitle>
          <DialogDescription>{t("schemaImportComingSoon")}</DialogDescription>
        </DialogHeader>
      </DialogContent>
    </Dialog>
  )
}
