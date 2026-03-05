"use client"

import { useState, useMemo } from "react"
import { Search, Key, Settings } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { MCPServerResponse } from "@/types/mcp-server"
import type { MCPServerInitialValues } from "./mcp-server-dialog"

// ---------------------------------------------------------------------------
// Curated server catalog — edit this list to add/remove servers
// ---------------------------------------------------------------------------

interface CuratedServer {
  name: string
  package: string        // npm/pypi package name (display only)
  description: string
  category: string
  command: string        // "npx" | "uvx" | etc.
  args: string           // comma-separated args for MCPServerDialog
  requiresConfig?: string // brief hint about env vars / config needed
  env?: Record<string, string> // pre-populated env vars (values left empty for user to fill)
}

const SERVERS: CuratedServer[] = [
  // ── Filesystem ────────────────────────────────────────────────────────────
  {
    name: "Filesystem",
    package: "@modelcontextprotocol/server-filesystem",
    description: "Read and write local files with configurable allowed directories.",
    category: "Filesystem",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-filesystem, /tmp",
    requiresConfig: "Replace /tmp with your allowed directory path",
  },
  {
    name: "Git",
    package: "mcp-server-git",
    description: "Inspect repository history, diffs, branches and file contents via Git.",
    category: "Filesystem",
    command: "uvx",
    args: "mcp-server-git, --repository, /path/to/repo",
    requiresConfig: "Requires uv · replace /path/to/repo",
  },
  // ── Database ──────────────────────────────────────────────────────────────
  {
    name: "SQLite",
    package: "@modelcontextprotocol/server-sqlite",
    description: "Query and modify SQLite databases with full SQL support.",
    category: "Database",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-sqlite, /path/to/db.sqlite",
    requiresConfig: "Replace with your .sqlite file path",
  },
  {
    name: "PostgreSQL",
    package: "@modelcontextprotocol/server-postgres",
    description: "Read-only SQL access to a PostgreSQL database.",
    category: "Database",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-postgres, postgresql://localhost/mydb",
    requiresConfig: "Replace with your connection string",
  },
  // ── Browser ───────────────────────────────────────────────────────────────
  {
    name: "Puppeteer",
    package: "@modelcontextprotocol/server-puppeteer",
    description: "Browser automation — navigate pages, take screenshots, interact with elements.",
    category: "Browser",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-puppeteer",
  },
  {
    name: "Playwright",
    package: "@playwright/mcp",
    description: "Fast and reliable browser automation with Microsoft Playwright.",
    category: "Browser",
    command: "npx",
    args: "-y, @playwright/mcp",
  },
  // ── Search ────────────────────────────────────────────────────────────────
  {
    name: "Fetch",
    package: "mcp-server-fetch",
    description: "Fetch any web page and convert it to Markdown — no API key needed.",
    category: "Search",
    command: "uvx",
    args: "mcp-server-fetch",
    requiresConfig: "Requires uv",
  },
  {
    name: "Brave Search",
    package: "@modelcontextprotocol/server-brave-search",
    description: "Web and local search via the Brave Search API.",
    category: "Search",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-brave-search",
    requiresConfig: "Get key at brave.com/search/api → paste into BRAVE_API_KEY",
    env: { BRAVE_API_KEY: "" },
  },
  {
    name: "Exa Search",
    package: "exa-mcp-server",
    description: "Semantic AI-first search and content retrieval via the Exa API.",
    category: "Search",
    command: "npx",
    args: "-y, exa-mcp-server",
    requiresConfig: "Get key at dashboard.exa.ai → paste into EXA_API_KEY",
    env: { EXA_API_KEY: "" },
  },
  // ── Productivity ──────────────────────────────────────────────────────────
  {
    name: "Memory",
    package: "@modelcontextprotocol/server-memory",
    description: "Persistent knowledge graph memory for agents across conversations.",
    category: "Productivity",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-memory",
  },
  {
    name: "Sequential Thinking",
    package: "@modelcontextprotocol/server-sequentialthinking",
    description: "Structured step-by-step reasoning tool for complex problem solving.",
    category: "Productivity",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-sequentialthinking",
  },
  {
    name: "Notion",
    package: "@notionhq/notion-mcp-server",
    description: "Official Notion MCP — read and write pages, databases, and blocks.",
    category: "Productivity",
    command: "npx",
    args: "-y, @notionhq/notion-mcp-server",
    requiresConfig: 'Get key at notion.so/my-integrations → set OPENAPI_MCP_HEADERS to: {"Authorization":"Bearer YOUR_KEY"}',
    env: { OPENAPI_MCP_HEADERS: "" },
  },
  // ── Dev Tools ─────────────────────────────────────────────────────────────
  {
    name: "GitHub",
    package: "@modelcontextprotocol/server-github",
    description: "Manage repositories, issues, pull requests and code via GitHub API.",
    category: "Dev Tools",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-github",
    requiresConfig: "Create token at github.com/settings/tokens → paste into GITHUB_PERSONAL_ACCESS_TOKEN",
    env: { GITHUB_PERSONAL_ACCESS_TOKEN: "" },
  },
  {
    name: "GitLab",
    package: "@modelcontextprotocol/server-gitlab",
    description: "Interact with GitLab repositories, issues and merge requests.",
    category: "Dev Tools",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-gitlab",
    requiresConfig: "Create token at gitlab.com/-/user_settings/personal_access_tokens · set GITLAB_URL to your GitLab host",
    env: { GITLAB_PERSONAL_ACCESS_TOKEN: "", GITLAB_URL: "" },
  },
  {
    name: "Everything (test)",
    package: "@modelcontextprotocol/server-everything",
    description: "Official MCP test server covering all tool types — great for verifying setup.",
    category: "Dev Tools",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-everything",
  },
  // ── Communication ─────────────────────────────────────────────────────────
  {
    name: "Slack",
    package: "@modelcontextprotocol/server-slack",
    description: "Read and post messages, manage channels in your Slack workspace.",
    category: "Communication",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-slack",
    requiresConfig: "Create bot at api.slack.com/apps → paste Bot Token; find Team ID in workspace URL",
    env: { SLACK_BOT_TOKEN: "", SLACK_TEAM_ID: "" },
  },
  // ── Cloud ─────────────────────────────────────────────────────────────────
  {
    name: "Google Drive",
    package: "@modelcontextprotocol/server-gdrive",
    description: "Search and access files in Google Drive.",
    category: "Cloud",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-gdrive",
    requiresConfig: "OAuth credentials setup required",
  },
  {
    name: "Google Maps",
    package: "@modelcontextprotocol/server-google-maps",
    description: "Location search, directions, and place details via Google Maps.",
    category: "Cloud",
    command: "npx",
    args: "-y, @modelcontextprotocol/server-google-maps",
    requiresConfig: "Enable Maps API at console.cloud.google.com → paste into GOOGLE_MAPS_API_KEY",
    env: { GOOGLE_MAPS_API_KEY: "" },
  },
]

