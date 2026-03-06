# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions correspond to [Roadmap](Roadmap.md) milestones.

## [Unreleased]

### Added
- **Admin/Settings: invite code inactive filter**: Revoked and exhausted invite codes are hidden by default; an "N inactive" toggle button reveals them. Exhausted codes (use_count ≥ max_uses) now show an "Exhausted" badge distinct from "Revoked".

### Fixed
- **Auth: refresh token expiry comparison** — `refresh_token_expires_at` (SQLite naive datetime) now has `tzinfo=UTC` attached before comparing with `datetime.now(UTC)`, fixing `TypeError: can't compare offset-naive and offset-aware datetimes` in `POST /api/auth/refresh`.
- **Replace deprecated `datetime.utcnow()`** with `datetime.now(UTC)` across auth and OAuth endpoints (S-9)
- **Add host-execution warning to ShellExecTool**: logs a startup warning when using the local sandbox backend without OS-level isolation (S-8)
- **OAuth callback redirect** now passes tokens via URL fragment instead of query params, preventing leakage in server logs and browser history (S-7)
- **OAuth bind flow** now uses one-time ticket instead of raw JWT in URL; new `POST /api/auth/oauth/bind-ticket` endpoint (S-6)
- **API/chat: DB session leak on setup failure**: Both `react_endpoint` and `dag_endpoint` now call `db_session.close()` before nulling the reference when the setup block raises, preventing connection pool exhaustion.
- **API/chat: atomic token tracking**: Fast-LLM token persistence replaced SELECT+modify with a single SQL `UPDATE … SET total_tokens = COALESCE(total_tokens,0)+N` to avoid read-modify-write races under concurrent requests.
- **API/chat: fast_usage_tracker scope in DAG endpoint**: Moved initialization before the try block so the variable is always defined when the done payload is assembled; also tracks usage from the conversation summarization phase.
- **Conversation context: stale 404 sidebar entry**: When `selectConversation` gets a 404 (conversation deleted by admin), the entry is now removed from the local sidebar list instead of leaving an unloadable ghost item.

### Changed
- **Maintenance mode check cached with 30s TTL** to reduce DB load on every request (P-1)
- **Admin: uniform page headers**: All admin sections (Overview, Users, Conversations, Connectors, MCP Servers, Storage, Audit Log, Settings) now open with a consistent `<h2>` title + subtitle description block. Section-level icon decorators removed; action buttons (Create User, Add Global Server) moved into the header row.
- **Login/Setup: frosted-glass right panel**: Page root uses `oklch(0.13 0.008 55)` background so the dark brand panel extends across the full viewport; the form panel uses `bg-background/88 + backdrop-blur-sm` for a subtle frosted-glass effect.

### Removed
- **Dead `get_user_id()` stub** in `deps.py` — always returned `"default"`, superseded by JWT auth (Q-1)

### Added
- **EmailSendTool recipient allowlist**: `SMTP_ALLOWED_DOMAINS` and `SMTP_ALLOWED_ADDRESSES` env vars restrict which addresses the `email_send` tool may send to; any blocked recipient returns `[Error]` without sending; both vars optional — leave unset for no restriction (not recommended for shared mailboxes)
- **Admin: token quota per user**: `token_quota` field on `User` model; admins can cap monthly token usage per account via `PUT /api/admin/users/{id}/quota`
- **Admin: global MCP servers**: `is_global` flag on `MCPServer` makes a server available to all users; `user_id` is now nullable for admin-managed global servers; DB migration handles column addition and table recreation for SQLite NOT NULL constraint
- **Admin: invite codes**: New `InviteCode` model and `invite_codes` table; admins can generate single-use or multi-use codes with optional expiry and notes; CRUD endpoints at `/api/admin/invite-codes`; register endpoint validates invite code in `invite` registration mode
- **Admin: API health dashboard**: `GET /api/admin/system/health` returns integration configuration status for LLM, Fast LLM, Embedding, Reranker, Web Search, Web Fetch, and Image Generation
- **Admin: conversation moderation**: `GET /api/admin/conversations` lists all conversations with user info, message counts, and search/filter; `DELETE /api/admin/conversations/:id` removes any conversation with file cleanup
- **Admin: storage management**: `GET /api/admin/storage` shows per-user disk usage; `DELETE /api/admin/storage/user/:id` clears a user's uploads; `DELETE /api/admin/storage/orphaned` cleans conversation upload dirs for deleted conversations
- **Admin: per-user force logout**: `POST /api/admin/users/:id/force-logout` invalidates a single user's session and marks `tokens_invalidated_at`
- **Admin: registration modes**: Registration endpoint now supports `open`, `invite`, and `disabled` modes with backward compatibility to legacy `registration_enabled` setting
- **Chat: token quota enforcement**: Both ReAct and DAG SSE endpoints check monthly token usage against user quota before starting stream; returns 429 when exceeded
- **Chat: global MCP servers loaded**: MCP server query in chat tool resolution now includes `is_global=true` servers alongside user-owned servers
- **EmailSendTool** (`email_send`): SMTP-powered email sending; supports SSL (port 465), STARTTLS (port 587), and plain; auto-registered when `SMTP_HOST`, `SMTP_USER`, and `SMTP_PASS` env vars are set; `SMTP_FROM` and `SMTP_FROM_NAME` optional; env block added to `example.env`
- **JsonTransformTool** (`json_transform`): JMESPath-based JSON extraction and transformation; extract nested fields, filter arrays, reshape payloads; no extra dependencies
- **TemplateRenderTool** (`template_render`): Jinja2 template rendering with variable injection; supports loops, conditionals, filters; `jinja2>=3.0` added as core dependency
- **TextUtilsTool** (`text_utils`): String manipulation — trim/strip, split, join, replace, case conversion (upper/lower/title), count occurrences, word count, truncate, pad
- **Tool catalog category ordering**: `"general"` category pinned first in `/api/tools` response; remaining categories sorted alphabetically
- **"general" category icon**: Clock → Zap with `text-yellow-500` color in the Built-in Tools page

