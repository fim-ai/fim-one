"""Seed the database with comprehensive workflow examples for manual testing.

Usage:
    uv run python scripts/seed_workflow_examples.py

Each workflow gets an api_key for unauthenticated trigger testing:
    curl -X POST http://localhost:8000/api/workflows/trigger -H "Authorization: Bearer {api_key}" \
         -H 'Content-Type: application/json' \
         -d '{"inputs": {...}}'
"""

import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

DB_PATH = "data/fim_one.db"
USER_ID = "1f867da0-54b9-4daa-a3bb-d4dc99a95816"  # tony (admin)

# Reference IDs for existing resources
AGENT_ID = "62166eb8-72ac-48c0-b114-e5584587b0cc"  # 翻译专家
GITHUB_CONNECTOR_ID = "f6b49f8e-cd00-4715-beb2-b5f185396baa"
GITHUB_LIST_REPOS_ACTION = "48bbd8ac-6cba-4bbe-a711-38853d4db3a4"
GITHUB_LIST_ISSUES_ACTION = "e8da6552-c0c9-4c8c-92b4-44082c6c23f2"
KB_ID = "68c5e4ef-266f-4d28-b18a-c950d9d4d656"  # Default KB


def _id() -> str:
    return str(uuid.uuid4())


def _api_key() -> str:
    return f"wf_{secrets.token_urlsafe(32)}"


# Backend type → Frontend ReactFlow node type
_TYPE_MAP: dict[str, str] = {
    "START": "start",
    "END": "end",
    "LLM": "llm",
    "CONDITION_BRANCH": "conditionBranch",
    "QUESTION_CLASSIFIER": "questionClassifier",
    "AGENT": "agent",
    "KNOWLEDGE_RETRIEVAL": "knowledgeRetrieval",
    "CONNECTOR": "connector",
    "HTTP_REQUEST": "httpRequest",
    "VARIABLE_ASSIGN": "variableAssign",
    "TEMPLATE_TRANSFORM": "templateTransform",
    "CODE_EXECUTION": "codeExecution",
    "ITERATOR": "iterator",
    "LOOP": "loop",
    "VARIABLE_AGGREGATOR": "variableAggregator",
    "PARAMETER_EXTRACTOR": "parameterExtractor",
    "LIST_OPERATION": "listOperation",
    "TRANSFORM": "transform",
    "DOCUMENT_EXTRACTOR": "documentExtractor",
    "QUESTION_UNDERSTANDING": "questionUnderstanding",
    "HUMAN_INTERVENTION": "humanIntervention",
    "MCP": "mcp",
    "BUILTIN_TOOL": "builtinTool",
    "SUB_WORKFLOW": "subWorkflow",
    "ENV": "env",
}


def _node(node_id: str, node_type: str, data: dict, *, x: float = 0, y: float = 0) -> dict:
    """Build a node dict with the correct ReactFlow type."""
    return {
        "id": node_id,
        "type": _TYPE_MAP.get(node_type, node_type.lower()),
        "position": {"x": x, "y": y},
        "data": {"type": node_type, **data},
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    source_handle: str = "source",
    target_handle: str = "target",
) -> dict:
    """Build an edge dict with explicit handle IDs.

    ReactFlow nodes use Handle id="source" and id="target".
    For branching nodes (ConditionBranch, QuestionClassifier),
    pass the specific source_handle like "condition-active" or "class-billing".
    """
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "sourceHandle": source_handle,
        "targetHandle": target_handle,
        "type": "default",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# Workflow 1: 智能客服分流
