"""Seed the database with comprehensive workflow examples for manual testing.

Usage:
    uv run python scripts/seed_workflow_examples.py

Each workflow gets an api_key for unauthenticated trigger testing:
    curl -X POST http://localhost:8000/api/workflows/trigger/{api_key} \
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
        "name": "🎯 智能客服分流",
        "icon": "🎯",
        "description": "根据用户问题自动分类（技术/账单/通用），路由到不同处理逻辑。测试 QuestionClassifier + ConditionBranch + 多 End 节点。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                {
                    "id": "start_1",
                    "type": "custom",
                    "position": {"x": 100, "y": 300},
                    "data": {
                        "type": "START",
                        "label": "接收问题",
                        "input_schema": {
                            "variables": [
                                {
                                    "name": "query",
                                    "type": "string",
                                    "required": True,
                                    "description": "用户问题",
                                    "default": "我的服务器CPU占用率一直很高，怎么排查？"
                                }
                            ]
                        }
                    }
                },
                {
                    "id": "classifier_1",
                    "type": "custom",
                    "position": {"x": 400, "y": 300},
                    "data": {
                        "type": "QUESTION_CLASSIFIER",
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
                        ]
                    }
                },
                {
                    "id": "llm_tech",
                    "type": "custom",
                    "position": {"x": 800, "y": 100},
                    "data": {
                        "type": "LLM",
                        "label": "技术支持",
                        "system_prompt": "You are a senior technical support engineer. Provide detailed, actionable troubleshooting steps. Be specific about commands and tools to use. Reply in the same language as the user's question.",
                        "prompt_template": "User's technical question:\n{{input.query}}\n\nProvide step-by-step troubleshooting guidance.",
                        "model_tier": "fast"
                    }
                },
                {
                    "id": "llm_billing",
                    "type": "custom",
                    "position": {"x": 800, "y": 300},
                    "data": {
                        "type": "LLM",
                        "label": "账单处理",
                        "system_prompt": "You are a billing support specialist. Be empathetic, clear, and provide specific next steps. Reply in the same language as the user's question.",
                        "prompt_template": "User's billing question:\n{{input.query}}\n\nProvide a helpful response with actionable next steps.",
                        "model_tier": "fast"
                    }
                },
                {
                    "id": "template_general",
                    "type": "custom",
                    "position": {"x": 800, "y": 500},
                    "data": {
                        "type": "TEMPLATE_TRANSFORM",
                        "label": "通用回复",
                        "template": "感谢您的咨询！\n\n您的问题：{{input.query}}\n\n我们已收到您的反馈，客服团队将在24小时内为您处理。如需紧急帮助，请拨打 400-123-4567。"
                    }
                },
                {
                    "id": "end_tech",
                    "type": "custom",
                    "position": {"x": 1200, "y": 100},
                    "data": {
                        "type": "END",
                        "label": "技术回复",
                        "output_mapping": {
                            "category": "{{classifier_1.output}}",
                            "response": "{{llm_tech.output}}"
                        }
                    }
                },
                {
                    "id": "end_billing",
                    "type": "custom",
                    "position": {"x": 1200, "y": 300},
                    "data": {
                        "type": "END",
                        "label": "账单回复",
                        "output_mapping": {
                            "category": "{{classifier_1.output}}",
                            "response": "{{llm_billing.output}}"
                        }
                    }
                },
                {
                    "id": "end_general",
                    "type": "custom",
                    "position": {"x": 1200, "y": 500},
                    "data": {
                        "type": "END",
                        "label": "通用回复",
                        "output_mapping": {
                            "category": "{{classifier_1.output}}",
                            "response": "{{template_general.output}}"
                        }
                    }
                }
            ],
            "edges": [
                {"id": "e1", "source": "start_1", "target": "classifier_1"},
                {"id": "e2", "source": "classifier_1", "target": "llm_tech", "sourceHandle": "class-technical"},
                {"id": "e3", "source": "classifier_1", "target": "llm_billing", "sourceHandle": "class-billing"},
                {"id": "e4", "source": "classifier_1", "target": "template_general", "sourceHandle": "class-general"},
                {"id": "e5", "source": "llm_tech", "target": "end_tech"},
                {"id": "e6", "source": "llm_billing", "target": "end_billing"},
                {"id": "e7", "source": "template_general", "target": "end_general"},
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
        "name": "🔄 批量翻译器",
        "icon": "🔄",
        "description": "将输入的文本数组逐项翻译为中文。测试 Iterator 节点的引擎级循环执行。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                {
                    "id": "start_1",
                    "type": "custom",
                    "position": {"x": 100, "y": 200},
                    "data": {
                        "type": "START",
                        "label": "输入文本列表",
                        "input_schema": {
                            "variables": [
                                {
                                    "name": "texts",
                                    "type": "array",
                                    "required": True,
                                    "description": "要翻译的文本数组",
                                    "default": ["Hello, how are you?", "The weather is beautiful today.", "I love programming."]
                                },
                                {
                                    "name": "target_lang",
                                    "type": "string",
                                    "required": False,
                                    "description": "目标语言",
                                    "default": "Chinese"
                                }
                            ]
                        }
                    }
                },
                {
                    "id": "iterator_1",
                    "type": "custom",
                    "position": {"x": 400, "y": 200},
                    "data": {
                        "type": "ITERATOR",
                        "label": "遍历文本",
                        "list_variable": "{{input.texts}}",
                        "iterator_variable": "current_text",
                        "index_variable": "current_index",
                        "max_iterations": 20
                    }
                },
                {
                    "id": "llm_translate",
                    "type": "custom",
                    "position": {"x": 700, "y": 200},
                    "data": {
                        "type": "LLM",
                        "label": "翻译单条",
                        "system_prompt": "You are a professional translator. Translate the given text accurately. Output ONLY the translation, no explanations.",
                        "prompt_template": "Translate the following text to {{input.target_lang}}:\n\n{{iterator_1.current_text}}",
                        "model_tier": "fast"
                    }
                },
                {
                    "id": "end_1",
                    "type": "custom",
                    "position": {"x": 1000, "y": 200},
                    "data": {
                        "type": "END",
                        "label": "翻译结果",
                        "output_mapping": {
                            "translation": "{{llm_translate.output}}",
                            "index": "{{iterator_1.current_index}}"
                        }
                    }
                }
            ],
            "edges": [
                {"id": "e1", "source": "start_1", "target": "iterator_1"},
                {"id": "e2", "source": "iterator_1", "target": "llm_translate"},
                {"id": "e3", "source": "llm_translate", "target": "end_1"},
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
        "name": "🔁 迭代文案优化",
        "icon": "🔁",
        "description": "对输入文案进行 3 轮 LLM 迭代优化，每轮基于上一轮结果改进。测试 Loop 节点引擎级循环。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                {
                    "id": "start_1",
                    "type": "custom",
                    "position": {"x": 100, "y": 200},
                    "data": {
                        "type": "START",
                        "label": "输入草稿",
                        "input_schema": {
                            "variables": [
                                {
                                    "name": "draft",
                                    "type": "string",
                                    "required": True,
                                    "description": "待优化的文案",
                                    "default": "We make good software. Our product is fast. Buy it now."
                                }
                            ]
                        }
                    }
                },
                {
                    "id": "loop_1",
                    "type": "custom",
                    "position": {"x": 400, "y": 200},
                    "data": {
                        "type": "LOOP",
                        "label": "循环优化",
                        "condition": "loop_index < 3",
                        "max_iterations": 5,
                        "loop_variable": "loop_index"
                    }
                },
                {
                    "id": "llm_improve",
                    "type": "custom",
                    "position": {"x": 700, "y": 200},
                    "data": {
                        "type": "LLM",
                        "label": "LLM 优化",
                        "system_prompt": "You are a world-class copywriter. Improve the given text to be more compelling, professional, and engaging. Each iteration should meaningfully enhance the previous version. Output ONLY the improved text.",
                        "prompt_template": "Iteration {{loop_1.loop_index}} of 3.\n\nCurrent text to improve:\n{{input.draft}}\n\nPrevious improvement (if any): {{llm_improve.output}}\n\nMake this text more compelling and professional. Focus on: clarity, emotional appeal, and call-to-action strength.",
                        "model_tier": "fast"
                    }
                },
                {
                    "id": "end_1",
                    "type": "custom",
                    "position": {"x": 1000, "y": 200},
                    "data": {
                        "type": "END",
                        "label": "最终文案",
                        "output_mapping": {
                            "final_text": "{{llm_improve.output}}",
                            "iterations": "{{loop_1.loop_index}}"
                        }
                    }
                }
            ],
            "edges": [
                {"id": "e1", "source": "start_1", "target": "loop_1"},
                {"id": "e2", "source": "loop_1", "target": "llm_improve"},
                {"id": "e3", "source": "llm_improve", "target": "end_1"},
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
        "name": "📊 API 数据采集分析",
        "icon": "📊",
        "description": "从公开 API 采集数据 → Python 解析 → 条件判断 → 格式化报告。测试 HTTP + Code + ConditionBranch(llm) + Transform 全链路。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                {
                    "id": "start_1",
                    "type": "custom",
                    "position": {"x": 100, "y": 300},
                    "data": {
                        "type": "START",
                        "label": "配置参数",
                        "input_schema": {
                            "variables": [
                                {
                                    "name": "user_id",
                                    "type": "number",
                                    "required": False,
                                    "description": "JSONPlaceholder user ID (1-10)",
                                    "default": 1
                                }
                            ]
                        }
                    }
                },
                {
                    "id": "http_user",
                    "type": "custom",
                    "position": {"x": 400, "y": 200},
                    "data": {
                        "type": "HTTP_REQUEST",
                        "label": "获取用户信息",
                        "url": "https://jsonplaceholder.typicode.com/users/{{input.user_id}}",
                        "method": "GET",
                        "headers": {"Accept": "application/json"},
                        "timeout": 15
                    }
                },
                {
                    "id": "http_posts",
                    "type": "custom",
                    "position": {"x": 400, "y": 450},
                    "data": {
                        "type": "HTTP_REQUEST",
                        "label": "获取用户帖子",
                        "url": "https://jsonplaceholder.typicode.com/posts?userId={{input.user_id}}",
                        "method": "GET",
                        "headers": {"Accept": "application/json"},
                        "timeout": 15
                    }
                },
                {
                    "id": "code_analyze",
                    "type": "custom",
                    "position": {"x": 750, "y": 300},
                    "data": {
                        "type": "CODE_EXECUTION",
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
                        )
                    }
                },
                {
                    "id": "condition_1",
                    "type": "custom",
                    "position": {"x": 1050, "y": 300},
                    "data": {
                        "type": "CONDITION_BRANCH",
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
                        "default_handle": "condition-inactive"
                    }
                },
                {
                    "id": "template_active",
                    "type": "custom",
                    "position": {"x": 1400, "y": 150},
                    "data": {
                        "type": "TEMPLATE_TRANSFORM",
                        "label": "活跃报告",
                        "template": "📊 **活跃用户报告**\n\n数据摘要:\n{{code_analyze.output}}\n\n✅ 该用户是活跃内容创作者，建议纳入 VIP 计划。"
                    }
                },
                {
                    "id": "template_inactive",
                    "type": "custom",
                    "position": {"x": 1400, "y": 450},
                    "data": {
                        "type": "TEMPLATE_TRANSFORM",
                        "label": "低活跃报告",
                        "template": "📊 **用户活跃度报告**\n\n数据摘要:\n{{code_analyze.output}}\n\n⚠️ 该用户活跃度较低，建议发送激励邮件。"
                    }
                },
                {
                    "id": "end_active",
                    "type": "custom",
                    "position": {"x": 1750, "y": 150},
                    "data": {
                        "type": "END",
                        "label": "活跃结果",
                        "output_mapping": {
                            "report": "{{template_active.output}}",
                            "status": "{{condition_1.output}}"
                        }
                    }
                },
                {
                    "id": "end_inactive",
                    "type": "custom",
                    "position": {"x": 1750, "y": 450},
                    "data": {
                        "type": "END",
                        "label": "低活跃结果",
                        "output_mapping": {
                            "report": "{{template_inactive.output}}",
                            "status": "{{condition_1.output}}"
                        }
                    }
                }
            ],
            "edges": [
                {"id": "e1", "source": "start_1", "target": "http_user"},
                {"id": "e2", "source": "start_1", "target": "http_posts"},
                {"id": "e3", "source": "http_user", "target": "code_analyze"},
                {"id": "e4", "source": "http_posts", "target": "code_analyze"},
                {"id": "e5", "source": "code_analyze", "target": "condition_1"},
                {"id": "e6", "source": "condition_1", "target": "template_active", "sourceHandle": "condition-active"},
                {"id": "e7", "source": "condition_1", "target": "template_inactive", "sourceHandle": "condition-inactive"},
                {"id": "e8", "source": "template_active", "target": "end_active"},
                {"id": "e9", "source": "template_inactive", "target": "end_inactive"},
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
        "name": "🤖 智能体协作链",
        "icon": "🤖",
        "description": "LLM 生成内容 → BuiltinTool(计算器)验证 → Agent(翻译专家)翻译。测试 LLM → BuiltinTool → Agent 全链路协作。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                {
                    "id": "start_1",
                    "type": "custom",
                    "position": {"x": 100, "y": 200},
                    "data": {
                        "type": "START",
                        "label": "输入主题",
                        "input_schema": {
                            "variables": [
                                {
                                    "name": "topic",
                                    "type": "string",
                                    "required": True,
                                    "description": "要分析的主题",
                                    "default": "The impact of AI on software development productivity"
                                }
                            ]
                        }
                    }
                },
                {
                    "id": "llm_analyze",
                    "type": "custom",
                    "position": {"x": 400, "y": 200},
                    "data": {
                        "type": "LLM",
                        "label": "主题分析",
                        "system_prompt": "You are a research analyst. Write a concise analysis (3-4 sentences) on the given topic. Include a specific numerical claim that can be verified.",
                        "prompt_template": "Analyze the following topic and provide a brief research summary:\n\n{{input.topic}}",
                        "model_tier": "fast"
                    }
                },
                {
                    "id": "builtin_calc",
                    "type": "custom",
                    "position": {"x": 700, "y": 200},
                    "data": {
                        "type": "BUILTIN_TOOL",
                        "label": "数学验证",
                        "tool_id": "calculator",
                        "parameters": {
                            "expression": "42 * 1.5 + 8"
                        },
                        "output_variable": "calc_result"
                    }
                },
                {
                    "id": "agent_translate",
                    "type": "custom",
                    "position": {"x": 1000, "y": 200},
                    "data": {
                        "type": "AGENT",
                        "label": "翻译专家",
                        "agent_id": AGENT_ID,
                        "prompt_template": "Please translate the following English analysis into fluent Chinese:\n\n{{llm_analyze.output}}\n\n(Note: calculation verification result = {{builtin_calc.output}})",
                        "output_variable": "translation"
                    }
                },
                {
                    "id": "end_1",
                    "type": "custom",
                    "position": {"x": 1300, "y": 200},
                    "data": {
                        "type": "END",
                        "label": "最终输出",
                        "output_mapping": {
                            "original_analysis": "{{llm_analyze.output}}",
                            "calculation": "{{builtin_calc.output}}",
                            "chinese_translation": "{{agent_translate.output}}"
                        }
                    }
                }
            ],
            "edges": [
                {"id": "e1", "source": "start_1", "target": "llm_analyze"},
                {"id": "e2", "source": "llm_analyze", "target": "builtin_calc"},
                {"id": "e3", "source": "builtin_calc", "target": "agent_translate"},
                {"id": "e4", "source": "agent_translate", "target": "end_1"},
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
        "name": "🏗️ 全链路综合测试",
        "icon": "🏗️",
        "description": "覆盖最多节点类型的综合流水线：HTTP采集 → 代码解析 → LLM分析 → 分类路由 → 聚合输出。",
        "status": "active",
        "is_active": True,
        "api_key": _api_key(),
        "blueprint": json.dumps({
            "nodes": [
                {
                    "id": "start_1",
                    "type": "custom",
                    "position": {"x": 50, "y": 300},
                    "data": {
                        "type": "START",
                        "label": "Start",
                        "input_schema": {
                            "variables": [
                                {
                                    "name": "post_id",
                                    "type": "number",
                                    "required": False,
                                    "description": "JSONPlaceholder post ID",
                                    "default": 1
                                }
                            ]
                        }
                    }
                },
                # Step 1: Fetch a post
                {
                    "id": "http_post",
                    "type": "custom",
                    "position": {"x": 300, "y": 300},
                    "data": {
                        "type": "HTTP_REQUEST",
                        "label": "获取帖子",
                        "url": "https://jsonplaceholder.typicode.com/posts/{{input.post_id}}",
                        "method": "GET",
                        "timeout": 10
                    }
                },
                # Step 2: Parse with code
                {
                    "id": "code_parse",
                    "type": "custom",
                    "position": {"x": 550, "y": 300},
                    "data": {
                        "type": "CODE_EXECUTION",
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
                        )
                    }
                },
                # Step 3: Assign variables
                {
                    "id": "var_assign_1",
                    "type": "custom",
                    "position": {"x": 800, "y": 300},
                    "data": {
                        "type": "VARIABLE_ASSIGN",
                        "label": "提取字段",
                        "assignments": [
                            {"variable": "post_title", "expression": "{{code_parse.output}}"},
                            {"variable": "analysis_ready", "expression": "True"}
                        ]
                    }
                },
                # Step 4: LLM analysis
                {
                    "id": "llm_analyze",
                    "type": "custom",
                    "position": {"x": 1050, "y": 300},
                    "data": {
                        "type": "LLM",
                        "label": "内容分析",
                        "system_prompt": "Analyze the given post. Classify its tone as: positive, negative, or neutral. Reply with a JSON object: {\"tone\": \"...\", \"summary\": \"...\", \"keywords\": [...]}",
                        "prompt_template": "Analyze this post:\n\nTitle: {{var_assign_1.post_title}}\n\nFull content from API: {{code_parse.output}}",
                        "model_tier": "fast"
                    }
                },
                # Step 5: Classify
                {
                    "id": "classifier_tone",
                    "type": "custom",
                    "position": {"x": 1300, "y": 300},
                    "data": {
                        "type": "QUESTION_CLASSIFIER",
                        "label": "语调分类",
                        "input_variable": "{{llm_analyze.output}}",
                        "classes": [
                            {"label": "positive", "handle": "positive", "description": "Positive, optimistic, enthusiastic tone"},
                            {"label": "negative", "handle": "negative", "description": "Negative, critical, pessimistic tone"},
                            {"label": "neutral", "handle": "neutral", "description": "Neutral, factual, informational tone"}
                        ]
                    }
                },
                # Step 6a: Positive branch
                {
                    "id": "template_positive",
                    "type": "custom",
                    "position": {"x": 1600, "y": 100},
                    "data": {
                        "type": "TEMPLATE_TRANSFORM",
                        "label": "正面报告",
                        "template": "✅ **正面内容分析报告**\n\n来源: Post #{{input.post_id}}\n分析: {{llm_analyze.output}}\n\n建议: 推荐置顶或加入精华帖。"
                    }
                },
                # Step 6b: Negative branch
                {
                    "id": "template_negative",
                    "type": "custom",
                    "position": {"x": 1600, "y": 300},
                    "data": {
                        "type": "TEMPLATE_TRANSFORM",
                        "label": "负面报告",
                        "template": "⚠️ **负面内容分析报告**\n\n来源: Post #{{input.post_id}}\n分析: {{llm_analyze.output}}\n\n建议: 需人工审核，考虑隐藏或回复。"
                    }
                },
                # Step 6c: Neutral branch
                {
                    "id": "template_neutral",
                    "type": "custom",
                    "position": {"x": 1600, "y": 500},
                    "data": {
                        "type": "TEMPLATE_TRANSFORM",
                        "label": "中性报告",
                        "template": "📝 **中性内容分析报告**\n\n来源: Post #{{input.post_id}}\n分析: {{llm_analyze.output}}\n\n建议: 正常展示，无需干预。"
                    }
                },
                # Step 7: End nodes
                {
                    "id": "end_pos",
                    "type": "custom",
                    "position": {"x": 1900, "y": 100},
                    "data": {
                        "type": "END",
                        "label": "正面结果",
                        "output_mapping": {"report": "{{template_positive.output}}", "tone": "{{classifier_tone.output}}"}
                    }
                },
                {
                    "id": "end_neg",
                    "type": "custom",
                    "position": {"x": 1900, "y": 300},
                    "data": {
                        "type": "END",
                        "label": "负面结果",
                        "output_mapping": {"report": "{{template_negative.output}}", "tone": "{{classifier_tone.output}}"}
                    }
                },
                {
                    "id": "end_neu",
                    "type": "custom",
                    "position": {"x": 1900, "y": 500},
                    "data": {
                        "type": "END",
                        "label": "中性结果",
                        "output_mapping": {"report": "{{template_neutral.output}}", "tone": "{{classifier_tone.output}}"}
                    }
                }
            ],
            "edges": [
                {"id": "e1", "source": "start_1", "target": "http_post"},
                {"id": "e2", "source": "http_post", "target": "code_parse"},
                {"id": "e3", "source": "code_parse", "target": "var_assign_1"},
                {"id": "e4", "source": "var_assign_1", "target": "llm_analyze"},
                {"id": "e5", "source": "llm_analyze", "target": "classifier_tone"},
                {"id": "e6", "source": "classifier_tone", "target": "template_positive", "sourceHandle": "class-positive"},
                {"id": "e7", "source": "classifier_tone", "target": "template_negative", "sourceHandle": "class-negative"},
                {"id": "e8", "source": "classifier_tone", "target": "template_neutral", "sourceHandle": "class-neutral"},
                {"id": "e9", "source": "template_positive", "target": "end_pos"},
                {"id": "e10", "source": "template_negative", "target": "end_neg"},
                {"id": "e11", "source": "template_neutral", "target": "end_neu"},
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

    # Clean existing test workflows (keep user's own "未命名工作流")
    cur.execute(
        "DELETE FROM workflows WHERE user_id = ? AND name LIKE '%测试%' OR name LIKE '%翻译器%' OR name LIKE '%优化%' OR name LIKE '%采集%' OR name LIKE '%协作%' OR name LIKE '%链路%' OR name LIKE '%客服%'",
        (USER_ID,),
    )

    workflows = [
        wf1_customer_router(),
        wf2_batch_translator(),
        wf3_iterative_improver(),
        wf4_data_pipeline(),
        wf5_agent_chain(),
        wf6_full_pipeline(),
    ]

    # Build column list from first workflow
    columns = list(workflows[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)

    for wf in workflows:
        values = [wf[c] for c in columns]
        cur.execute(f"INSERT INTO workflows ({col_names}) VALUES ({placeholders})", values)
        print(f"✅ Inserted: {wf['name']}")
        print(f"   API Key: {wf['api_key']}")
        print(f"   Trigger: curl -X POST http://localhost:8000/api/workflows/trigger/{wf['api_key']} -H 'Content-Type: application/json' -d '{{\"inputs\": {{}}}}'")
        print()

    conn.commit()
    conn.close()

    print(f"\n🎉 Done! {len(workflows)} workflows inserted.")
    print("Go to the Workflows page in the UI to see them.")


if __name__ == "__main__":
    main()
