"use client"

import { AIPanel } from "@/components/shared/ai-panel"
import type { ConnectorResponse } from "@/types/connector"

interface AIActionPanelProps {
  connectorId: string | null
  onActionsChanged: () => void
  onConnectorUpdated?: (connector: ConnectorResponse) => void
  onSchemaChanged?: () => void
  formDirty?: boolean
  isNewMode?: boolean
  onConnectorCreated?: (connector: ConnectorResponse) => void
  onBuilderModeChange?: (active: boolean) => void
  connectorType?: "api" | "database"
}

export function AIActionPanel({
  connectorId,
  onActionsChanged,
  onConnectorUpdated,
  onSchemaChanged,
  formDirty = false,
  isNewMode = false,
  onConnectorCreated,
  onBuilderModeChange,
  connectorType = "api",
}: AIActionPanelProps) {
  const mode = connectorType === "database" ? "connector-db" : "connector-api"

  return (
    <AIPanel
      mode={mode}
      id={connectorId}
      formDirty={formDirty}
      isNewMode={isNewMode}
      onBuilderModeChange={onBuilderModeChange}
      onActionsChanged={onActionsChanged}
      onConnectorUpdated={onConnectorUpdated}
      onSchemaChanged={onSchemaChanged}
      onEntityCreated={onConnectorCreated as (entity: unknown) => void}
    />
  )
}
