# Parked

Work that is deliberately not being done.

This is not a backlog and nothing here is owed to anyone. The list exists so
that a request arriving in six months lands on a written starting point
instead of a blank page.

**Taking something off this list requires a person outside the project asking
for it by name, because they are blocked without it.** Not a survey answer,
not a hypothetical enterprise, not our own enthusiasm. Until that happens the
item stays here and `docs/roadmap.mdx` stays short.

Two notes on reading this file. It is not published to the docs site, so it
can be blunt. And the `dev/` design notes it references are local-only, kept
out of the repository, so every entry below carries enough context to be
picked up without them.

---

## Ecosystem and distribution

### Market Package System

The one the author actually wants to build, once an ecosystem exists to
receive it. Distributable resource bundles replacing the per-type marketplace
with a single packaging layer.

A `fim-package.yaml` manifest declares metadata (name, version, description,
author, license, tags, `min_fim_version`), an entry point (a primary Skill or
Agent), a resource list (agents, skills, connectors, knowledge bases, MCP
servers, workflows) with config references, inter-package dependencies as
semver ranges, required credentials mapped to connector refs for install-time
collection, and user-configurable variables with defaults.

Two consumption modes. **Install** batch-creates every resource and auto-wires
internal references by ID substitution, keeping a link to the source for
update notifications. **Fork** clones everything as user-owned editable copies
with no update link, which is the template mode. Around them: publish with a
review workflow, uninstall with a dependency check and modified-resource
confirmation, version history, and upgrade with a per-resource diff preview. A
`PackageInstallation` table tracks what each user installed, with the resource
ID mapping that uninstall and upgrade need. A dependency resolver handles
nested requirements and conflict detection.

Packages coexist with individual resource publishing rather than replacing it:
a single connector stays publishable on its own. Worked example of a
dependency tree: package `contract-review` → skill `contract-review` (entry
point) → agents `contract-analyst` and `risk-scorer` → knowledge base
`legal-clauses`, connector `docusign-api`, MCP server `pdf-extractor`,
workflow `contract-approval-flow`.

Marketplace Redesign Phase 1 already shipped (two-tier Solutions/Components
model, scope selector, unified subscriptions), so the substrate is in place.

**Trigger**: third-party authors publishing resources, or an installer who
needs more than one resource wired together.

### Creator Program

Monetization on top of packages: creator profiles with portfolio pages,
per-package analytics (installs, forks, active users, ratings), affiliate
commission when a package drives a subscription, a paid package tier with
pricing and purchase flow, a creator dashboard, a public API for programmatic
publishing, and community features (comments, Q&A, per-version changelogs).

**Trigger**: the Market Package System shipping and someone asking to be paid
for what they published. Strictly downstream of it.

### Hot-plug connectors

Upload an OpenAPI spec, have the AI generate the connector config, live in
five minutes with no restart. Connector-adjacent, so first in line if work
ever resumes on this list.

**Trigger**: onboarding time for a new upstream system becoming the thing that
loses a deal.

### MCP gateway output

Reverse-expose connector discover/execute as MCP tools, so an installed FIM
One turns a set of legacy systems into governed MCP tools that any agent
frontend can call.

**Trigger**: two or more unsolicited asks of the form "can I mount your tools
in my agent". Counting unsolicited asks is the point; do not count our own
speculation that someone might want this.

### Channelization / white-label

Selling the base to other implementors. The commercial-license path is already
in place, so this is packaging and enablement rather than engineering.

**Trigger**: an implementor asking about licensing.

---

## Delivery surfaces

Everything here is a new mouth on the existing kernel. The architectural rule
if any of them is ever built: reuse the same assembly layer for auth,
credentials, approval and metering. More frontends, never more logic.

