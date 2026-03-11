"use client"

import { AIPanel } from "@/components/shared/ai-panel"
import type { AgentResponse } from "@/types/agent"

interface AgentAIPanelProps {
  agentId: string | null
  onAgentUpdated: (agent: AgentResponse) => void
  formDirty?: boolean
  isNewMode?: boolean
  onAgentCreated?: (agent: AgentResponse) => void
  onBuilderModeChange?: (active: boolean) => void
}

export function AgentAIPanel({
  agentId,
  onAgentUpdated,
  formDirty = false,
  isNewMode = false,
  onAgentCreated,
  onBuilderModeChange,
}: AgentAIPanelProps) {
  return (
    <AIPanel
      mode="agent"
      id={agentId}
      formDirty={formDirty}
      isNewMode={isNewMode}
      onBuilderModeChange={onBuilderModeChange}
      onAgentUpdated={onAgentUpdated}
      onEntityCreated={onAgentCreated as (entity: unknown) => void}
    />
  )
}