#   Tests: QuestionClassifier, ConditionBranch(expression), LLM×2,
#          TemplateTransform, multiple End nodes
# ═══════════════════════════════════════════════════════════════════════
def wf1_customer_router():
    return {
        "id": _id(),
        "user_id": USER_ID,
        "name": "智能客服分流",
        "icon": "💬",
        "description": "根据用户问题自动分类（技术/账单/通用），路由到不同处理逻辑。测试 QuestionClassifier + ConditionBranch + 多 End 节点。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                _node("start_1", "START", {
                    "label": "接收问题",
                    "variables": [
                        {
                            "name": "query",
                            "type": "string",
                            "required": True,
                            "description": "用户问题",
                            "default_value": "我的服务器CPU占用率一直很高，怎么排查？"
                        }
                    ],
                }, x=100, y=300),
                _node("classifier_1", "QUESTION_CLASSIFIER", {
                    "label": "问题分类",
                    "input_variable": "{{input.query}}",
                    "classes": [
                        {
                            "label": "technical",
                            "handle": "technical",
                            "description": "Technical support questions about servers, software, APIs, debugging, performance"
                        },
                        {
                            "label": "billing",
                            "handle": "billing",
                            "description": "Billing, payment, subscription, pricing, invoice questions"
                        },
                        {
                            "label": "general",
                            "handle": "general",
                            "description": "General inquiries, feedback, account settings, other"
                        }
                    ],
                }, x=400, y=300),
                _node("llm_tech", "LLM", {
                    "label": "技术支持",
                    "system_prompt": "You are a senior technical support engineer. Provide detailed, actionable troubleshooting steps. Be specific about commands and tools to use. Reply in the same language as the user's question.",
                    "prompt_template": "User's technical question:\n{{input.query}}\n\nProvide step-by-step troubleshooting guidance.",
                    "model_tier": "fast",
                }, x=800, y=100),
                _node("llm_billing", "LLM", {
                    "label": "账单处理",
                    "system_prompt": "You are a billing support specialist. Be empathetic, clear, and provide specific next steps. Reply in the same language as the user's question.",
                    "prompt_template": "User's billing question:\n{{input.query}}\n\nProvide a helpful response with actionable next steps.",
                    "model_tier": "fast",
                }, x=800, y=300),
                _node("template_general", "TEMPLATE_TRANSFORM", {
                    "label": "通用回复",
                    "template": "感谢您的咨询！\n\n您的问题：{{input.query}}\n\n我们已收到您的反馈，客服团队将在24小时内为您处理。如需紧急帮助，请拨打 400-123-4567。",
                }, x=800, y=500),
                _node("end_tech", "END", {
                    "label": "技术回复",
                    "output_mapping": {
                        "category": "{{classifier_1.output}}",
                        "response": "{{llm_tech.output}}"
                    },
                }, x=1200, y=100),
                _node("end_billing", "END", {
                    "label": "账单回复",
                    "output_mapping": {
                        "category": "{{classifier_1.output}}",
                        "response": "{{llm_billing.output}}"
                    },
                }, x=1200, y=300),
                _node("end_general", "END", {
                    "label": "通用回复",
                    "output_mapping": {
                        "category": "{{classifier_1.output}}",
                        "response": "{{template_general.output}}"
                    },
                }, x=1200, y=500),
            ],
            "edges": [
                _edge("e1", "start_1", "classifier_1"),
                _edge("e2", "classifier_1", "llm_tech", "class-technical"),
                _edge("e3", "classifier_1", "llm_billing", "class-billing"),
                _edge("e4", "classifier_1", "template_general", "class-general"),
                _edge("e5", "llm_tech", "end_tech"),
                _edge("e6", "llm_billing", "end_billing"),
                _edge("e7", "template_general", "end_general"),
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 0.8}
        }),
        "input_schema": json.dumps({
            "variables": [{"name": "query", "type": "string", "required": True}]
        }),
        "created_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════