- **JS bubble / iframe embed** — one snippet dropped into a host system.
  Blocked on a product decision, not on code: anonymous-visitor identity and
  billing attribution have to be settled before the first line (attribute to
  the widget key's owner?).
- **Page context injection** — the embedded widget reads host page context
  (current record ID, URL, DOM selectors). Only meaningful with the embed.
- **Feishu inbound @mention** — agents living in the group chat: query data,
  file approvals, chase flows. Outbound already ships. Inbound needs the
  cross-channel identity mapping that the Identity Provider module below
  would supply.
- **WeCom / DingTalk channels** — cheap once Feishu inbound exists; widens
  coverage rather than adding capability.
- **Outbound patterns** — failure alerts, budget warnings, scheduled digests,
  escalation, audit receipts. A content and template exercise on the shipped
  channel layer.
- **Advanced triggers** — inbound webhook events, and scheduled-job
  enhancements (multi-timezone, calendar-aware).

---

## Identity and organization

The cluster that felt heaviest and was never pulled by anyone.

- **Identity Provider module + Channel slim-down** — lift SSO, upstream OAuth
  tokens and org-graph sync out of Channel into a separate module with
  database-level config, leaving Channel with one job (notification and
  interaction gating). Would eventually cover GitHub, Google, Discord, Feishu,
  DingTalk, Azure AD, LDAP, SAML and custom OIDC.
- **OrgSync** — mirror a Feishu department tree into FIM organizations. Needs
  three things on the FIM side that do not exist: an org-tree write API with
  cycle detection, pre-creating members from an external identity, and a
  department column on membership.
- **Connector authorization Tier 2 and Tier 3** — Tier 2 requires per-user
  credentials with key-binding health; Tier 3 exchanges a login ticket for
  legacy systems that only offer username and password. Tier 1, the
  database fences, has shipped.
- **Enterprise security** — IP allowlisting, encryption at rest, SSO. The SSO
  half is the Identity Provider module; the rest is deployment configuration
  that a customer with the requirement would specify themselves.

**Trigger for all four**: a customer who cannot use the product without it.
Historically this cluster generated the most design documents and the least
shipped code, which is the signal that it was never pulled.

Two smaller items from the same area, left undone after the sharing-revocation
fixes in v0.9:

- **Withdrawing an approval** — a reviewer can approve or reject a submission
  while it is pending, but there is no way to revoke an approval already
  granted; the only route back is asking the owner to unpublish. The
  visibility filter already refuses anything whose `publish_status` is not
  `approved`, so the missing half is one endpoint and its audit entry.
  **Trigger**: a reviewer who has to take something down and cannot reach its
  owner.
- **Market browse pagination** — the listing reads every published row of all
  five resource types for the scope into memory and slices the requested page
  in Python. Correct, and linear in catalogue size. **Trigger**: a Market
  catalogue large enough for the listing to be slow, which needs more
  published resources than exist today.

---

## Billing beyond what ships today

Stripe billing is live through admin plan management and the feature flag.
Only go-live remains, and that stays on the roadmap because it finishes
something already in the codebase. These three do not:

- **Billing access model** — an instance picks one of three postures:
  no subscriptions, included plus paid, or paid-only, so self-host, SaaS and
  charge-from-day-one stay distinct.
- **Team plan (Stripe seats)** — per-seat pricing via subscription quantity,
  resolved through organization membership so quota and flags come from the
  seat group rather than the individual.
- **Group-level token quota for deployments without Stripe** — organization
  budgets with the quota chain extended to override > group > plan > default,
  and group resolution taking the max so a VIP is not capped by the team.

---

## Agent core architecture program

Two study-driven refactoring programs, roughly a quarter of work each, both
written up in local `dev/dsh-lessons/` and `dev/codex-lessons/` notes. They
were gated behind the v0.9 cut, which has now happened, and they are parked
rather than started.

What they would deliver, in one line each:

- **Governance** — a bug-class defensive-patterns document and design-note
  lifecycle states. The markdown link gate from this program was built
  separately and already runs in pre-commit and CI.
- **Session event log** — every model-visible input becomes a durable ordered
  fact, with the message history derived from it rather than assembled ad hoc.
- **Runtime invariants and keyless snapshot replay** — assert owned
  relationships in production, and diff assembled transcripts in CI without an
  API key.
- **Tool pipeline seams** — pre/execute/post stages, so permission, timeout,
  sandbox and background handling leave the agent loop. Prerequisite for
  collapsing the two parallel loop implementations in `react.py`.
- **Typed context fragments and world-state sections** — replace f-string
  injection sites with typed classes that declare their own caps, and send
  changed state as a diff instead of re-sending it.
- **Code Mode preset** — one generated program composes several connector
  calls through the same pipeline, replacing multi-round-trip orchestration.
  Read-only first; the approval story for a program that writes 500 records is
  unanswered.
- **Policy planes and automated approval** — resolved values that carry which
  layer set them, and a reviewer that can clear low-risk actions with no human
  present.
- **Typed frontend from OpenAPI** — `docs/openapi.json` is already exported
  and nothing consumes it, while `frontend/src/lib/api.ts` carries roughly 50
  hand-written interfaces that drift by construction.

The last one is the cheapest and the only one with a standing cost, so it is
the one to pick up first if any of this restarts.

**Trigger**: a defect class that keeps recurring and is traceable to one of
these gaps. Not "the code would be nicer".

---

## Platform depth

Extensions to shipped subsystems. None has a requester.

- **Connector Progressive Disclosure Phase 3-5** — YAML/JSON connector config,
  scale mode for batch and ETL connectors, a CLI-style universal
  `connector <name> <action> <params>` interface, and semantic-guided tool
  selection (entity extraction from the query, ontology lookup, connector-set
  reduction) aimed at deployments with 50+ connectors.
- **Cross-connector entity alignment (Ontology Registry)** — shared entity
  types with field mappings so the planner resolves cross-system join keys
  without hardcoded field names. Downgraded in April 2026 to on-demand custom
  delivery rather than a core capability; that ruling stands.
- **DB connectors Phase 4** — Oracle, SQL Server and GBase drivers. Dameng and
  KingbaseES already ship.
- **MCP connection pooling** and **sandbox hardening v2**.
- **Batch execution** — 1000+ items through the DAG engine.
- **KB Advanced Editor** — a builder-mode agent for large knowledge bases:
  bulk URL ingestion, duplicate detection, gap analysis, document lifecycle.
- **Guardrails v1** — off-topic filter, PII redactor as an output guardrail,
  per-agent guardrail config UI. v0 (tripwire layer, jailbreak detector)
  shipped.
- **Hook system extras** — built-in hooks, user-supplied YAML hooks, and
  per-hook config pass-through. The last one existed only to carry scope rules
  into a hook-based connector fence; the fences shipped without hooks, so it
  now has no consumer.
- **Public API Phase 2** — per-key rate limits and quotas, versioning, SDKs, a
  developer portal.
- **Observability** — the Agent Trace Layer (trace/span model, timeline
  viewer, OpenTelemetry export) plus a metrics dashboard. Repeatedly described
  internally as a commercial anchor, never requested by anyone.
- **Agent Workspace remainder** — handoff notes, file browser UI,
  cross-session recall, and grep-able on-disk compaction segments. Chat wiring
  already shipped.
- **Schema-gated structured output (T2)** — `response_format:
  {"type": "json_schema"}` plus `strict: true` on native function calling, so
  `structured_llm_call` gains a rung that enforces fields rather than only
  syntax. Today the chain is non-strict function calling → `json_object` →
  plain text, and the middle guarantee tier is missing entirely; the tiers and
  per-provider support are written up in
  `docs/architecture/llm-provider-guide.mdx`. Parked because every call site
  but one passes `default_value`, so a failed extraction already degrades to a
  safe default rather than an error, and consumers re-validate the fields they
  care about by hand. The cost is not one branch: it needs a per-model
  capability flag (`abilities` + a `model_configs` column + migration + admin
  toggle) because only about half the supported providers offer the tier, and
  strict mode's schema subset (`additionalProperties: false`, every field in
  `required`) would mean rewriting most schemas in the tree.
  **Trigger**: the `structured_llm_call` level logs show a non-trivial share of
  traffic landing on `plain_text` or `default_value`, or a deployment
  standardises on models whose thinking mode blocks forced tool choice (GLM,
  Kimi thinking, `deepseek-reasoner`), which closes the T1 door and leaves T2
  as the only schema-gated path.