- **README Provider-Agnostic Setup**: Replaced Anthropic-only Recommended Setup example with a provider table (OpenAI, DeepSeek, Anthropic, Ollama) and a generic minimal `.env`; leading message now correctly states any OpenAI-compatible provider is supported
- **Environment Variables Wiki**: Extracted all env var documentation from README into `wiki/Environment-Variables.md` with full coverage across 8 sections (LLM, agent execution, web tools, RAG, code execution, image generation, connectors, platform, OAuth); README now links to the wiki page

- **Disable Public Registration**: Admins can toggle public self-registration on/off from Admin Panel → Settings tab; setting persists in a new `system_settings` DB table; `POST /api/auth/register` enforces the setting (first user bootstrap always allowed); login page hides the Register tab when disabled; OAuth callback also blocked from creating new accounts; public `GET /api/auth/registration-status` endpoint for frontend
- **Admin Panel — Audit Log**: New `audit_logs` DB table + `AuditLog` model; `write_audit()` helper records every key admin action (user create/delete/enable/disable/grant-admin/revoke-admin/reset-password, settings update, force logout); new Audit Log tab with color-coded action badges, target label, detail column, pagination, and refresh
- **Admin Panel — User Deletion**: `DELETE /api/admin/users/{id}` hard-deletes a user; Users table has new Delete User option with destructive confirmation dialog
- **Admin Panel — System Announcement Banner**: Admin can set an announcement text and toggle it on/off in Settings tab; authenticated users see a dismissible amber banner at the top of every page (fetched via public `GET /api/auth/announcement`)
- **Admin Panel — Maintenance Mode**: Toggle in Settings tab; FastAPI middleware intercepts all non-auth non-admin requests and returns 503 with `Retry-After` header when enabled
- **Admin Panel — Force Logout All Users**: Button in Settings → Danger Zone; calls `POST /api/admin/actions/force-logout-all` to invalidate all refresh tokens; shows count of invalidated sessions; guarded by AlertDialog confirmation

- **Image Generation Tool** (`generate_image`): Built-in tool powered by Google Imagen 3 via the Gemini API; returns a markdown image link that renders inline in chat; requires `IMAGE_GEN_API_KEY` (Google AI Studio key); `IMAGE_GEN_MODEL` defaults to `gemini-3.1-flash-image-preview`; supports aspect ratios 1:1 / 16:9 / 9:16 / 4:3 / 3:4; images are stored per-conversation under `uploads/conversations/{id}/` and cleaned up when the conversation is deleted; Next.js proxies `/uploads/*` to FastAPI so images render correctly in the chat UI
- **Tool Availability System**: `BaseTool.availability()` hook lets any tool declare itself unavailable with a user-facing reason (e.g. missing API key); `ToolRegistry.to_catalog()` now includes `available` + `unavailable_reason` fields; `to_openai_tools()` automatically excludes unavailable tools so the LLM never attempts to call an unconfigured tool
- **Tool Catalog Grayed-Out State**: Unavailable tools appear in the Built-in Tools page with a lock icon and reduced opacity; the card body shows the configuration hint instead of the description; available tools are unaffected
- **`media` Tool Category**: New category for media-generation tools with a pink Image icon in the catalog
- **Uploads Dir Always Mounted**: `app.py` now pre-creates the uploads directory at startup and always mounts `/uploads` as a static route, fixing potential 404s for tools that write to `uploads/` on first use

- **System Model Config Management**: Admin configures LLM providers in Settings → Models tab; `ModelConfig` extended with `max_output_tokens`, `context_size`, and `role` (`"general" | "fast" | null`) fields; migrations `e5g7h9i1j234` and `f6h8j0k2l345`
- **Role-Based LLM Slots**: Settings → Models shows two pinned slot cards (General / Fast); assigning a provider to a role sets `user_id=NULL` (system-level) and single-active constraint via `_unset_role()`
- **Full LLM Priority Chain**: General slot — `get_effective_llm()`: DB `role=general` → ENV; Fast slot — `get_effective_fast_llm()`: DB `role=fast` → DB `role=general` → ENV; agent pinned config (`model_config_id`) takes highest priority; all wired in both ReAct and DAG endpoints
- **Agent Model Selector**: Agent settings form exposes a Model dropdown listing all configured system LLM providers; selection stored as `model_config_json.model_config_id`

- **Write-First Script Execution**: All sandbox backends now write code to a named file in `exec_dir/` before executing — `script_{uuid8}.py` / `.js`; `SandboxResult` carries `script_path`; `python_exec` and `node_exec` tools prepend `[Script: <name>]` so agents can reference the file with `file_ops` and re-run it; Docker backend mounts exec_dir as `/workspace` and executes the file (no more stdin pipe); Local Node switches from `node -e` to `node file.js`