# Workflow 2: 批量翻译器 (Iterator)
#   Tests: Iterator, LLM per item, collected results
# ═══════════════════════════════════════════════════════════════════════
def wf2_batch_translator():
    return {
        "id": _id(),
        "user_id": USER_ID,
        "name": "批量翻译器",
        "icon": "🌐",
        "description": "将输入的文本数组逐项翻译为中文。测试 Iterator 节点的引擎级循环执行。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                _node("start_1", "START", {
                    "label": "输入文本列表",
                    "variables": [
                        {
                            "name": "texts",
                            "type": "array",
                            "required": True,
                            "description": "要翻译的文本数组",
                            "default_value": ["Hello, how are you?", "The weather is beautiful today.", "I love programming."]
                        },
                        {
                            "name": "target_lang",
                            "type": "string",
                            "required": False,
                            "description": "目标语言",
                            "default_value": "Chinese"
                        }
                    ],
                }, x=100, y=200),
                _node("iterator_1", "ITERATOR", {
                    "label": "遍历文本",
                    "list_variable": "{{input.texts}}",
                    "iterator_variable": "current_text",
                    "index_variable": "current_index",
                    "max_iterations": 20,
                }, x=400, y=200),
                _node("llm_translate", "LLM", {
                    "label": "翻译单条",
                    "system_prompt": "You are a professional translator. Translate the given text accurately. Output ONLY the translation, no explanations.",
                    "prompt_template": "Translate the following text to {{input.target_lang}}:\n\n{{iterator_1.current_text}}",
                    "model_tier": "fast",
                }, x=700, y=200),
                _node("end_1", "END", {
                    "label": "翻译结果",
                    "output_mapping": {
                        "translations": "{{iterator_1.results}}"
                    },
                }, x=1000, y=200),
            ],
            "edges": [
                _edge("e1", "start_1", "iterator_1"),
                _edge("e2", "iterator_1", "llm_translate"),
                _edge("e3", "llm_translate", "end_1"),
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 0.9}
        }),
        "input_schema": json.dumps({
            "variables": [
                {"name": "texts", "type": "array", "required": True},
                {"name": "target_lang", "type": "string", "required": False}
            ]
        }),
        "created_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════
# Workflow 3: 迭代优化器 (Loop)
#   Tests: Loop condition, LLM, re-execution, loop_index
# ═══════════════════════════════════════════════════════════════════════
def wf3_iterative_improver():
    return {
        "id": _id(),
        "user_id": USER_ID,
        "name": "迭代文案优化",
        "icon": "✨",
        "description": "对输入文案进行 3 轮 LLM 迭代优化，每轮基于上一轮结果改进。测试 Loop 节点引擎级循环。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                _node("start_1", "START", {
                    "label": "输入草稿",
                    "variables": [
                        {
                            "name": "draft",
                            "type": "string",
                            "required": True,
                            "description": "待优化的文案",
                            "default_value": "We make good software. Our product is fast. Buy it now."
                        }
                    ],
                }, x=100, y=200),
                _node("loop_1", "LOOP", {
                    "label": "循环优化",
                    "condition": "loop_index < 3",
                    "max_iterations": 5,
                    "loop_variable": "loop_index",
                }, x=400, y=200),
                _node("llm_improve", "LLM", {
                    "label": "LLM 优化",
                    "system_prompt": "You are a world-class copywriter. Improve the given text to be more compelling, professional, and engaging. Each iteration should meaningfully enhance the previous version. Output ONLY the improved text.",
                    "prompt_template": "Iteration {{loop_1.loop_index}} of 3.\n\nCurrent text to improve:\n{{input.draft}}\n\nPrevious improvement (if any): {{llm_improve.output}}\n\nMake this text more compelling and professional. Focus on: clarity, emotional appeal, and call-to-action strength.",
                    "model_tier": "fast",
                }, x=700, y=200),
                _node("end_1", "END", {
                    "label": "最终文案",
                    "output_mapping": {
                        "final_text": "{{llm_improve.output}}",
                        "iterations": "{{loop_1.loop_index}}"
                    },
                }, x=1000, y=200),
            ],
            "edges": [
                _edge("e1", "start_1", "loop_1"),
                _edge("e2", "loop_1", "llm_improve"),
                _edge("e3", "llm_improve", "end_1"),
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 0.9}
        }),
        "input_schema": json.dumps({
            "variables": [{"name": "draft", "type": "string", "required": True}]
        }),
        "created_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════