- **Prompt cache follow-ups** — a Gemini context-cache adapter and per-agent
  `cache_ttl`.
- **Hot mid-stream DAG resume** — SSE reconnect re-attaches to a running turn.
  Cold retry-resume already ships, which covers the failure users actually
  hit.
- **Scheduled and event-triggered agents**, workflow trigger-identity
  observability, per-workflow `credential_policy`, DB Schema Advanced Builder.
- **Scenario onboarding** — first run starting from a scenario template
  instead of an empty workbench, a docs landing page led by three vertical
  stories, and one template distilled per delivered engagement. Parked because
  the templates are supposed to come out of real deliveries; writing them
  first is the closed-room problem this whole list exists to stop.
- **DAG evidence fidelity** — three deferred hardening items around how source
  evidence reaches the DAG analyzer and synthesis steps: give evidence its own
  truncation budget instead of re-clipping it with `DAG_ANALYZER_TRUNCATION`;
  truncate structure-aware (head plus tail, keep lists and tables) so a long
  enumeration does not silently lose its tail; and port the source-fidelity
  guideline into the ReAct fallback synthesis prompt, which today catches
  total and severity mislabels only on the DAG path. Held a low-priority
  backlog section on the roadmap for a while without anyone hitting the
  matching scenario. **Trigger**: a report where the numbers came back wrong
  and the cause traces to clipped evidence.