- **Docker Sandbox Backend**: `CODE_EXEC_BACKEND=docker` routes `python_exec`, `node_exec`, and `shell_exec` through Docker containers with `--network=none`, `--memory=256m`, `--cpus=0.5` for OS-level isolation; volume-mounts an exec_dir per run; `DOCKER_PYTHON_IMAGE`, `DOCKER_NODE_IMAGE`, `DOCKER_SHELL_IMAGE` env vars to override default images
- **`node_exec` Tool**: JavaScript/Node.js code execution; local mode runs `node -e`; Docker mode uses `node:20-slim` container; category `computation`, auto-discovered via `discover_builtin_tools()`
- **Pluggable `SandboxBackend` Protocol**: `core/tool/sandbox/` module — `SandboxResult`, `SandboxBackend` Protocol, `LocalBackend`, `DockerBackend` implementations; `get_sandbox_backend()` factory reads `CODE_EXEC_BACKEND` env var; `python_exec` and `shell_exec` delegate to the selected backend; 18 new tests
- **Multi-Provider Web Search**: `core/web/search/` module with `BaseWebSearch` protocol and three backends — `JinaSearch` (default, no key needed), `TavilySearch`, `BraveSearch`; provider auto-selected by `WEB_SEARCH_PROVIDER` env var or presence of `TAVILY_API_KEY`/`BRAVE_API_KEY`; `format_results()` renders structured `SearchResult` objects as Markdown
- **Multi-Provider Web Fetch**: `core/web/fetch/` module with `BaseWebFetch` protocol — `JinaFetch` (Jina Reader, clean Markdown) and `HttpxFetch` (stdlib `html.parser` extraction, zero extra dependencies); provider via `WEB_FETCH_PROVIDER` or auto-detect from `JINA_API_KEY`
- **CohereReranker**: New `core/reranker/cohere.py` — Cohere Rerank API v2 with retry; selectable via `RERANKER_PROVIDER=cohere` or `COHERE_API_KEY` auto-detect
- **OpenAIReranker**: New `core/reranker/openai.py` — bi-encoder cosine similarity using any OpenAI-compatible embedding endpoint; fallback when cross-encoder rerankers are unavailable; selectable via `RERANKER_PROVIDER=openai`

### Fixed
- **Stale image attachment shown on follow-up suggestion**: Clicking an AI-suggested follow-up question no longer shows the previous turn's image thumbnails as still attached; `handleSuggestionSelect` now clears `attachedFiles` and `pendingImages` before forwarding the query (the files were never sent to the backend — this was a UI-only state leak)
- **Maintenance mode console error (race condition)**: `apiFetch` now sets a module-level `_isMaintenance` flag and returns a never-resolving `Promise` on 503 instead of throwing; `setMaintenanceCallback` fires the overlay immediately if 503 already arrived before `AppShell`'s `useEffect` registered the callback — eliminates "System is under maintenance" console errors from child components that mount before the parent overlay handler
- **Force logout all users had no effect**: Access tokens remained valid after invalidating refresh tokens; fixed by adding `iat` (issued-at) to JWT access tokens, adding `tokens_invalidated_at` column to users, and rejecting tokens issued at or before the invalidation timestamp in `get_current_user`; `force_logout_all` now also sets `tokens_invalidated_at = now()` on all non-admin users
- **DAG planning spinner persists after abort**: Removed `stepMap.size > 0` guard from the abort cleanup block in `useDagSteps` so `currentPhase` is reset to `null` even when stopped during the planning phase before any steps arrive
- **SSE HTTP errors silently ignored**: Migrated `useSSE` from `EventSource` to `fetch` + `ReadableStream`; HTTP 4xx/5xx errors now surface as toast messages; `isError` state lets the playground correctly mark a run as stopped/failed
- **Settings flash-and-disappear feedback**: Replaced `setTimeout`-based inline success text with `toast.success` / `toast.error` across AccountSettings and GeneralSettings (OAuth bind, email, password, instructions)
- **Sidebar mode icon always visible**: DAG/ReAct mode icons now only appear on row hover; added Zap icon for ReAct conversations
- **Reranker silent crash when Jina key absent**: `get_reranker()` now returns `None` when `RERANKER_PROVIDER=jina` is set but `JINA_API_KEY` is missing; reranking is silently skipped rather than creating a broken reranker that fails at call time
- **Playground history user icon**: History turns now show user initials (first char of display_name / username) instead of a generic User icon
- **Conversation sidebar stale active highlight**: Active conversation highlight now clears when navigating away from `/`; previously the item stayed highlighted on all sub-pages
- **Admin delete-user dialog JSX quote**: Replaced raw `"` quotes in `AlertDialogTitle` with `&quot;` entities to fix JSX escape lint error

### Changed
- **Image preview lightbox**: Replaced `yet-another-react-lightbox` (and its zoom/download plugins) with a native `Dialog`-based preview in both the playground attachment thumbnails and the markdown renderer; images in chat messages now support click-to-expand with a download/open-in-tab overlay icon; unified UX across all image surfaces
- **env file structure**: Reorganized `example.env` and `.env` into logical sections (LLM → Web Tools → RAG → Code Execution → Connectors → Platform → OAuth); provider selectors now appear before their API keys; replaced version-tagged headers (`v0.4`, `v0.5`) with clean `──` section separators
- **WebSearchTool / WebFetchTool**: Refactored to delegate to `get_web_searcher()` / `get_web_fetcher()` factories; identical public interface — zero breaking changes
- **get_reranker()**: Widened return type to `BaseReranker | None`; supports `RERANKER_PROVIDER` env var for selecting jina / cohere / openai backends