# Workflow 4: 数据采集 & 分析 (HTTP + Code + Transform)
#   Tests: HttpRequest, CodeExecution, VariableAssign,
#          TemplateTransform, ConditionBranch(llm)
# ═══════════════════════════════════════════════════════════════════════
def wf4_data_pipeline():
    return {
        "id": _id(),
        "user_id": USER_ID,
        "name": "API 数据采集分析",
        "icon": "🔍",
        "description": "从公开 API 采集数据 → Python 解析 → 条件判断 → 格式化报告。测试 HTTP + Code + ConditionBranch(llm) + Transform 全链路。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                _node("start_1", "START", {
                    "label": "配置参数",
                    "variables": [
                        {
                            "name": "user_id",
                            "type": "number",
                            "required": False,
                            "description": "JSONPlaceholder user ID (1-10)",
                            "default_value": 1
                        }
                    ],
                }, x=100, y=300),
                _node("http_user", "HTTP_REQUEST", {
                    "label": "获取用户信息",
                    "url": "https://jsonplaceholder.typicode.com/users/{{input.user_id}}",
                    "method": "GET",
                    "headers": {"Accept": "application/json"},
                    "timeout": 15,
                }, x=400, y=200),
                _node("http_posts", "HTTP_REQUEST", {
                    "label": "获取用户帖子",
                    "url": "https://jsonplaceholder.typicode.com/posts?userId={{input.user_id}}",
                    "method": "GET",
                    "headers": {"Accept": "application/json"},
                    "timeout": 15,
                }, x=400, y=450),
                _node("code_analyze", "CODE_EXECUTION", {
                    "label": "数据分析",
                    "language": "python",
                    "code": (
                        "import json\n"
                        "# Variables use dotted keys: 'node_id.output'\n"
                        "user_raw = variables.get('http_user.output', '{}')\n"
                        "posts_raw = variables.get('http_posts.output', '[]')\n"
                        "try:\n"
                        "    user = json.loads(user_raw) if isinstance(user_raw, str) else (user_raw or {})\n"
                        "    posts = json.loads(posts_raw) if isinstance(posts_raw, str) else (posts_raw or [])\n"
                        "except:\n"
                        "    user = {}\n"
                        "    posts = []\n"
                        "post_count = len(posts) if isinstance(posts, list) else 0\n"
                        "avg_title_len = 0\n"
                        "if post_count > 0:\n"
                        "    avg_title_len = sum(len(p.get('title', '')) for p in posts) / post_count\n"
                        "result = {\n"
                        "    'username': user.get('username', 'unknown') if isinstance(user, dict) else 'unknown',\n"
                        "    'email': user.get('email', '') if isinstance(user, dict) else '',\n"
                        "    'company': user.get('company', {}).get('name', 'N/A') if isinstance(user, dict) else 'N/A',\n"
                        "    'post_count': post_count,\n"
                        "    'avg_title_length': round(avg_title_len, 1),\n"
                        "    'is_active_author': post_count >= 5\n"
                        "}\n"
                        "print(json.dumps(result))\n"
                    ),
                }, x=750, y=300),
                _node("condition_1", "CONDITION_BRANCH", {
                    "label": "活跃度判定",
                    "mode": "llm",
                    "llm_prompt": "Based on the code analysis output, determine if the user is an active author (has 5 or more posts) or inactive.\n\nAnalysis result: {{code_analyze.output}}",
                    "conditions": [
                        {
                            "id": "active",
                            "label": "活跃作者",
                            "llm_prompt": "The analysis shows the user IS an active author (is_active_author is true, post count >= 5)"
                        },
                        {
                            "id": "inactive",
                            "label": "低活跃",
                            "llm_prompt": "The analysis shows the user is NOT an active author (is_active_author is false, post count < 5)"
                        }
                    ],
                    "default_handle": "condition-inactive",
                }, x=1050, y=300),
                _node("template_active", "TEMPLATE_TRANSFORM", {
                    "label": "活跃报告",
                    "template": "📊 **活跃用户报告**\n\n数据摘要:\n{{code_analyze.output}}\n\n✅ 该用户是活跃内容创作者，建议纳入 VIP 计划。",
                }, x=1400, y=150),
                _node("template_inactive", "TEMPLATE_TRANSFORM", {
                    "label": "低活跃报告",
                    "template": "📊 **用户活跃度报告**\n\n数据摘要:\n{{code_analyze.output}}\n\n⚠️ 该用户活跃度较低，建议发送激励邮件。",
                }, x=1400, y=450),
                _node("end_active", "END", {
                    "label": "活跃结果",
                    "output_mapping": {
                        "report": "{{template_active.output}}",
                        "status": "{{condition_1.output}}"
                    },
                }, x=1750, y=150),
                _node("end_inactive", "END", {
                    "label": "低活跃结果",
                    "output_mapping": {
                        "report": "{{template_inactive.output}}",
                        "status": "{{condition_1.output}}"
                    },
                }, x=1750, y=450),
            ],
            "edges": [
                _edge("e1", "start_1", "http_user"),
                _edge("e2", "start_1", "http_posts"),
                _edge("e3", "http_user", "code_analyze"),
                _edge("e4", "http_posts", "code_analyze"),
                _edge("e5", "code_analyze", "condition_1"),
                _edge("e6", "condition_1", "template_active", "condition-active"),
                _edge("e7", "condition_1", "template_inactive", "condition-inactive"),
                _edge("e8", "template_active", "end_active"),
                _edge("e9", "template_inactive", "end_inactive"),
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 0.7}
        }),
        "input_schema": json.dumps({
            "variables": [{"name": "user_id", "type": "number", "required": False}]
        }),
        "created_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════
