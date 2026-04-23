# Translation Glossary

Single source of truth for translation rules. Loaded by `scripts/translate.py`
and injected into every LLM system prompt (JSON, MDX, README).

## How this works

When you spot a mistranslation (wrong term, inconsistent vocabulary, brand name
translated, etc.) in any locale file, **do not manually edit the locale file**.
Those edits are overwritten on the next `translate.py` run.

Instead:

1. Add or update a rule in this file.
2. Commit the change.
3. The next `translate.py` run (local hook or CI) applies the new rule to all
   five locales.
4. To retroactively fix existing translations that predate your new rule, run:
   `uv run scripts/translate.py --files <affected files> --force`

The glossary travels with the repo. Every contributor's translation obeys the
same rules. Nothing is ever manually tweaked in locale files.

---

## NEVER TRANSLATE — keep as English

These terms must appear verbatim in every locale. They are product names,
industry-standard technical terms, or code identifiers.

### Brand and product
- FIM One, FIM
- Feishu, Lark (platform names)
- Exa
- DeepSeek, OpenAI, Anthropic, Claude, GPT, Gemini
- KingbaseES, HighGo, DM8 (Xinchuang database names — their Chinese brand names
  may appear in parentheses but the English identifier stays)

### Technical standards and protocols
- API, REST, GraphQL, gRPC
- MCP, JSON-RPC, SSE, WebSocket, HTTP, HTTPS
- OAuth, OAuth2, SSO, JWT, SAML, OIDC
- JSON, YAML, TOML, XML, Markdown, MDX
- URL, URI, UUID, CLI, SDK
- CORS, CSRF, XSS

### Frameworks and runtimes
- Python, TypeScript, JavaScript, Node.js
- FastAPI, SQLAlchemy, Pydantic, Alembic, Uvicorn
- Next.js, React, shadcn, shadcn/ui, Radix, Tailwind, Tailwind CSS
- PostgreSQL, SQLite, MySQL, Redis
- Docker, Kubernetes, k8s
- pnpm, npm, uv, pip

### AI / agent technical terms
- LLM, RAG, ReAct, DAG
- Token, tokens (context-dependent — keep as `token` in technical contexts;
  see "translatable AI terms" below for `prompt`, `temperature` etc.)

### Code symbols (any CamelCase or snake_case project identifier)
- All class names: `FeishuGateHook`, `PreToolUseHook`, `ConfirmationRequest`,
  `AgentConfig`, `ConnectorAction`, etc.
- All function names in backticks: `build_confirmation_card()`, etc.
- All CLI flags: `--all`, `--force`, `--files`, `--locale`, `--no-verify`
- All environment variables: `LLM_API_KEY`, `FAST_LLM_MODEL`, etc.
- File paths: `frontend/messages/en/`, `docs/*.mdx`, etc.

---

## CANONICAL TRANSLATIONS

When translating the following domain terms, use the specified translation.
Do not vary by section or by translator preference.

### Chinese (zh)

| English | 中文 | Notes |
|---|---|---|
| agent | 智能体 | NEVER "代理"、"特工" |
| connector | 连接器 | NEVER "连接符"、"接口" |
| channel | 通道 | NEVER "频道" (consumer-app vocabulary mismatch) |
| hook | 钩子 | Technical term, OK to translate |
| tool (in agent context) | 工具 | NEVER "道具" |
| tool call | 工具调用 | |
| confirmation | 确认 | |
| approval | 审批 | NEVER "批准" when used as noun |
| approver | 审批人 | |
| playground | 演练场 | NEVER "游乐场" |
| portal | 门户 | |
| workspace | 工作区 | |
| sandbox | 沙箱 | |
| organization / org | 组织 | |
| tenant | 租户 | |
| admin / administrator | 管理员 | |
| owner | 所有者 | |
| member | 成员 | |
| dashboard | 仪表板 | |
| integration | 集成 | as noun; verb form: 集成 |
| solution | 解决方案 | |
| package | 套餐 (when a commercial bundle) / 包 (when a software package) | context-aware |
| roadshow | 路演 | |
| field test | 实地测试 | |
| contributor | 贡献者 | |
| pioneer | 先锋 | |
| founding contributor | 创始贡献者 | |
| prompt | 提示词 | |
| temperature / temp | 温度 | LLM sampling temperature, NEVER "临时" |
| reasoning | 推理 | |
| inference | 推理 | |
| embedding | 嵌入 | |
| fine-tuning | 微调 | |
| context window | 上下文窗口 | |
| hallucination | 幻觉 | |
| provider | 提供商 | LLM provider context |
| model | 模型 | |
| streaming | 流式 | as adjective; 流式传输 as noun |
| chunk | 分块 | |
| confirmation gate | 确认闸门 | |
| approval gate | 审批闸门 | |
| locale | 语言环境 | |
| i18n | 国际化 | spell out when it reads better |

### Japanese (ja)
Use standard technical Japanese. Keep all ENGLISH in the "NEVER TRANSLATE"
list above. For AI terms, use established Japanese technical vocabulary
(e.g. `埋め込み` for embedding, `推論` for inference, `微調整` for fine-tuning).

### Korean (ko)
Use standard technical Korean. Keep all ENGLISH in the "NEVER TRANSLATE"
list above. For AI terms, use established Korean technical vocabulary
(e.g. `임베딩` for embedding, `추론` for inference, `미세조정` for fine-tuning).

### German (de)
Prefer German terms where well-established (e.g. `Einbettung` for embedding,
`Inferenz` for inference, `Feinabstimmung` for fine-tuning), but keep English
for industry-standard identifiers.

### French (fr)
Prefer French terms where well-established (e.g. `intégration` for embedding,
`inférence` for inference, `réglage fin` for fine-tuning), but keep English
for industry-standard identifiers.

---

## STYLE RULES

Apply to all locales unless a language has a contradictory convention.

1. **Technical tone**: professional but not stiff. Match the register of
   modern developer documentation (think Vercel, Stripe, Linear docs).
2. **No literal word-for-word translation**. Prioritize natural reading in
   the target language over preserving English sentence structure.
3. **Preserve code symbols, placeholders, and MDX/JSX structure exactly**.
   This includes `<!--CODE_BLOCK_N-->` placeholders, `{variable}` tokens,
   `<Component />` tags, code fences, frontmatter keys.
4. **Do not add content** that isn't in the source. No editorial flourishes.
5. **Chinese-specific**:
   - Use full-width punctuation for Chinese text: `，。：；！？（）`.
   - Use `——` (full-width em dash) for parenthetical breaks, not `--` or `—`.
   - No space between Chinese characters and adjacent ASCII, except when the
     ASCII token is in backticks or is a standalone English term.
   - Example: "使用 `uv run pytest`" (space around backtick is OK) but
     "使用uv和pnpm" when flowing prose (no spaces).
6. **Headings**: translate the text content but preserve `#` depth and any
   anchor syntax. Never introduce a new heading level.
7. **Frontmatter**: translate only `title`, `description`, `sidebarTitle`.
   Leave all other keys and values untouched.