- **MCP Hub — Smithery Registry Search & One-Click Install**: New "Browse Hub" button in the MCP Servers tab opens a searchable dialog powered by the Smithery Registry public API; remote servers install directly via Streamable HTTP transport; local servers pre-fill the MCPServerDialog with `npx @smithery/cli` args; backend proxies the registry search at `GET /api/mcp-servers/hub/search` (auth-guarded)
- **Admin Panel**: System stats dashboard (total users, conversations, messages, tokens, agents, KBs) with recharts bar chart (14-day activity) and donut chart (model usage distribution); user management with search, pagination, create user, edit profile, reset password, toggle admin role, and enable/disable account; admin-only access via `get_current_admin` dependency
- **Role-Based Access Control**: `is_admin` and `is_active` fields on User model; first registered user auto-promoted to admin; disabled accounts blocked from login, token refresh, and API access; `get_current_admin` FastAPI dependency for admin-only endpoints
- **Agent Execution Mode**: Per-agent `execution_mode` field (react/dag) stored in DB; sets default mode for new conversations; frontend mode toggle (Standard/Planner) with icon and description in agent settings form
- **Agent Temperature Control**: Per-agent temperature slider in settings form (0.00–2.00 with Reset to Default); stored in `model_config_json`; frontend syncs execution mode from agent on selection
- **Drag-and-Drop Suggested Prompts**: Suggested prompts editor rebuilt with @dnd-kit for drag-and-drop reordering (replaces arrow up/down buttons); stable IDs prevent React key issues during reorder
- **URL File Resolver**: `resolve_url()` in url_importer detects file extensions (.pdf, .docx, .xlsx, etc.) for direct binary download vs Jina Reader for web pages; enables KB import of direct file URLs
- **BaseLLM.model_id Property**: New property on BaseLLM (returns None); OpenAICompatibleLLM overrides to return the actual model identifier
- **Streamable HTTP MCP Transport**: MCP servers can now use the MCP 2025-03-26 Streamable HTTP transport (`transport: "streamable_http"`); `MCPClient.connect_streamable_http()` using `mcp.client.streamable_http`; UI shows a 3rd transport option with HTTP-aware URL placeholder
- **MCP HTTP Headers**: SSE and Streamable HTTP servers support `headers` (dict of HTTP headers, e.g. `Authorization: Bearer token`); stored as JSON column, passed at connect time; HTTP Headers key-value editor in the MCP server dialog
- **STDIO Working Directory**: STDIO MCP servers now accept `working_dir`; passed as `cwd` to `StdioServerParameters`; optional field in dialog and schema
- **ALLOW_STDIO_MCP Guard**: New `ALLOW_STDIO_MCP` env var (default `true`); set to `false` in SaaS deployments to block subprocess MCP servers; `GET /api/mcp-servers/capabilities` endpoint exposes the flag to the frontend; STDIO option disabled with warning when false
- **MCP Server Management UI**: New `/tools` page with per-user MCP server CRUD (name, transport STDIO/SSE, command/args/env or URL); servers connect on-demand during agent execution and disconnect after the SSE stream ends; system-level `MCP_SERVERS` env var continues to work alongside user-defined servers
- **MCPClient SSE Transport**: `MCPClient.connect_sse()` for remote MCP servers over HTTP/SSE transport, alongside existing `connect_stdio()`; `MCPClient.disconnect()` for single-server session removal
- **Built-in Tools Catalog**: Read-only tools reference in `/tools` page showing Computation, Web, Filesystem, and Knowledge categories
- **KB URL Import**: New "URL Import" tab in the KB upload dialog; users paste multiple URLs (one per line), backend fetches each via Jina Reader API (r.jina.ai) and ingests as Markdown documents using the existing chunking/embedding/LanceDB pipeline; per-URL success/failed status reported
- **file_ops Extended Operations**: Nine new file operations added to the `file_ops` built-in tool — `append`, `delete`, `exists`, `get_info`, `read_json`, `write_json`, `read_csv`, `write_csv`, `find_replace` — bringing feature parity with competitor file tooling while retaining the single-tool dispatch architecture
- **list_knowledge_bases Tool**: New `knowledge`-category built-in tool that lets agents discover all available knowledge bases (id, name, description, document/chunk counts) before calling `kb_retrieve`; auto-discovered via `discover_builtin_tools()`
- **Suggested Prompts Editor**: Agent configuration form now includes a suggested prompts editor; prompts displayed as clickable cards on the playground page
- **Streaming Loading Indicator**: Visual loading state for streaming responses in the playground
- **New Chat Keyboard Shortcut**: ⇧⌘O (Mac) / Ctrl+Shift+O (Windows) to start a new chat from anywhere
- **User Language Preference**: `preferred_language` setting (auto/en/zh) with language directive injection across all LLM interactions (ReAct, DAG, Connector AI, suggestions)
- **Language Selector**: Language sub-menu in user dropdown menu for quick switching
- **OpenAPI Spec Import**: One-shot connector creation from OpenAPI 3.x specs (JSON/YAML); paste, URL, or parsed dict input; auto-extracts connector name, base_url, and description from spec info/servers; parses all operations into actions with parameters_schema, method, path, and requires_confirmation; resolves local `$ref` pointers
- **Add Actions from OpenAPI**: Import actions from OpenAPI spec into an existing connector; optional "replace existing" mode
- **AI Action Generation**: LLM-powered action creation from natural language instructions; connector context + existing actions fed to fast LLM; per-action Pydantic validation with partial success reporting
- **AI Action Refinement**: LLM-powered action editing via create/update/delete operations; supports targeted single-action or bulk refinement
- **AI Action Panel**: Inline collapsible chat panel in the action editor; first message generates, follow-ups refine; live result display (created/updated/deleted counts)
- **OpenAPI Import Dialog**: Reusable dialog with Paste/URL tabs; two modes — "create" (one-shot) and "add-actions" (to existing connector)
- **Citation Badges & References Redesign**: Inline `[N]` citation badges as styled `<sup>` elements in markdown; references section with card layout and collapse/expand toggle
- **KB Document Retry**: Failed document ingestion can be retried; frontend retry button with error tooltip
- **Chunk Text Search**: Search/filter chunks within KB drawer with debounced input and match highlighting
- **Connector Smart Truncation**: JSON-aware response truncation (array item limit + char limit, env-configurable via `CONNECTOR_RESPONSE_MAX_CHARS` / `CONNECTOR_RESPONSE_MAX_ITEMS`)
- **Observation JSON Highlighting**: Auto-detect JSON in tool output, pretty-print with syntax highlighting; label renamed to "Output"
- **Sonner Toast Notifications**: Toast notification library for non-blocking user feedback
- **KB Document Management in README**: Added KB Document Management to README Key Features section
- **RETRIEVAL_MODE Config**: Switch between grounding pipeline and basic RAG via `RETRIEVAL_MODE` env var (grounding | simple); simple mode uses `kb_retrieve` for basic RAG, grounding mode uses `grounded_retrieve` for full 5-stage pipeline; `KBRetrieveTool` supports bound kb_ids with multi-KB merge; frontend `parseSimpleEvidence()` for simple mode references
- **Agent Quick Chat Link**: "Start Chat" button on agent cards and detail page for published agents; auto-selects agent from `?agent=` URL param in chat interface
- **Documentation i18n (Roadmap)**: Added Chinese translation for README, Wiki, and GitHub pages as a v1.0 i18n milestone
- **Iteration Duration Tracking**: Server-side per-iteration elapsed timing (`iter_elapsed`) in DAG executor; frontend displays duration on completed iterations with client-side fallback
- **Elapsed Timer for Running Steps**: Real-time running clock on active DAG step cards
- **First-Run Admin Setup Wizard**: When the database is empty, visitors are redirected from `/login` to `/setup` to create the initial admin account (`is_admin=True`); once created, `/setup` permanently redirects to `/login`
- **Connector Call Logging**: `connector_call_logs` table (ORM + Alembic migration) tracks every connector HTTP call — connector/action IDs, agent/conversation context, HTTP method/status, latency, request/response size; callback pattern in `ConnectorToolAdapter` with automatic logging in chat endpoints
- **Connector Stats API**: `GET /api/admin/connector-stats` endpoint returns per-connector call counts, success rates, average latency, and last-called timestamps for admin monitoring
- **Enhanced Admin Overview Stats**: Admin stats endpoint now includes KB document/chunk counts, total connector count, today's conversation count, and tokens-by-agent breakdown for the recharts dashboard
- **Admin Connectors Tab**: New "Connectors" tab in the admin panel showing connector call metrics (total calls, success rate, avg latency, last called) alongside the existing Overview and Users tabs
- **Tokens by Agent Chart**: Admin overview dashboard displays a tokens-by-agent bar chart for per-agent token consumption visibility
- **Fast LLM Token Tracking**: All fast LLM calls (compact, context guard, grounding, suggestions) now record token consumption via per-request `UsageTracker`; `fast_llm_tokens` column on Conversation model with SQLite auto-migration and Alembic migration; admin dashboard pie chart shows separate "Fast LLM" slice and Total Tokens card shows secondary "Fast LLM: X.XK" line

