import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"
import { useState } from "react"
import type { Edge, Node } from "@xyflow/react"

import { useCanvasEditing } from "@/components/workflows/editor/use-canvas-editing"

const translate = vi.hoisted(() => (key: string) => key)
vi.mock("next-intl", () => ({ useTranslations: () => translate }))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function node(id: string, type: string, data: Record<string, unknown> = {}, extra: Partial<Node> = {}): Node {
  return { id, type, position: { x: 0, y: 0 }, data, ...extra }
}

/** The editing hook over real node and edge state, as the editor holds it. */
function useCanvas(initialNodes: Node[], initialEdges: Edge[] = []) {
  const [nodes, setNodes] = useState(initialNodes)
  const [edges, setEdges] = useState(initialEdges)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const editing = useCanvasEditing({ nodes, edges, setNodes, setEdges, selectedNodeId, setSelectedNodeId })
  return { nodes, edges, editing }
}

afterEach(cleanup)

describe("useCanvasEditing", () => {
  describe("Duplicate from a node's context menu", () => {
    it("copies the node that was right-clicked, not the selected one", () => {
      const { result } = renderHook(() =>
        useCanvas([
          node("llm_1", "llm", { prompt_template: "selected" }, { selected: true }),
          node("agent_1", "agent", { prompt_template: "right-clicked" }, { position: { x: 100, y: 40 } }),
        ]),
      )
      act(() => result.current.editing.duplicateNode("agent_1"))

      expect(result.current.nodes).toHaveLength(3)
      const copy = result.current.nodes[2]
      expect(copy.type).toBe("agent")
      expect(copy.data).toEqual({ prompt_template: "right-clicked" })
      expect(copy.id).not.toBe("agent_1")
      expect(copy.position).toEqual({ x: 150, y: 90 })
      // The copy is what is selected afterwards.
      expect(result.current.nodes.filter((n) => n.selected).map((n) => n.id)).toEqual([copy.id])
    })

    it("leaves the clipboard as it was", () => {
      const { result } = renderHook(() =>
        useCanvas([node("llm_1", "llm", {}, { selected: true }), node("agent_1", "agent")]),
      )
      act(() => result.current.editing.copySelected())
      act(() => result.current.editing.duplicateNode("agent_1"))
      act(() => result.current.editing.paste())
      expect(result.current.nodes.map((n) => n.type)).toEqual(["llm", "agent", "agent", "llm"])
    })

    it("copies the whole selection when the right-clicked node is part of it", () => {
      const { result } = renderHook(() =>
        useCanvas(
          [
            node("llm_1", "llm", {}, { selected: true }),
            node("agent_1", "agent", {}, { selected: true }),
            node("mcp_1", "mcp"),
          ],
          [{ id: "e1", source: "llm_1", target: "agent_1" }],
        ),
      )
      act(() => result.current.editing.duplicateNode("agent_1"))
      expect(result.current.nodes.map((n) => n.type)).toEqual(["llm", "agent", "mcp", "llm", "agent"])
      expect(result.current.edges).toHaveLength(2)
    })

    it("does not copy Start or End", () => {
      const { result } = renderHook(() => useCanvas([node("start_1", "start"), node("end_1", "end")]))
      act(() => result.current.editing.duplicateNode("start_1"))
      act(() => result.current.editing.duplicateNode("end_1"))
      expect(result.current.nodes).toHaveLength(2)
    })
  })
})