# Workflow 5: 多步 LLM 推理链 + Agent (Agent + LLM chain)
#   Tests: LLM, VariableAssign, Agent(翻译专家), BuiltinTool(calculator)
# ═══════════════════════════════════════════════════════════════════════
def wf5_agent_chain():
    return {
        "id": _id(),
        "user_id": USER_ID,
        "name": "智能体协作链",
        "icon": "🧠",
        "description": "LLM 生成内容 → BuiltinTool(计算器)验证 → Agent(翻译专家)翻译。测试 LLM → BuiltinTool → Agent 全链路协作。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                _node("start_1", "START", {
                    "label": "输入主题",
                    "variables": [
                        {
                            "name": "topic",
                            "type": "string",
                            "required": True,
                            "description": "要分析的主题",
                            "default_value": "The impact of AI on software development productivity"
                        }
                    ],
                }, x=100, y=200),
                _node("llm_analyze", "LLM", {
                    "label": "主题分析",
                    "system_prompt": "You are a research analyst. Write a concise analysis (3-4 sentences) on the given topic. Include a specific numerical claim that can be verified.",
                    "prompt_template": "Analyze the following topic and provide a brief research summary:\n\n{{input.topic}}",
                    "model_tier": "fast",
                }, x=400, y=200),
                _node("builtin_calc", "BUILTIN_TOOL", {
                    "label": "数学验证",
                    "tool_id": "calculator",
                    "parameters": {
                        "expression": "42 * 1.5 + 8"
                    },
                    "output_variable": "calc_result",
                }, x=700, y=200),
                _node("agent_translate", "AGENT", {
                    "label": "翻译专家",
                    "agent_id": AGENT_ID,
                    "prompt_template": "Please translate the following English analysis into fluent Chinese:\n\n{{llm_analyze.output}}\n\n(Note: calculation verification result = {{builtin_calc.output}})",
                    "output_variable": "translation",
                }, x=1000, y=200),
                _node("end_1", "END", {
                    "label": "最终输出",
                    "output_mapping": {
                        "original_analysis": "{{llm_analyze.output}}",
                        "calculation": "{{builtin_calc.output}}",
                        "chinese_translation": "{{agent_translate.output}}"
                    },
                }, x=1300, y=200),
            ],
            "edges": [
                _edge("e1", "start_1", "llm_analyze"),
                _edge("e2", "llm_analyze", "builtin_calc"),
                _edge("e3", "builtin_calc", "agent_translate"),
                _edge("e4", "agent_translate", "end_1"),
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 0.9}
        }),
        "input_schema": json.dumps({
            "variables": [{"name": "topic", "type": "string", "required": True}]
        }),
        "created_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════