### Changed
- **Tool Display — Backend Source of Truth**: `Tool` protocol and `BaseTool` now expose `display_name`; builtin tools override where title-casing is wrong (`http_request` → "HTTP Request", `kb_list` → "KB List", `kb_retrieve` → "KB Retrieve", `python_exec` → "Python", `file_ops` → "File Operations", `grounded_retrieve` → "Grounded Retrieve"); MCP/Connector adapters produce `"{server}: {action}"` format; `ToolRegistry.to_catalog()` serialises all metadata; `GET /api/tools/catalog` (no auth) serves it to the frontend with 5-min SWR cache
- **Tool Catalog UI**: Built-in tools section reads from catalog API (zero hardcoding); cards are click-to-expand for descriptions; Connector/MCP link cards show display names; agent settings category tooltips list tool names dynamically; `getToolDisplayName()` / `getToolIcon()` in step-summary use catalog with fallback by naming convention
- **Navigation Links**: All navigation buttons replaced with Next.js `<Link>` components for proper middle-click/Cmd+Click new-tab behavior across agent cards, connector cards, KB cards, sidebar conversations, chats page, and all "New" buttons
- **Mode Labels**: "ReAct" renamed to "Standard", "DAG" renamed to "Planner" across sidebar, playground mode toggle, and examples; mode toggle now shows tooltip with description
- **Tools Page Redesign**: Split into Tabs (Built-in / MCP Servers); built-in tools section redesigned with card grid layout, per-tool icons with category colors, and category filter chips
- **Toast Feedback**: All CRUD operations now show sonner toast notifications (success/error) replacing silent `console.error()` — affects agents, connectors, KB, chunks, actions, and MCP server dialogs
- **Dirty State Protection**: MCP server dialog and agent editor page now warn before discarding unsaved changes (AlertDialog confirm on close/navigate); agent editor adds `beforeunload` browser warning
- **Setup Page**: Email field now required (was optional); client-side email format validation
- **Tool Category Tooltips**: Agent settings form shows tooltip on hover for each tool category with description and included tools list
- **Iteration Detail Drawer**: Tab bar and stats (summary, duration) combined into a single row for cleaner layout
- **Input/Textarea Focus**: Changed from ring-based to outline-based focus style for consistency
- **KB Detail Labels**: "Upload" → "Add Documents", "New MD" → "Write Note"
- **Agent Selector**: Shows agent emoji/icon instead of generic User icon; uses agent ID as value for correct selection with duplicate names
- **Retry Button**: Playground shows a retry button for stopped/interrupted tasks (both in-session and after page refresh)
- **Login Page Redesign**: Split layout with left brand panel (dark, mesh gradient, tagline) and right form panel (theme-aware); removed Satoshi font, kept Cabinet Grotesk
- **Sidebar Logo Font**: Increased from `text-sm` to `text-base`
- **Frontend API Proxy**: Migrated all frontend API calls to Next.js proxy rewrites (`getApiBaseUrl` for client-side proxy, `getApiDirectUrl` for server-side direct access); eliminates CORS issues and hides backend URL from browser
- **Roadmap v1.1 restructured**: Renamed from "Enterprise & Scale" to "Agent as a Service" — added Agent Publishing (published pages, API endpoints, API keys, version snapshots, custom branding), 3 delivery channels (Web App / API / Embed), access control (visitor modes, rate limiting, domain/IP restrictions), and usage analytics (per-agent dashboard, conversation logs, cost attribution); existing Connector Ecosystem and Enterprise Operations items retained
- Sidebar "New Chat" and "Search" restyled as minimal plain buttons with hover keyboard shortcut hints (⇧⌘O / ⌘K)
- Sidebar logo size reduced from h-6 to h-5
- Frontend i18n moved from v0.6.2 scope to v1.0 backlog; User Language Preference (backend) remains shipped
- Connectors now default to "published" on creation; removed publish/unpublish workflow and status badge
- Agent form always sends `kb_ids` and `connector_ids` (even when empty); confidence threshold moved above connectors section
- LanceDB auto-migrates missing schema columns on table open
- KB chunk drawer search replaced with collapsible toggle icon button (auto-focus, Escape to close)
- KB detail page "Search" tab renamed to "Retrieve Test"
- Inject recall button now always visible (not hidden on success)
- **ThinkingCard Loading**: Simplified to single "Processing..." label with shiny-text shimmer animation; removed fake rotating THINKING_HINTS
- **Monospace Font**: Switched to JetBrains Mono for `--font-mono` (replaces Inter)
- **Sidebar Layout**: Width w-60→w-72; logo clickable (links to /new); collapsed mode keeps only search button; "New chat" icon restyled with background container
- **Step Collapsible Groups**: DAG and ReAct step cards rendered inside the collapsible summary container (no longer floating below the toggle bar)
- **Result Drawer**: Step result display changed from inline collapsible to right-side Sheet drawer for full-width markdown rendering
- **History Step Cards**: Completed DAG turns in history hide individual step cards, showing only the summary bar
- **ThinkingCard Layout**: Icon moved from circular wrapper into inline Badge; removed excess left padding

