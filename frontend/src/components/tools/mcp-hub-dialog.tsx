"use client"

import { useState, useMemo } from "react"
import { useTranslations, useMessages } from "next-intl"
import { Check, Key, LayoutTemplate } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
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

const ALL_CATEGORIES = Array.from(new Set(SERVERS.map((s) => s.category)))

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
  const t = useTranslations("tools")
  const tc = useTranslations("common")
  const messages = useMessages()
  const [activeCategory, setActiveCategory] = useState("all")
  const [selectedPackage, setSelectedPackage] = useState<string | null>(null)

  // Safe translation helpers using raw messages to avoid throwing on missing keys
  const mcpHub = ((messages["tools"] as Record<string, unknown>)?.["mcpHub"] ?? {}) as {
    categories?: Record<string, string>
    servers?: Record<string, { desc?: string; config?: string }>
  }
  const getCategoryLabel = (cat: string) => mcpHub.categories?.[cat] ?? cat
  const getServerDesc = (server: CuratedServer) => mcpHub.servers?.[server.name]?.desc ?? server.description

  const filtered = useMemo(() => {
    if (activeCategory === "all") return SERVERS
    return SERVERS.filter((s) => s.category === activeCategory)
  }, [activeCategory])

  const selectedServer = useMemo(
    () => SERVERS.find((s) => s.package === selectedPackage) ?? null,
    [selectedPackage],
  )

  const handleConfigure = () => {
    if (!selectedServer) return
    onInstallLocal({
      name: selectedServer.name,
      description: getServerDesc(selectedServer),
      transport: "stdio",
      command: selectedServer.command,
      args: selectedServer.args,
      env: selectedServer.env,
    })
  }

  // Reset selection when dialog opens
  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      setSelectedPackage(null)
      setActiveCategory("all")
    }
    onOpenChange(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <LayoutTemplate className="h-4 w-4" />
            {t("mcpTemplateTitle")}
          </DialogTitle>
          <DialogDescription>{t("mcpTemplateDescription")}</DialogDescription>
        </DialogHeader>

        {/* Category filter tabs */}
        <Tabs
          value={activeCategory}
          onValueChange={setActiveCategory}
          className="w-full"
        >
          <TabsList className="w-full justify-start flex-wrap h-auto gap-1">
            <TabsTrigger value="all" className="text-xs">
              {getCategoryLabel("All")}
            </TabsTrigger>
            {ALL_CATEGORIES.map((cat) => (
              <TabsTrigger key={cat} value={cat} className="text-xs">
                {getCategoryLabel(cat)}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        {/* Server grid */}
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <LayoutTemplate className="h-10 w-10 text-muted-foreground/40 mb-3" />
            <p className="text-sm text-muted-foreground">{t("noServersFound")}</p>
          </div>
        ) : (
          <ScrollArea className="max-h-[400px]">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 pr-3">
              {filtered.map((server) => {
                const isSelected = selectedPackage === server.package
                return (
                  <button
                    key={server.package}
                    type="button"
                    className={cn(
                      "relative text-left rounded-lg border p-4 transition-all",
                      isSelected
                        ? "border-ring bg-accent/20 ring-1 ring-ring"
                        : "border-border hover:border-ring/40 hover:bg-accent/10",
                    )}
                    onClick={() =>
                      setSelectedPackage(isSelected ? null : server.package)
                    }
                  >
                    {/* Selected indicator */}
                    {isSelected && (
                      <div className="absolute top-2 right-2 flex h-5 w-5 items-center justify-center rounded-full bg-primary">
                        <Check className="h-3 w-3 text-primary-foreground" />
                      </div>
                    )}

                    {/* Icon */}
                    <div className="flex h-10 w-10 items-center justify-center rounded-md bg-muted mb-3 text-muted-foreground">
                      <span className="text-sm font-bold">
                        {server.name.charAt(0).toUpperCase()}
                      </span>
                    </div>

                    {/* Name + config hint */}
                    <div className="flex items-center gap-1 pr-5">
                      <p className="text-sm font-medium truncate">{server.name}</p>
                      {server.requiresConfig && (
                        <Key className="h-3 w-3 shrink-0 text-amber-600/70" />
                      )}
                    </div>

                    {/* Description */}
                    <p className="text-xs text-muted-foreground line-clamp-2 mt-1 min-h-[2rem]">
                      {getServerDesc(server)}
                    </p>

                    {/* Category badge */}
                    <div className="flex items-center gap-2 mt-3">
                      <Badge
                        variant="secondary"
                        className={cn(
                          "text-[10px] ring-1",
                          CATEGORY_STYLES[server.category] ?? "bg-muted text-muted-foreground ring-border",
                        )}
                      >
                        {getCategoryLabel(server.category)}
                      </Badge>
                    </div>
                  </button>
                )
              })}
            </div>
          </ScrollArea>
        )}

        {/* Footer actions */}
        <div className="flex items-center justify-between pt-2">
          <div className="text-xs text-muted-foreground">
            {selectedServer
              ? selectedServer.name
              : t("mcpSelectHint")}
          </div>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
            >
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleConfigure}
              disabled={!selectedServer}
              className="gap-1.5"
            >
              {t("configure")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