# Workflow 6: 全链路压力测试 (最多节点类型覆盖)
#   Tests: LLM, HttpRequest, CodeExecution, VariableAssign,
#          QuestionClassifier, TemplateTransform, ConditionBranch,
#          ListOperation, VariableAggregator
# ═══════════════════════════════════════════════════════════════════════
def wf6_full_pipeline():
    return {
        "id": _id(),
        "user_id": USER_ID,
        "name": "全链路综合测试",
        "icon": "⚡",
        "description": "覆盖最多节点类型的综合流水线：HTTP采集 → 代码解析 → LLM分析 → 分类路由 → 聚合输出。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                _node("start_1", "START", {
                    "label": "Start",
                    "variables": [
                        {
                            "name": "post_id",
                            "type": "number",
                            "required": False,
                            "description": "JSONPlaceholder post ID",
                            "default_value": 1
                        }
                    ],
                }, x=50, y=300),
                # Step 1: Fetch a post
                _node("http_post", "HTTP_REQUEST", {
                    "label": "获取帖子",
                    "url": "https://jsonplaceholder.typicode.com/posts/{{input.post_id}}",
                    "method": "GET",
                    "timeout": 10,
                }, x=300, y=300),
                # Step 2: Parse with code
                _node("code_parse", "CODE_EXECUTION", {
                    "label": "解析数据",
                    "language": "python",
                    "code": (
                        "import json\n"
                        "# Variables use dotted keys: 'node_id.output'\n"
                        "raw = variables.get('http_post.output', '{}')\n"
                        "try:\n"
                        "    data = json.loads(raw) if isinstance(raw, str) else (raw or {})\n"
                        "except:\n"
                        "    data = {}\n"
                        "result = {\n"
                        "    'title': data.get('title', '') if isinstance(data, dict) else '',\n"
                        "    'body': data.get('body', '') if isinstance(data, dict) else '',\n"
                        "    'word_count': len(data.get('body', '').split()) if isinstance(data, dict) else 0\n"
                        "}\n"
                        "print(json.dumps(result))\n"
                    ),
                }, x=550, y=300),
                # Step 3: Assign variables
                _node("var_assign_1", "VARIABLE_ASSIGN", {
                    "label": "提取字段",
                    "assignments": [
                        {"variable": "post_title", "expression": "{{code_parse.output}}"},
                        {"variable": "analysis_ready", "expression": "True"}
                    ],
                }, x=800, y=300),
                # Step 4: LLM analysis
                _node("llm_analyze", "LLM", {
                    "label": "内容分析",
                    "system_prompt": "Analyze the given post. Classify its tone as: positive, negative, or neutral. Reply with a JSON object: {\"tone\": \"...\", \"summary\": \"...\", \"keywords\": [...]}",
                    "prompt_template": "Analyze this post:\n\nTitle: {{var_assign_1.post_title}}\n\nFull content from API: {{code_parse.output}}",
                    "model_tier": "fast",
                }, x=1050, y=300),
                # Step 5: Classify
                _node("classifier_tone", "QUESTION_CLASSIFIER", {
                    "label": "语调分类",
                    "input_variable": "{{llm_analyze.output}}",
                    "classes": [
                        {"label": "positive", "handle": "positive", "description": "Positive, optimistic, enthusiastic tone"},
                        {"label": "negative", "handle": "negative", "description": "Negative, critical, pessimistic tone"},
                        {"label": "neutral", "handle": "neutral", "description": "Neutral, factual, informational tone"}
                    ],
                }, x=1300, y=300),
                # Step 6a: Positive branch
                _node("template_positive", "TEMPLATE_TRANSFORM", {
                    "label": "正面报告",
                    "template": "**正面内容分析报告**\n\n来源: Post #{{input.post_id}}\n分析: {{llm_analyze.output}}\n\n建议: 推荐置顶或加入精华帖。",
                }, x=1600, y=100),
                # Step 6b: Negative branch
                _node("template_negative", "TEMPLATE_TRANSFORM", {
                    "label": "负面报告",
                    "template": "**负面内容分析报告**\n\n来源: Post #{{input.post_id}}\n分析: {{llm_analyze.output}}\n\n建议: 需人工审核，考虑隐藏或回复。",
                }, x=1600, y=300),
                # Step 6c: Neutral branch
                _node("template_neutral", "TEMPLATE_TRANSFORM", {
                    "label": "中性报告",
                    "template": "**中性内容分析报告**\n\n来源: Post #{{input.post_id}}\n分析: {{llm_analyze.output}}\n\n建议: 正常展示，无需干预。",
                }, x=1600, y=500),
                # Step 7: End nodes
                _node("end_pos", "END", {
                    "label": "正面结果",
                    "output_mapping": {"report": "{{template_positive.output}}", "tone": "{{classifier_tone.output}}"},
                }, x=1900, y=100),
                _node("end_neg", "END", {
                    "label": "负面结果",
                    "output_mapping": {"report": "{{template_negative.output}}", "tone": "{{classifier_tone.output}}"},
                }, x=1900, y=300),
                _node("end_neu", "END", {
                    "label": "中性结果",
                    "output_mapping": {"report": "{{template_neutral.output}}", "tone": "{{classifier_tone.output}}"},
                }, x=1900, y=500),
            ],
            "edges": [
                _edge("e1", "start_1", "http_post"),
                _edge("e2", "http_post", "code_parse"),
                _edge("e3", "code_parse", "var_assign_1"),
                _edge("e4", "var_assign_1", "llm_analyze"),
                _edge("e5", "llm_analyze", "classifier_tone"),
                _edge("e6", "classifier_tone", "template_positive", "class-positive"),
                _edge("e7", "classifier_tone", "template_negative", "class-negative"),
                _edge("e8", "classifier_tone", "template_neutral", "class-neutral"),
                _edge("e9", "template_positive", "end_pos"),
                _edge("e10", "template_negative", "end_neg"),
                _edge("e11", "template_neutral", "end_neu"),
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 0.6}
        }),
        "input_schema": json.dumps({
            "variables": [{"name": "post_id", "type": "number", "required": False}]
        }),
        "created_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════