### Fixed
- **DAG SSE Streaming**: EventSource now connects directly to backend, bypassing Next.js rewrite proxy that buffered SSE events (caused DAG progress to arrive in batches instead of frame-by-frame)
- **ReAct Interrupt Queue**: Added missing `await` to `InterruptQueue.drain()` calls in ReAct agent; without await, coroutine objects were returned instead of message lists, breaking mid-stream injection
- **DAG Early Stop**: Explicitly cancel all in-flight DAG step tasks when user hits Stop, preventing orphaned LLM calls and tool executions
- **Async Concurrency Hardening**: Added `asyncio.Lock` to `InterruptQueue` and `UsageSummary` aggregation; converted `DbMemory` to async-with sessions; re-raised `CancelledError` instead of suppressing; narrowed exception handling; bounded SSE queues to `maxsize=1000`; added `step_timeout` to `DAGExecutor`
- IME composition handling in chat input (Chinese/Japanese input no longer triggers premature submit)
- Playground scroll behavior and inject animation timing
- DAG skip button for running/pending steps during interrupt
- Remove excessive slide-in animations from playground, DAG, and step components
- Agent form custom checkbox styling and orphan resource display
- Resource ownership validation on agent binding and KB lookup
- Connector publish/unpublish confirmation dialogs (now removed)
- Evidence-utils citation regex matching for grounded retrieval
- Grounded retrieve tool fallback format when no evidence found
- Failed inject content now restored to input box with toast warning instead of being lost
- **Conversation model_name**: `model_name` now resolved eagerly at conversation creation time, eliminating "Unknown" entries in admin stats

