import { useCallback, useEffect, useRef, useState } from "react"
import type { MutableRefObject, RefObject } from "react"
import { toast } from "sonner"
import { useTranslations } from "next-intl"

import { workflowApi } from "@/lib/api"
import type { ValidationResult } from "@/components/workflows/workflow-toolbar"
import type { WorkflowEditorHandle } from "@/components/workflows/workflow-editor"
import type { WorkflowBlueprint, WorkflowValidateResponse } from "@/types/workflow"

/** How long after the last edit the blueprint is validated. */
const VALIDATION_DELAY_MS = 2000

/**
 * Validation of the blueprint being edited: the debounced check behind the
 * toolbar indicator and the node badges, and the on-demand server check
 * shown in the validation panel.
 */
export function useWorkflowValidation(
  workflowId: string | null,
  blueprintRef: MutableRefObject<WorkflowBlueprint>,
  editorRef: RefObject<WorkflowEditorHandle | null>,
) {
  const t = useTranslations("workflows")

  // Validation state (client-side, debounced)
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null)
  const [isValidating, setIsValidating] = useState(false)
  const validationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Server-side validation panel state
  const [serverValidationOpen, setServerValidationOpen] = useState(false)
  const [serverValidationResult, setServerValidationResult] = useState<WorkflowValidateResponse | null>(null)
  const [isServerValidating, setIsServerValidating] = useState(false)

  // Debounced blueprint validation
  const triggerValidation = useCallback(() => {
    if (validationTimerRef.current) {
      clearTimeout(validationTimerRef.current)
    }
    validationTimerRef.current = setTimeout(async () => {
      const bp = blueprintRef.current
      // Skip validation for empty blueprints
      if (bp.nodes.length === 0) {
        setValidationResult(null)
        return
      }
      setIsValidating(true)
      try {
        const result = await workflowApi.validate(bp as unknown as Record<string, unknown>)
        setValidationResult(result)
        // Apply validation badges on canvas nodes
        editorRef.current?.applyValidationOverlay(result)
      } catch {
        // Silently ignore validation failures — don't block the user
        setValidationResult(null)
        editorRef.current?.clearValidationOverlay()
      } finally {
        setIsValidating(false)
      }
    }, VALIDATION_DELAY_MS)
  }, [blueprintRef, editorRef])

  // Clean up the timer on unmount
  useEffect(() => {
    return () => {
      if (validationTimerRef.current) {
        clearTimeout(validationTimerRef.current)
      }
    }
  }, [])

  const validateOnServer = useCallback(async () => {
    if (!workflowId) return
    setServerValidationOpen(true)
    setIsServerValidating(true)
    try {
      const result = await workflowApi.validateById(workflowId)
      setServerValidationResult(result)
    } catch {
      toast.error(t("validateFailed"))
    } finally {
      setIsServerValidating(false)
    }
  }, [workflowId, t])

  return {
    validationResult,
    isValidating,
    triggerValidation,
    serverValidationOpen,
    setServerValidationOpen,
    serverValidationResult,
    isServerValidating,
    validateOnServer,
  }
}
