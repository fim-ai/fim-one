"use client"

import { Badge } from "@/components/ui/badge"

interface ToolEntry {
  name: string
  description: string
  note?: string // optional sub-detail, e.g. operation list
}

interface ToolCategory {
  category: string
  tools: ToolEntry[]
}

const BUILTIN_TOOLS: ToolCategory[] = [
  {
    category: "Computation",
    tools: [
      { name: "calculator", description: "Safe AST-based math expression evaluation — add, sub, mul, div, pow, sqrt, sin, cos, log, …" },
      { name: "python_exec", description: "Execute Python code in a sandboxed namespace; stdout/stderr captured; supports file I/O and most stdlib" },
    ],
  },
  {
    category: "Web",
    tools: [
      { name: "web_fetch", description: "Fetch a URL and return clean Markdown via Jina Reader (r.jina.ai)" },
      { name: "web_search", description: "Search the web and return ranked results via Jina Search (s.jina.ai)" },
      { name: "http_request", description: "Send arbitrary HTTP requests (GET/POST/PUT/DELETE/PATCH) to REST APIs; SSRF-protected" },
    ],
  },
  {
    category: "Filesystem",
    tools: [
      {
        name: "file_ops",
        description: "Sandboxed file operations within a per-conversation workspace",
        note: "read · write · append · delete · list · mkdir · exists · get_info · read_json · write_json · read_csv · write_csv · find_replace",
      },
      { name: "shell_exec", description: "Run shell commands in a sandboxed directory; blocked: sudo, rm -rf /, network reconfig, dangerous binaries" },
    ],
  },
  {
    category: "Knowledge",
    tools: [
      { name: "kb_list", description: "List all knowledge bases available to the current user (id, name, description, doc count)" },
      { name: "kb_retrieve", description: "Basic vector retrieval from knowledge bases — returns top-K chunks by relevance score" },
      { name: "grounded_retrieve", description: "5-stage grounding pipeline: multi-KB retrieve → citation extraction → alignment scoring → conflict detection → confidence scoring" },
    ],
  },
]

export function BuiltinToolsSection() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {BUILTIN_TOOLS.map((group) => (
        <div
          key={group.category}
          className="flex flex-col rounded-lg border border-border bg-card p-4"
        >
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
            {group.category}
          </h3>
          <div className="flex flex-col gap-3">
            {group.tools.map((tool) => (
              <div key={tool.name} className="flex flex-col gap-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge variant="secondary" className="shrink-0 text-xs font-mono">
                    {tool.name}
                  </Badge>
                  <span className="text-xs text-muted-foreground">{tool.description}</span>
                </div>
                {tool.note && (
                  <p className="text-xs text-muted-foreground/60 pl-1 font-mono leading-relaxed">
                    {tool.note}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
