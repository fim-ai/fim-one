<div align="center">

![FIM One Banner](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![GitHub stars](https://img.shields.io/github/stars/fim-ai/fim-one?style=social)](https://github.com/fim-ai/fim-one/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/fim-ai/fim-one?style=social)](https://github.com/fim-ai/fim-one/network)
[![GitHub issues](https://img.shields.io/github/issues/fim-ai/fim-one)](https://github.com/fim-ai/fim-one/issues)
[![Follow on X](https://img.shields.io/twitter/follow/FIM_One?style=social)](https://x.com/FIM_One)
[![Discord](https://img.shields.io/discord/1480638265206771742?logo=discord&label=discord)](https://discord.gg/z64czxdC7z)
[![Product Hunt](https://img.shields.io/badge/Product%20Hunt-FIM%20One-DA552F?logo=producthunt&logoColor=white)](https://www.producthunt.com/products/fim-one)

[🌐 English](README.md) | [🇨🇳 中文](README.zh.md) | [🇯🇵 日本語](README.ja.md) | [🇰🇷 한국어](README.ko.md) | [🇩🇪 Deutsch](README.de.md) | [🇫🇷 Français](README.fr.md)

**您的系统无法相互通信。FIM One 将它们全部连接到 AI — 零代码更改，零数据迁移。**

*AI 驱动的连接器中心 — 作为 Copilot 嵌入到一个系统中，或作为中心连接所有系统。*

🌐 [网站](https://one.fim.ai/) · 📖 [文档](https://docs.fim.ai) · 📋 [更新日志](https://docs.fim.ai/changelog) · 🐛 [报告错误](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

> [!TIP]
> **☁️ 跳过设置 — 在云上尝试 FIM One。**
> 托管版本现已在 **[cloud.fim.ai](https://cloud.fim.ai/)** 上线：无需 Docker，无需 API 密钥，无需配置。登录并在几秒内开始连接您的系统。_早期访问，欢迎反馈。_

---

## 目录

- [概述](#overview)
- [用例](#use-cases)
- [为什么选择 FIM One](#why-fim-one)
- [FIM One 的定位](#where-fim-one-sits)
- [主要功能](#key-features)
- [架构](#architecture)
- [快速开始](#quick-start)（Docker / 本地 / 生产环境）
- [配置](#configuration)
- [开发](#development)
- [路线图](#roadmap)
- [贡献](#contributing)
- [Star 历史](#star-history)
- [活动](#activity)
- [贡献者](#contributors)
- [许可证](#license)## 概述

每家公司都有相互不通的系统——ERP、CRM、OA、财务、HR、自定义数据库。每个厂商的 AI 在自己的领地内很聪明，但对其他一切都一无所知。FIM One 是**外部、第三方中枢**，通过 AI 将它们全部连接起来——无需修改现有基础设施。三种交付模式，一个智能体核心：

| 模式           | 定义                                                                       | 访问方式                       |
| -------------- | -------------------------------------------------------------------------------- | --------------------------------------- |
| **独立版** | 通用 AI 助手——搜索、代码、知识库                      | 门户网站                                  |
| **副驾驶**    | 嵌入宿主系统的 AI——在用户现有 UI 中与用户并肩工作        | iframe / 小部件 / 嵌入宿主页面 |
| **中枢**        | 中央 AI 编排——所有系统互联，跨系统智能 | 门户网站 / API                            |

```mermaid
graph LR
    ERP --> Hub["FIM One Hub<br/>(AI orchestration)"]
    Database --> Hub
    Lark --> Hub
    CRM --> Hub
    OA --> Hub
    API[Custom API] --> Hub
```

核心始终如一：ReAct 推理循环、支持并发执行的动态 DAG 规划、可插拔工具，以及协议优先架构，零厂商锁定。

### 使用 Agents

![Using Agents](https://github.com/user-attachments/assets/b03d7750-eae6-4b16-9242-4c500d53d6cf)### 使用规划器模式

![Using Planner Mode](https://github.com/user-attachments/assets/2b630496-2e62-4e14-bbdf-b8c707258390)## 用例

企业数据和工作流被锁定在OA、ERP、财务和审批系统中。FIM One让AI代理读写这些系统——自动化跨系统流程，无需修改现有基础设施。

| 场景                  | 推荐起点 | 自动化内容                                                                                                |
| ------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------- |
| **法律与合规**    | Copilot → Hub     | 合同条款提取、版本对比、风险标记（含来源引用）、自动触发OA审批          |
| **IT运维**         | Hub               | 告警触发 → 日志拉取 → 根本原因分析 → 修复派发至Lark/Slack——一个完整闭环                 |
| **业务运营**   | Copilot           | 定时数据摘要推送至团队频道；针对实时数据库的即席自然语言查询         |
| **财务自动化**    | Hub               | 发票验证、费用审批路由、ERP和会计系统间的账本对账          |
| **采购**           | Copilot → Hub     | 需求 → 供应商对比 → 合同草稿 → 审批——Agent处理跨系统交接           |
| **开发者集成** | API               | 导入OpenAPI规范或在聊天中描述API——connector在几分钟内创建，自动注册为agent工具 |## 为什么选择 FIM One### 逐步扩展

从在一个系统中嵌入一个 **Copilot** 开始——比如你的 ERP。用户可以直接在熟悉的界面中与 AI 交互：查询财务数据、生成报告、获取答案，无需离开页面。

当价值得到验证后，建立一个 **Hub** ——一个连接所有系统的中央门户。ERP Copilot 继续运行嵌入式应用；Hub 添加跨系统编排功能：在 CRM 中查询合同、在 OA 中检查审批、在 Lark 上通知利益相关者——所有操作都可以在一个地方完成。

Copilot 在一个系统中证明价值。Hub 在所有系统中释放价值。### FIM One 不做什么

FIM One 不复制目标系统中已存在的工作流逻辑：

- **无 BPM/FSM 引擎** — 审批链、路由、升级和状态机是目标系统的责任。这些系统花费多年构建这些逻辑。
- **无 BPM/FSM 工作流引擎** — FIM One 的工作流蓝图是自动化模板（LLM 调用、条件分支、连接器操作），而非业务流程管理。审批链、路由规则和状态机应属于目标系统。
- **连接器 = API 调用** — 从连接器的角度看，"转移审批" = 一个 API 调用，"拒绝并说明原因" = 一个 API 调用。所有复杂的工作流操作都归结为 HTTP 请求。FIM One 调用 API；目标系统管理状态。

这是一个刻意的架构边界，而非能力缺陷。

### 竞争定位

|                        | Dify                       | Manus            | Coze                  | FIM One                      |
| ---------------------- | -------------------------- | ---------------- | --------------------- | ---------------------------- |
| **方法**           | 可视化工作流构建器    | 自主代理 | 构建器 + 代理空间 | AI 连接器中心             |
| **规划**           | 人工设计的静态 DAG | 多代理 CoT  | 静态 + 动态      | LLM DAG 规划 + ReAct     |
| **跨系统**       | API 节点（手动）         | 否               | 插件市场    | Hub 模式（N:N 编排） |
| **人工确认** | 否                         | 否               | 否                    | 是（执行前门控）     |
| **自托管**        | 是（Docker 堆栈）         | 否               | 是（Coze Studio）     | 是（单进程）         |

> 深入了解：[Philosophy](https://docs.fim.ai/architecture/philosophy) | [Execution Modes](https://docs.fim.ai/concepts/execution-modes) | [Competitive Landscape](https://docs.fim.ai/strategy/competitive-landscape)### FIM One 的定位

```
                Static Execution          Dynamic Execution
            ┌──────────────────────┬──────────────────────┐
 Static     │ BPM / Workflow       │ ACM                  │
 Planning   │ Camunda, Activiti    │ (Salesforce Case)    │
            │ Dify, n8n, Coze     │                      │
            ├──────────────────────┼──────────────────────┤
 Dynamic    │ (transitional —      │ Autonomous Agent     │
 Planning   │  unstable quadrant)  │ AutoGPT, Manus       │
            │                      │ ★ FIM One (bounded)│
            └──────────────────────┴──────────────────────┘
```

Dify/n8n 属于**静态规划 + 静态执行** — 人类在可视化画布上设计 DAG，节点执行固定操作。FIM One 属于**动态规划 + 动态执行** — LLM 在运行时生成 DAG，每个节点运行 ReAct 循环，当目标未达成时进行重新规划。但受到限制（最多 3 轮重新规划、token 预算、确认门控），因此比 AutoGPT 更可控。

FIM One 不做 BPM/FSM — 工作流逻辑属于目标系统，Connectors 只是调用 API。

> 完整说明：[Philosophy](https://docs.fim.ai/architecture/philosophy)## 主要特性#### 连接器平台（核心）
- **连接器中心架构** — 独立助手、嵌入式 Copilot 或中央 Hub — 相同的 agent 核心，不同的交付方式。
- **任何系统，一种模式** — 连接 API、数据库和消息总线。操作自动注册为 agent 工具，支持身份验证注入（Bearer、API Key、Basic）。
- **数据库连接器** — 直接 SQL 访问 PostgreSQL、MySQL、Oracle、SQL Server 和中国国产数据库（DM、KingbaseES、GBase、Highgo）。支持模式内省、AI 驱动的注释、只读查询执行和静态加密凭证。每个数据库连接器自动生成 3 个工具（`list_tables`、`describe_table`、`query`）。
- **构建连接器的三种方式：**
  - *导入 OpenAPI 规范* — 上传 YAML/JSON/URL；连接器和所有操作自动生成。
  - *AI 聊天构建器* — 用自然语言描述 API；AI 在对话中生成和迭代操作配置。10 个专业构建器工具处理连接器设置、操作、测试和 agent 接线。
  - *MCP 生态系统* — 直接连接任何 MCP 服务器；第三方 MCP 社区开箱即用。#### 智能规划与执行
- **动态 DAG 规划** — LLM 在运行时将目标分解为依赖图。无硬编码工作流。
- **并发执行** — 独立步骤通过 asyncio 并行运行。
- **DAG 重新规划** — 当目标未达成时，自动修订计划，最多 3 轮。
- **ReAct 代理** — 具有自动错误恢复的结构化推理和行动循环。
- **自动路由** — 自动查询分类将每个请求路由到最优执行模式（ReAct 或 DAG）。前端支持三向切换（Auto/Standard/Planner）。可通过 `AUTO_ROUTING` 配置。
- **扩展思考** — 通过 `LLM_REASONING_EFFORT` 为支持的模型（OpenAI o-series、Gemini 2.5+、Claude）启用思维链推理。模型的推理过程在 UI 的"thinking"步骤中显示。#### 工作流蓝图
- **可视化工作流编辑器** — 使用基于 React Flow v12 的拖放画布设计多步骤自动化蓝图。12 种节点类型：开始、结束、LLM、条件分支、问题分类器、智能体、知识检索、连接器、HTTP 请求、变量赋值、模板转换、代码执行。
- **拓扑执行引擎** — 工作流按依赖顺序执行节点，支持条件分支、跨节点变量传递和实时 SSE 状态流。
- **导入/导出** — 将工作流蓝图作为 JSON 共享。加密环境变量以实现安全的凭证处理。

#### 工具与集成
- **可插拔工具系统** — 自动发现；内置 Python 执行器、Node.js 执行器、计算器、网络搜索/获取、HTTP 请求、shell 执行等。
- **可插拔沙箱** — `python_exec` / `node_exec` / `shell_exec` 在本地或 Docker 模式（`CODE_EXEC_BACKEND=docker`）中运行，实现操作系统级隔离（`--network=none`、`--memory=256m`）。适合 SaaS 和多租户部署。
- **MCP 协议** — 将任何 MCP 服务器连接为工具。第三方 MCP 生态系统开箱即用。
- **工具制品系统** — 工具生成丰富的输出（HTML 预览、生成的文件），支持聊天内渲染和下载。HTML 制品在沙箱 iframe 中渲染；文件制品显示下载芯片。
- **OpenAI 兼容** — 适用于任何 `/v1/chat/completions` 提供商（OpenAI、DeepSeek、Qwen、Ollama、vLLM…）。#### RAG & 知识库
- **完整 RAG 管道** — Jina embedding + LanceDB + FTS + RRF 混合检索 + reranker。支持 PDF、DOCX、Markdown、HTML、CSV。
- **有根据的生成** — 证据锚定的 RAG，带有内联 `[N]` 引用、冲突检测和可解释的置信度分数。
- **知识库文档管理** — 块级 CRUD、跨块文本搜索、失败文档重试和自动迁移向量存储模式。#### Portal & UX
- **Real-time Streaming (SSE v2)** — Split event protocol (`done` / `suggestions` / `title` / `end`) with streaming dot-pulse cursor, KaTeX math rendering, and tool step folding.
- **DAG Visualization** — Interactive flow graph with live status, dependency edges, click-to-scroll, and re-plan round snapshots as collapsible cards.
- **Conversational Interrupt** — Send follow-up messages while the agent is running; injected at the next iteration boundary.
- **Dark / Light / System Theme** — Full theme support with system-preference detection.
- **Command Palette** — Conversation search, starring, batch operations, and title rename.#### 平台与多租户
- **JWT 认证** — 基于令牌的 SSE 认证、对话所有权、按用户资源隔离。
- **智能体管理** — 创建、配置和发布绑定了模型、工具和指令的智能体。按智能体执行模式（标准/规划器）和温度控制。可选的 `discoverable` 标志启用 LLM 通过 CallAgentTool 自动发现。
- **智能体技能系统** — 智能体的渐进式指令加载。技能（SOP、脚本、领域知识）在系统提示中作为轻量级存根引用（每个约 15 个令牌）；智能体调用 `read_skill(name)` 按需加载完整内容，将每次对话的指令令牌成本降低约 80%。按智能体的 `compact_instructions` 字段用于自定义 ContextGuard 压缩策略。
- **市场（影子市场组织）** — 内置市场组织作为不可见的后端实体用于资源共享。资源通过市场浏览发现并显式订阅（拉取模型）— 无自动加入成员资格。发布到市场始终需要审核。
- **资源订阅** — 用户浏览并订阅来自市场的共享资源。通过 UI 或 API 订阅/取消订阅。所有资源类型（智能体、连接器、知识库、MCP 服务器、技能、工作流）都支持市场发布和订阅管理。
- **管理面板** — 系统统计仪表板（用户、对话、令牌、模型使用图表、按智能体的令牌分解）、连接器调用指标（成功率、延迟、调用计数）、用户管理（搜索/分页）、角色切换、密码重置、账户启用/禁用和按工具启用/禁用控制。
- **首次运行设置向导** — 首次启动时，门户引导您创建管理员账户（用户名、密码、电子邮件）。这个一次性设置成为您的登录凭证 — 无需配置文件。
- **个人中心** — 按用户的全局系统指令，应用于所有对话。
- **语言偏好** — 按用户的语言设置（自动/en/zh），指导所有 LLM 响应使用所选语言。

#### 上下文与内存
- **LLM Compact** — 自动 LLM 驱动的摘要总结，保持在令牌预算范围内。
- **ContextGuard + Pinned Messages** — 令牌预算管理器；固定消息受保护，不会被压缩。
- **双数据库支持** — SQLite（零配置默认选项）可在几秒内快速启动；PostgreSQL 用于生产环境和多工作进程部署。Docker Compose 自动配置 PostgreSQL 并进行健康检查。`docker compose up` 即可启动运行。## 架构### 系统概览

```mermaid
graph TB
    subgraph app["Application & Interaction Layer"]
        a["Portal · API · iframe · Lark/Slack Bot · Webhook · WeCom/DingTalk"]
    end
    subgraph mid["FIM One Middleware"]
        direction LR
        m1["Connectors<br/>+ MCP Hub"] ~~~ m2["Orch Engine<br/>ReAct / DAG"] ~~~ m3["RAG /<br/>Knowledge"] ~~~ m4["Auth /<br/>Admin"]
    end
    subgraph biz["Business Systems & Data Layer"]
        b["ERP · CRM · OA · Finance · Databases · Custom APIs<br/>Lark · DingTalk · WeCom · Slack · Email · Webhook"]
    end
    app --> mid --> biz
```### Connector Hub

```mermaid
graph LR
    ERP["ERP<br/>(SAP/Kingdee)"] --> A
    CRM["CRM<br/>(Salesforce)"] --> B
    OA["OA<br/>(Seeyon/Weaver)"] --> C
    DB["Custom DB<br/>(PG/MySQL)"] --> D
    subgraph Hub["FIM One Hub"]
        A["Agent A: Finance Audit"]
        B["Agent B: Contract Review"]
        C["Agent C: Approval Assist"]
        D["Agent D: Data Reporting"]
    end
    A --> O1["Lark / Slack"]
    B --> O2["Email / WeCom"]
    C --> O3["Teams / Webhook"]
    D --> O4["Any API"]
```

*Portal / API / iframe*

每个 connector 都是一个标准化的桥接——agent 不需要知道或关心它是在与 SAP 还是自定义 PostgreSQL 数据库通信。详见 [Connector Architecture](https://docs.fim.ai/architecture/connector-architecture)。### 内部执行

FIM One 提供两种执行模式，具有自动路由功能：

| 模式         | 最适用于                  | 工作原理                                                       |
| ------------ | ------------------------- | ------------------------------------------------------------------ |
| Auto         | 所有查询（默认）     | 快速 LLM 分类查询并路由到 ReAct 或 DAG           |
| ReAct        | 单个复杂查询    | 推理 → 行动 → 观察循环与工具                             |
| DAG Planning | 多步骤并行任务 | LLM 生成依赖图，独立步骤并发运行 |

```mermaid
graph TB
    Q[User Query] --> P["DAG Planner<br/>LLM decomposes the goal into steps + dependency edges"]
    P --> E["DAG Executor<br/>Launches independent steps concurrently via asyncio<br/>Each step is handled by a ReAct Agent"]
    E --> R1["ReAct Agent 1 → Tools<br/>(python_exec, custom, ...)"]
    E --> R2["ReAct Agent 2 → RAG<br/>(retriever interface)"]
    E --> RN["ReAct Agent N → ..."]
    R1 & R2 & RN --> An["Plan Analyzer<br/>LLM evaluates results · re-plans if goal not met"]
    An --> F[Final Answer]
```## 快速开始### 选项 A：Docker（推荐）

无需本地 Python 或 Node.js — 所有内容都在容器内构建。

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one
```# 配置 — 仅需要 LLM_API_KEY
cp example.env .env# 编辑 .env: 设置 LLM_API_KEY (以及可选的 LLM_BASE_URL、LLM_MODEL)# 构建和运行（首次运行或拉取新代码后）
```bash
docker compose up --build -d
```

打开 http://localhost:3000 — 首次启动时，您将被引导创建管理员账户。就这么简单。

初始构建后，后续启动只需：

```bash
docker compose up -d          # 启动（如果镜像未变更则跳过重建）
docker compose down           # 停止
docker compose logs -f        # 查看日志
```

数据持久化在 Docker 命名卷（`fim-data`、`fim-uploads`）中，容器重启后数据保留。

> **注意：** Docker 模式不支持热重载。代码更改需要重建镜像（`docker compose up --build -d`）。如需进行带实时重载的活跃开发，请使用下方的**选项 B**。### 选项 B：本地开发

前置条件：Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js 18+、pnpm。

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one

cp example.env .env
```# 编辑 .env: 设置 LLM_API_KEY# 安装
uv sync --all-extras
cd frontend && pnpm install && cd ..# 启动（带热重载）
./start.sh dev
```

| 命令             | 启动内容                                                | URL                                      |
| ---------------- | ------------------------------------------------------- | ---------------------------------------- |
| `./start.sh`     | Next.js + FastAPI                                       | http://localhost:3000 (UI) + :8000 (API) |
| `./start.sh dev` | 相同，带热重载（Python `--reload` + Next.js HMR）      | 相同                                     |
| `./start.sh api` | 仅 FastAPI（无头模式，用于集成或测试）                 | http://localhost:8000/api                |### 生产部署

两种方法都适用于生产环境：

| 方法       | 命令                   | 最适合                              |
| ---------- | ---------------------- | ----------------------------------- |
| **Docker** | `docker compose up -d` | 无需干预的部署、轻松更新            |
| **脚本**   | `./start.sh`           | 裸金属服务器、自定义进程管理器      |

对于任一方法，在前面放置 Nginx 反向代理以支持 HTTPS 和自定义域名：

```
User → Nginx (443/HTTPS) → localhost:3000
```

API 在内部运行于端口 8000 — Next.js 自动代理 `/api/*` 请求。只需暴露端口 3000。

**更新正在运行的部署**（零停机时间）：

```bash
cd /path/to/fim-one \
  && git pull origin master \
  && sudo docker compose build \
  && sudo docker compose up -d \
  && sudo docker image prune -f
```

`build` 首先运行，而旧容器继续提供流量。`up -d` 然后仅替换镜像已更改的容器 — 停机时间约为 10 秒而非数分钟。

如果使用代码执行沙箱（`CODE_EXEC_BACKEND=docker`），挂载 Docker socket：

```yaml
```
# docker-compose.yml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```## 配置### 推荐设置

FIM One 与**任何 OpenAI 兼容的 LLM 提供商**配合使用 — OpenAI、DeepSeek、Anthropic、Qwen、Ollama、vLLM 等。选择您偏好的任何一个：

| 提供商             | `LLM_API_KEY` | `LLM_BASE_URL`                 | `LLM_MODEL`         |
| ------------------ | ------------- | ------------------------------ | ------------------- |
| **OpenAI**         | `sk-...`      | *(默认)*                       | `gpt-4o`            |
| **DeepSeek**       | `sk-...`      | `https://api.deepseek.com/v1`  | `deepseek-chat`     |
| **Anthropic**      | `sk-ant-...`  | `https://api.anthropic.com/v1` | `claude-sonnet-4-6` |
| **Ollama** (本地)  | `ollama`      | `http://localhost:11434/v1`    | `qwen2.5:14b`       |

**[Jina AI](https://jina.ai/)** 解锁网页搜索/获取、嵌入和完整的 RAG 管道（提供免费层）。

最小化 `.env`：

```bash
LLM_API_KEY=sk-your-key
```# LLM_BASE_URL=https://api.openai.com/v1   # 默认值 — 更改为其他提供商# LLM_MODEL=gpt-4o                         # 默认值 — 更改为其他模型

JINA_API_KEY=jina_...                       # 解锁网络工具 + RAG
```### 所有变量

查看完整的[环境变量](https://docs.fim.ai/configuration/environment-variables)参考文档，了解所有配置选项（LLM、agent 执行、web 工具、RAG、代码执行、图像生成、connector、平台、OAuth）。## 开发

```bash


```# 安装所有依赖项（包括开发额外功能）
uv sync --all-extras# 运行测试
pytest# 运行测试并生成覆盖率报告
pytest --cov=fim_one --cov-report=term-missing# Lint
ruff check src/ tests/# 类型检查
mypy src/# 安装 git hooks（克隆后运行一次 — 在提交时启用自动 i18n 翻译）
bash scripts/setup-hooks.sh
```## 国际化 (i18n)

FIM One 支持 **6 种语言**：英语、中文、日语、韩语、德语和法语。翻译完全自动化 — 您只需编辑英文源文件。

**支持的语言**: `en` `zh` `ja` `ko` `de` `fr`

| 内容 | 源文件（编辑此处） | 自动生成（不要编辑） |
|------|--------------------|-----------------------------|
| UI 字符串 | `frontend/messages/en/*.json` | `frontend/messages/{locale}/*.json` |
| 文档 | `docs/*.mdx` | `docs/{locale}/*.mdx` |
| README | `README.md` | `README.{locale}.md` |

**工作原理**：提交前钩子检测对英文文件的更改，并通过项目的 Fast LLM 进行翻译。翻译是增量式的 — 仅处理新增、修改或删除的内容。

```bash# 设置（克隆后运行一次）
bash scripts/setup-hooks.sh# 完整翻译（首次或添加新语言后）
uv run scripts/translate.py --all# 翻译特定文件
uv run scripts/translate.py --files frontend/messages/en/common.json# 覆盖目标区域设置
uv run scripts/translate.py --all --locale ja ko# 增加并行 API 调用（默认值：3，如果您的 API 允许，请提高）
uv run scripts/translate.py --all --concurrency 10# 日常工作流：只需提交 — hook 自动处理一切
git add frontend/messages/en/common.json
git commit -m "feat(i18n): add new strings"  # hook 自动翻译
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--all` | — | 重新翻译所有内容（忽略缓存） |
| `--files` | — | 仅翻译特定文件 |
| `--locale` | 自动发现 | 覆盖目标语言 |
| `--concurrency` | 3 | 最大并行 LLM API 调用数 |
| `--force` | — | 强制重新翻译所有 JSON 键 |

**添加新语言**：`mkdir frontend/messages/{locale}` → 运行 `--all` → 将语言添加到 `frontend/src/i18n/request.ts` 的 `SUPPORTED_LOCALES`。## 路线图

查看完整的[路线图](https://docs.fim.ai/roadmap)了解版本历史和后续计划。## 贡献

我们欢迎各种形式的贡献 — 代码、文档、翻译、bug 报告和想法。

> **先锋计划**：前 100 位获得 PR 合并的贡献者将被认可为**创始贡献者**，在项目中获得永久致谢、个人资料徽章和优先问题支持。[了解更多 &rarr;](CONTRIBUTING.md#-pioneer-program)

**快速链接：**

- [**贡献指南**](CONTRIBUTING.md) — 设置、约定、PR 流程
- [**好的首个问题**](https://github.com/fim-ai/fim-one/labels/good%20first%20issue) — 为新手精选
- [**开放问题**](https://github.com/fim-ai/fim-one/issues) — bugs 和功能请求## Star 历史

<a href="https://star-history.com/#fim-ai/fim-one&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
  </picture>
</a>## 活动

![Alt](https://repobeats.axiom.co/api/embed/ff8b449fd183a323d81f33fc96e7ce42ad745f65.svg "Repobeats analytics image")## 贡献者

感谢这些杰出的人们（[emoji 说明](https://allcontributors.org/docs/en/emoji-key)）：

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->
<!-- ALL-CONTRIBUTORS-LIST:END -->

[![Contributors](https://contrib.rocks/image?repo=fim-ai/fim-one)](https://github.com/fim-ai/fim-one/graphs/contributors)

本项目遵循 [all-contributors](https://allcontributors.org/) 规范。欢迎任何形式的贡献！## 许可证

FIM One 源代码可用许可证。这**不是** OSI 批准的开源许可证。

**允许**：内部使用、修改、保持许可证完整的分发、嵌入到您自己的（非竞争性）应用程序中。

**限制**：多租户 SaaS、竞争性 agent 平台、白标、移除品牌。

如有商业许可证咨询，请在 [GitHub](https://github.com/fim-ai/fim-one) 上提交 issue。

完整条款见 [LICENSE](LICENSE)。

---

<div align="center">

🌐 [网站](https://one.fim.ai/) · 📖 [文档](https://docs.fim.ai) · 📋 [更新日志](https://docs.fim.ai/changelog) · 🐛 [报告 Bug](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>