## [0.6.1] — Connector Entity & Manual Builder

### Added
- **Connector + ConnectorAction ORM**: Database models for connectors and their actions
- **Connector CRUD API**: Full REST endpoints at `/api/connectors` and `/api/connectors/{id}/actions`
- **ConnectorToolAdapter**: Wraps ConnectorAction as BaseTool; HTTP proxy with path param substitution, auth injection (bearer with configurable prefix, API key with custom header, basic auth), default credential fallback from `auth_config`
- **Agent Connector Binding**: `connector_ids` field on Agent model; `_resolve_tools()` loads bound connectors and registers actions as tools
- **Connector Management UI**: List page, create/edit form, action editor (method, path, parameters schema)
- **Agent Form Connector Selector**: Multi-select connector binding in agent configuration (like KB binding)
- **Per-Auth-Type Config UI**: Bearer token prefix + default token, API key header name + default key, basic auth username/password

### Changed
- Database migration adds `connector` and `connector_action` tables, `connector_ids` column on agent

## [0.6.0] — Portal UX & Connector Platform Foundation

### Added
- **Dark / Light / System Theme**: `next-themes` provider with system-preference detection, manual toggle in Appearance settings, full component theming
- **Suggested Follow-ups**: Fast LLM generates 2-3 follow-up questions as clickable chips after agent completes
- **Conversational Interrupt**: Send messages while agent is running; injected at next iteration boundary; backend async queue per conversation; frontend inject mode with Stop button; messages persist as `message_type="inject"` for replay
- **Conversational Interrupt UI**: Inject event display, auto-focus, optimistic UI with SSE confirmation
- **MarkItDown Integration**: DOCX/XLSX/PPTX document loading via MarkItDown library
- **DAG Interrupt Skip**: Skip running/pending DAG steps during interrupt
- **Env-Driven Execution Limits**: Configurable `MAX_REACT_ITERATIONS` and `MAX_DAG_STEPS` via environment variables

### Changed
- Rebrand "Adapter" to "Connector", "Sidecar" to "Copilot" across codebase and docs
- Iteration card UI redesign: compact log-style cards with English summaries, tab switcher for Args/Obs, unified step/tool call display components

### Fixed
- Chats page: refetch conversation list after delete; add "All Chats" sidebar link
- Agent resolution hardening for edge cases

## [0.5.0] — RAG, Knowledge & Memory

### Added
- **LLM Compact**: Fast LLM compresses early conversation turns into a summary when history exceeds threshold; retain recent turns verbatim
- **DAG Re-Planning**: Auto re-plan when analyzer confidence < 0.5, up to 3 rounds; new SSE `replanning` phase event
- **ContextGuard**: Unified context window budget manager; hint-specific LLM compaction, oversized message truncation, smart fallback; wired into ReAct and DAG
- **Pinned Messages**: `ChatMessage.pinned` flag protects critical messages from compaction; three-way split (system / pinned / compactable); DAG steps receive `original_goal` context
- **DAG Multi-Turn Polish**: Structured message history for planner (replaces text prefix injection)
- **DAG LLM Compact**: Apply LLM compact to enriched planner query before planning
- **Tool-Name-Aware Planning**: Planner `tool_hint` constrained to available tool names
- **DAG Step-Level ReAct**: Each DAG step runs a full ReAct agent loop instead of single LLM call
- **DAG Step-Level Memory**: Step executors see relevant prior conversation context
- **DAG History Replay**: Frontend replays persisted DAG executions with flow graph
- **Embedding**: `BaseEmbedding` protocol + OpenAI-compatible implementation (Jina jina-embeddings-v3)
- **Vector Store**: LanceDB embedded vector store with native FTS, content-hash dedup
- **Document Loaders**: PDF, DOCX, Markdown, HTML, CSV, plain text
- **Chunking Strategies**: Fixed-size, recursive, and semantic chunking
- **Hybrid Retrieval**: Dense vector search + LanceDB native FTS + RRF fusion + Jina reranker
- **Knowledge Base Management**: Full KB CRUD with document upload, background ingest, agent `kb_retrieve` tool
- **Grounded Generation**: 5-stage evidence-anchored RAG pipeline — multi-KB retrieval, citation extraction, alignment scoring, conflict detection, confidence computation; inline `[N]` citation markers with References panel
- **Chunk-Level CRUD**: View, edit, delete individual chunks; inline chunk editor
- **KB Detail Page**: Dedicated page with document table and chunk browser
- **Markdown Document Creation**: Create markdown documents directly from KB UI
- **Citation References UI**: Structured references section with confidence badge, source names, page numbers, exact quotes
- **DAG Step Progress UI**: Visual progress indicator for DAG step execution
- **Per-User KB Isolation**: Knowledge bases scoped to the owning user
- **Conversation Starring & Pinning**: Star/pin conversations for quick access
- **Batch Delete Conversations**: Multi-select and delete conversations from sidebar
- **Chat Search (Cmd+K)**: Command palette for finding conversations by title or content
- **Conversation Rename**: Inline rename from sidebar
- **Global User Instructions**: Per-user system instructions injected into every agent conversation
- **Personal Center**: Settings page with user profile and preferences
- **Clipboard Image Paste**: Paste images from clipboard into chat input
- **Execution Mode Decoupling**: Separate agent execution mode from model configuration
- **Grounding Threshold**: Configurable confidence threshold for grounded generation