const CATEGORY_STYLES: Record<string, string> = {
  Filesystem:    "bg-amber-500/10 text-amber-600 ring-amber-500/20",
  Database:      "bg-blue-500/10 text-blue-600 ring-blue-500/20",
  Browser:       "bg-purple-500/10 text-purple-600 ring-purple-500/20",
  Search:        "bg-green-500/10 text-green-600 ring-green-500/20",
  Productivity:  "bg-pink-500/10 text-pink-600 ring-pink-500/20",
  "Dev Tools":   "bg-cyan-500/10 text-cyan-600 ring-cyan-500/20",
  Communication: "bg-orange-500/10 text-orange-600 ring-orange-500/20",
  Cloud:         "bg-sky-500/10 text-sky-600 ring-sky-500/20",
}

const ALL_CATEGORIES = ["All", ...Array.from(new Set(SERVERS.map((s) => s.category)))]

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface MCPHubDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: (server: MCPServerResponse) => void
  onInstallLocal: (initial: MCPServerInitialValues) => void
}

export function MCPHubDialog({ open, onOpenChange, onInstallLocal }: MCPHubDialogProps) {
  const [query, setQuery] = useState("")
  const [activeCategory, setActiveCategory] = useState("All")

  const filtered = useMemo(() => {
    const q = query.toLowerCase()
    return SERVERS.filter((s) => {
      const matchesCategory = activeCategory === "All" || s.category === activeCategory
      const matchesQuery = !q ||
        s.name.toLowerCase().includes(q) ||
        s.package.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q)
      return matchesCategory && matchesQuery
    })
  }, [query, activeCategory])

  const handleConfigure = (server: CuratedServer) => {
    onInstallLocal({
      name: server.name,
      description: server.description,
      transport: "stdio",
      command: server.command,
      args: server.args,
      env: server.env,
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col gap-0 p-0">
        <DialogHeader className="px-6 pt-6 pb-0 shrink-0">
          <DialogTitle>MCP Catalog</DialogTitle>
          <div className="relative mt-3">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search servers..."
              className="pl-9"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          {/* Category chips */}
          <div className="flex items-center gap-1.5 mt-3 flex-wrap pb-3 border-b border-border/40">
            {ALL_CATEGORIES.map((cat) => (
              <button
                key={cat}
                type="button"
                onClick={() => setActiveCategory(cat)}
                className={`px-2.5 py-0.5 rounded-full text-xs font-medium border transition-colors ${
                  activeCategory === cat
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-transparent text-muted-foreground border-border hover:border-foreground/40 hover:text-foreground"
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <p className="text-sm text-muted-foreground">No servers found</p>
            </div>
          ) : (
            <div className="space-y-2">
              {filtered.map((server) => (
                <CatalogCard
                  key={server.package}
                  server={server}
                  onConfigure={() => handleConfigure(server)}
                />
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

function CatalogCard({ server, onConfigure }: { server: CuratedServer; onConfigure: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-3 hover:border-border/80 transition-colors">
      {/* Avatar */}
      <div className="h-8 w-8 rounded bg-muted flex items-center justify-center shrink-0 mt-0.5">
        <span className="text-xs font-bold text-muted-foreground">
          {server.name.charAt(0).toUpperCase()}
        </span>
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-sm font-medium text-foreground">{server.name}</span>
          <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 shrink-0 ${CATEGORY_STYLES[server.category] ?? "bg-muted text-muted-foreground ring-border"}`}>
            {server.category}
          </span>
        </div>
        <p className="text-xs font-mono text-muted-foreground truncate mt-0.5">{server.package}</p>
        <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{server.description}</p>
        {server.requiresConfig && (
          <p className="flex items-center gap-1 text-[10px] text-amber-600/80 mt-1.5">
            <Key className="h-2.5 w-2.5 shrink-0" />
            {server.requiresConfig}
          </p>
        )}
      </div>

      {/* Action */}
      <div className="shrink-0 mt-0.5">
        <Button
          size="sm"
          variant="outline"
          className="gap-1.5 h-7 text-xs"
          onClick={onConfigure}
        >
          <Settings className="h-3 w-3" />
          Configure
        </Button>
      </div>
    </div>
  )
}