---

## Considered and declined

These were evaluated and turned down on the merits, mostly because the
industry is absorbing them. They are a weaker form of parked: the trigger is
not just a request but a request plus evidence the absorption argument was
wrong.

| Item | Why not |
|---|---|
| Multi-agent orchestration (deep hierarchies) | Providers are building this natively. One-level delegation is covered by the existing call-agent tool. |
| Agent self-modifying skills | An agent rewriting its own skill file during execution. High complexity and a large safety and audit surface for an unrequested capability. |
| Cross-session long-term memory | Context windows are growing fast and providers are adding built-in memory. High cost, shrinking differentiation. |
| Memory lifecycle (TTL, quotas) | Depends on cross-session memory; declined with it. |
| Agent-triggered context compression | Frozen alongside ContextGuard. Large context windows removed most of the value. |
| Browser automation / computer use | High maintenance (DOM churn, anti-bot, sandboxing) and the industry is converging on computer-use modes and browser MCP servers. Consume via MCP rather than self-build. |
| Web push notifications | Overlaps the IM channel layer, which enterprises prefer anyway. |
| Multi-user collaborative workflow editing | Real-time co-editing needs CRDT or OT plus presence infrastructure. Today's one-editor-plus-version-diff model has drawn no complaints. |
| Per-node workflow execution permissions | A third authorization axis on top of workflow-level and connector-level, with material complexity and no request. |
| Cross-org workflow sharing with live updates | Subscribe currently means fork, so upstream breakage never propagates. Live updates need schema evolution and conflict resolution. |

---

## Questions that only matter if something above unparks

Recorded so they are not re-derived later. None needs an answer now.

- Marketplace moderation: how are community packages validated, and is
  automated scanning for leaked credentials in package configs required?
- Package versioning: do breaking changes in an installed package auto-upgrade
  with migration scripts or require per-update approval, and how is the
  dependency diamond resolved?
- Package pricing: free versus paid tiers, commission rate, payment provider.
- Package credential UX: install-time collection as a wizard or deferred
  setup, and whether credentials are shared across packages using the same
  connector type.
- Token economics for multi-user, multi-agent scenarios.
- Connector authorization tier selection: does an admin discover the
  applicable tier by auto-probe or explicit declaration, and how is "supports
  Tier 2 but operating in Tier 1" shown without confusing a non-technical
  admin?
- Integration versus connector duality: when a Feishu binding is at once an
  SSO provider and an API surface, is it one object with three toggles or
  three bindings sharing a credential, and does revoking SSO kill the
  connector?
- Per-connector and per-agent rate limiting. Per-user workflow rate limiting
  already ships.
- Telemetry opt-out, and connector versioning across breaking upstream API
  changes.