### Changed
- Sidebar UX overhaul with settings navigation
- Chunk size hardening and validation
- Analyzer retry logic for edge cases

### Fixed
- Format token counts as compact "k" notation in chat UI

## [0.4.0] — Platform Foundation

### Added
- **HTTP Request Tool**: General-purpose HTTP client with SSRF protection, JSON auto-pretty-print, 200KB response limit
- **Shell Exec Tool**: Sandboxed shell execution with command blocklist (30+ patterns), env var scrubbing, per-user sandbox directory
- **Persistent Storage**: SQLAlchemy ORM + SQLite with async WAL mode engine
- **Conversation Persistence**: Durable session history; `DbMemory` with smart truncation, CJK-aware token estimation, auto-compact
- **Multi-Tenant Auth**: User registration/login (JWT), SSE token-based auth, conversation ownership validation
- **Project & Agent Management**: Agent CRUD, tool/model/prompt binding per agent, agent-aware chat endpoints
- **File Upload & Management**: Upload/download/delete with per-user isolation
- **Drag-and-Drop File Upload**: Drop files onto chat input with visual overlay
- **Multi-Turn Conversation**: Full multi-turn context with smart truncation and thinking UX overhaul
- **SSE Abort/Disconnect**: Graceful SSE connection handling and client-initiated abort
- **DAG Horizontal Layout**: Horizontal orientation for DAG flow graph
- **Step Timing in Sidebar**: Elapsed duration display per step in sidebar

### Changed
- Token usage display moved to per-message in chat UI (backend tracking from v0.2)
- Sidebar resize behavior improvements

### Fixed
- Token count display formatted as compact "k" notation

## [0.3.0] — Rich Tool Ecosystem

### Added
- **Web Search Tool**: Jina-powered web search (`s.jina.ai`)
- **Web Fetch Tool**: Jina-powered HTTP reader (`r.jina.ai`) for clean page content extraction
- **DAG Visualization**: Interactive @xyflow/react flow graph with real-time step status, dependency edges, duration, iteration details
- **Right Sidebar Panel**: Persistent sidebar with expand/collapse toggle, auto fitView, DAG flow graph + ReAct timeline, click-to-scroll, responsive auto-hide
- **Env-Driven Configuration**: `LLM_TEMPERATURE` and `MAX_CONCURRENCY` via environment variables
- **Calculator Tool**: Safe AST-based math expression evaluation
- **File Ops Tool**: Sandboxed file read/write/list/mkdir with path traversal protection
- **Python Exec Sandbox**: Restricted builtins whitelist, blocked dangerous modules, stderr capture, 100KB output limit
- **Tool Auto-Discovery**: Convention-based loading — drop a `BaseTool` subclass in `builtin/` and it auto-registers
- **MCP Client**: Model Context Protocol integration via stdio transport; auto-discover and register external tools; configured via `MCP_SERVERS` env var
- **Tool Categories & Permissions**: Category tagging, `filter_by_category()`, `exclude_by_name()`, category-filtered `to_openai_tools()`
- **DAG Node Detail Enhancement**: Tool list, start time, and elapsed duration on each DAG step node
- **Frontend Token Usage**: Per-task input/output token counts in chat UI

### Fixed
- Parallel tool timing: correct elapsed-time measurement for concurrent tool calls in native function-calling mode
- ReAct agent JSON parsing reliability improvements

## [0.2.0] — Core Enhancements

### Added
- **Native Function Calling**: OpenAI-style `tool_choice` / `parallel_tool_calls` alongside ReAct JSON mode
- **Conversation Memory**: Short-term message window + conversation summary for multi-turn sessions
- **Retry & Rate Limiting**: Configurable retry with exponential backoff + token-bucket rate limiter
- **Token Usage Tracking**: Prompt/completion token counts across all LLM calls; per-task cost summary
- **Multi-Model Support**: ModelRegistry for multiple LLMs per project (general / fast / vision / compact)
- **Next.js Portal**: Interactive playground with FastAPI backend

## [0.1.0] — Foundation

### Added
- **ReAct Agent**: JSON-mode reasoning and action loop
- **DAG Planning**: DAGPlanner -> DAGExecutor -> PlanAnalyzer with concurrent step execution
- **Web UI Playground**: Interactive chat interface with DAG progress streaming
- **Real-Time Streaming**: Async queue bridge and `on_iteration` callbacks for both ReAct and DAG modes via SSE
- **KaTeX Math Rendering**: LaTeX math display in frontend
- **Step Timestamps**: Timing information on execution steps
- **Robust JSON Parsing**: Resilient parsing for LLM-generated JSON with fallback strategies

### Fixed
- Step dependency display improvements

### Fixed
- MCP stdio client: `RuntimeError: Attempted to exit cancel scope in a different task` — moved `connect_stdio()` from the request handler coroutine into the SSE generator body via `_connect_pending_mcp_servers()` helper, so anyio cancel scope enter/exit happen in the same coroutine