# INSERT
# ═══════════════════════════════════════════════════════════════════════
def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Clean all workflows for this seed user (they're all test data)
    cur.execute("DELETE FROM workflows WHERE user_id = ?", (USER_ID,))

    workflows = [
        wf1_customer_router(),
        wf2_batch_translator(),
        wf3_iterative_improver(),
        wf4_data_pipeline(),
        wf5_agent_chain(),
        wf6_full_pipeline(),
    ]

    # Store what the API would store: v2 blueprints, hashed trigger keys.
    from fim_one.core.workflow.blueprint_v2 import upgrade_blueprint
    from fim_one.web.services.workflow_trigger_keys import (
        DISPLAY_PREFIX_LEN,
        hash_trigger_key,
    )

    raw_keys: dict[str, str] = {}
    for wf in workflows:
        wf["blueprint"] = json.dumps(upgrade_blueprint(json.loads(wf["blueprint"])))
        raw_key = wf.pop("api_key")
        raw_keys[wf["id"]] = raw_key
        wf["api_key_hash"] = hash_trigger_key(raw_key)
        wf["api_key_prefix"] = raw_key[:DISPLAY_PREFIX_LEN]

    # Build column list from first workflow
    columns = list(workflows[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)

    for wf in workflows:
        values = [wf[c] for c in columns]
        cur.execute(f"INSERT INTO workflows ({col_names}) VALUES ({placeholders})", values)
        print(f"✅ Inserted: {wf['name']}")
        print(f"   API Key: {raw_keys[wf['id']]}")
        print(
            "   Trigger: curl -X POST http://localhost:8000/api/workflows/trigger"
            f" -H 'Authorization: Bearer {raw_keys[wf['id']]}'"
            " -H 'Content-Type: application/json' -d '{\"inputs\": {}}'"
        )
        print()

    conn.commit()
    conn.close()

    print(f"\n🎉 Done! {len(workflows)} workflows inserted.")
    print("Go to the Workflows page in the UI to see them.")


if __name__ == "__main__":
    main()
