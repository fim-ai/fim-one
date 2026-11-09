import type { ComponentType } from "react"
import type { WorkflowNodeV2Type } from "@/types/workflow-blueprint.generated"
import { AgentForm } from "./agent-form"
import { ConditionBranchForm } from "./condition-branch-form"
import { ConnectorForm } from "./connector-form"
import { EndForm } from "./end-form"
import { HumanInterventionForm } from "./human-intervention-form"
import { KnowledgeRetrievalForm } from "./knowledge-retrieval-form"
import { LLMForm } from "./llm-form"
import { MCPForm } from "./mcp-form"
import { StartForm } from "./start-form"
import type { NodeFormProps } from "./types"

/**
 * The settings form of each node type. Keyed by the generated node type
 * union: a node type added to the backend schema does not compile here
 * until it has a form.
 */
export const NODE_FORMS: { [K in WorkflowNodeV2Type]: ComponentType<NodeFormProps<K>> } = {
  start: StartForm,
  end: EndForm,
  llm: LLMForm,
  conditionBranch: ConditionBranchForm,
  agent: AgentForm,
  knowledgeRetrieval: KnowledgeRetrievalForm,
  connector: ConnectorForm,
  humanIntervention: HumanInterventionForm,
  mcp: MCPForm,
}

/** The form for a node type, or `null` for a type this editor does not know. */
export function formForNodeType(
  nodeType: string | undefined,
): ComponentType<NodeFormProps<WorkflowNodeV2Type>> | null {
  if (!nodeType || !Object.prototype.hasOwnProperty.call(NODE_FORMS, nodeType)) return null
  return NODE_FORMS[nodeType as WorkflowNodeV2Type] as ComponentType<
    NodeFormProps<WorkflowNodeV2Type>
  >
}
