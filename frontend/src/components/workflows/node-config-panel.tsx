"use client"

import { useState, useCallback, useMemo } from "react"
import { useTranslations } from "next-intl"
import { Beaker, Trash2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { AdvancedSection, defaultTimeoutMs } from "./node-config/advanced-section"
import { CommentSection } from "./node-config/comment-section"
import { formForNodeType } from "./node-config/registry"
import { TestNodeDialog } from "./test-node-dialog"
import type { Node } from "@xyflow/react"
import type { WorkflowNodeType, ErrorStrategy } from "@/types/workflow"

interface NodeConfigPanelProps {
  workflowId: string
  node: Node | null
  allNodes: Node[]
  onUpdate: (nodeId: string, data: Record<string, unknown>) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}

export function NodeConfigPanel({ workflowId, node, allNodes, onUpdate, onDelete, onClose }: NodeConfigPanelProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")
  const [testDialogOpen, setTestDialogOpen] = useState(false)

  const updateField = useCallback(
    (field: string, value: unknown) => {
      if (!node) return
      onUpdate(node.id, { ...node.data, [field]: value })
    },
    [node, onUpdate],
  )

  // Several fields in one edit (one change, one undo step)
  const updateFields = useCallback(
    (patch: Record<string, unknown>) => {
      if (!node) return
      onUpdate(node.id, { ...node.data, ...patch })
    },
    [node, onUpdate],
  )

  // All nodes except the currently selected one — variable sources
  const otherNodes = useMemo(
    () => (node ? allNodes.filter((n) => n.id !== node.id) : []),
    [allNodes, node],
  )

  if (!node) {
    return (
      <div className="flex flex-col h-full border-l border-border/40 bg-background w-[300px]">
        <div className="flex items-center justify-center h-full">
          <p className="text-xs text-muted-foreground">{t("configNoSelection")}</p>
        </div>
      </div>
    )
  }

  const nodeType = node.type as WorkflowNodeType
  // One form per node type, typed against the generated blueprint v2 data
  const NodeForm = formForNodeType(node.type)

  return (
    <div
      data-testid="node-config-panel"
      className="flex flex-col h-full border-l border-border/40 bg-background w-[300px]"
    >
      <div className="flex items-center justify-between px-3 pt-3 pb-2 shrink-0 border-b border-border/40">
        <h3 className="text-xs font-semibold text-foreground">
          {t("configTitle")}
        </h3>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setTestDialogOpen(true)}
            title={t("testNode")}
          >
            <Beaker className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      <ScrollArea className="flex-1 min-h-0">
        <div className="p-3 space-y-4">
          <p className="text-sm font-medium text-foreground">
            {t(`nodeType_${nodeType}` as Parameters<typeof t>[0])}
          </p>

          {/* Note annotation — available for all node types */}
          <div className="space-y-1">
            <label className="text-[11px] font-medium text-muted-foreground">
              {t("configNote")}
            </label>
            <Textarea
              value={(node.data.note as string) ?? ""}
              onChange={(e) => updateField("note", e.target.value || undefined)}
              placeholder={t("configNotePlaceholder")}
              className="min-h-[32px] h-auto text-xs resize-none"
              rows={1}
              onInput={(e) => {
                const target = e.target as HTMLTextAreaElement
                target.style.height = "auto"
                target.style.height = `${target.scrollHeight}px`
              }}
            />
          </div>

          {NodeForm ? (
            <NodeForm
              // A form's own state (a draft, an open section) belongs to
              // one step: another step of the same type starts afresh.
              key={node.id}
              nodeId={node.id}
              data={node.data}
              setField={updateField}
              setFields={updateFields}
              otherNodes={otherNodes}
            />
          ) : (
            <p className="text-xs text-muted-foreground">No configuration available</p>
          )}

          {/* Advanced section — error strategy + timeout (not for Start/End) */}
          {nodeType !== "start" && nodeType !== "end" && (
            <AdvancedSection
              errorStrategy={(node.data.error_strategy as ErrorStrategy) ?? "stop_workflow"}
              timeoutMs={(node.data.timeout_ms as number) ?? defaultTimeoutMs(nodeType)}
              retryCount={(node.data.retry_count as number) ?? 0}
              retryDelayMs={(node.data.retry_delay_ms as number) ?? 1000}
              onChangeErrorStrategy={(v) => updateField("error_strategy", v)}
              onChangeTimeout={(v) => updateField("timeout_ms", v)}
              onChangeRetryCount={(v) => updateField("retry_count", v)}
              onChangeRetryDelay={(v) => updateField("retry_delay_ms", v)}
            />
          )}

          {/* Comment / annotation section — all node types */}
          <CommentSection
            comment={(node.data.comment as string) ?? ""}
            onChange={(v) => updateField("comment", v || undefined)}
          />

          {/* Delete node button — disabled for start/end nodes */}
          {nodeType !== "start" && nodeType !== "end" && (
            <>
              <Separator className="my-1" />
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    variant="destructive"
                    size="sm"
                    className="w-full gap-1.5"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {t("deleteNode")}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent size="sm">
                  <AlertDialogHeader>
                    <AlertDialogTitle>{t("deleteNode")}</AlertDialogTitle>
                    <AlertDialogDescription>
                      {t("deleteNodeConfirm")}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
                    <AlertDialogAction
                      variant="destructive"
                      onClick={() => onDelete(node.id)}
                    >
                      {tc("delete")}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </>
          )}
        </div>
      </ScrollArea>

      {/* Test Node Dialog */}
      <TestNodeDialog
        workflowId={workflowId}
        nodeId={node.id}
        nodeType={nodeType}
        nodeLabel={t(`nodeType_${nodeType}` as Parameters<typeof t>[0])}
        open={testDialogOpen}
        onOpenChange={setTestDialogOpen}
      />
    </div>
  )
}
