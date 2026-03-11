"""Quickstart example for fim-one.

Demonstrates two usage patterns:
  1. ReAct Agent  -- a single agent that reasons and calls tools in a loop.
  2. DAG Planner  -- an LLM decomposes a goal into a dependency graph,
                     executes steps concurrently, then analyzes results.

Configuration
-------------
Set the following environment variables before running:

    export LLM_API_KEY="sk-..."
    export LLM_BASE_URL="https://api.openai.com/v1"   # any OpenAI-compatible endpoint
    export LLM_MODEL="gpt-4o"                          # model identifier

Usage::

    python examples/quickstart.py
"""

from __future__ import annotations

import asyncio
import os

from fim_one.core.agent import ReActAgent
from fim_one.core.model import OpenAICompatibleLLM
from fim_one.core.planner import DAGExecutor, DAGPlanner, PlanAnalyzer
from fim_one.core.tool import ToolRegistry
from fim_one.core.tool.builtin.python_exec import PythonExecTool


def build_llm() -> OpenAICompatibleLLM:
    """Create an LLM instance from environment variables."""
    api_key = os.environ.get("LLM_API_KEY", "")
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o")

    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY is not set. "
            "Export it before running: export LLM_API_KEY='sk-...'"
        )

    return OpenAICompatibleLLM(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )


def build_tool_registry() -> ToolRegistry:
    """Create a tool registry with the built-in Python executor."""
    registry = ToolRegistry()
    registry.register(PythonExecTool())
    return registry


# ======================================================================
# Example 1: ReAct Agent
# ======================================================================


async def react_agent_example() -> None:
    """Run a single query through a ReAct agent with a Python tool."""
    print("=" * 60)
    print("Example 1: ReAct Agent")
    print("=" * 60)

    llm = build_llm()
    tools = build_tool_registry()
    agent = ReActAgent(llm=llm, tools=tools, max_iterations=10)

    query = "Calculate the factorial of 10 using Python."
    print(f"\nQuery: {query}\n")

    result = await agent.run(query)

    print(f"Answer: {result.answer}")
    print(f"Iterations: {result.iterations}")
    print(f"Steps taken: {len(result.steps)}")

    # Print the reasoning trace.
    for i, step in enumerate(result.steps, 1):
        action = step.action
        print(f"\n  Step {i} [{action.type}]")
        print(f"    Reasoning: {action.reasoning[:120]}...")
        if action.type == "tool_call":
            print(f"    Tool: {action.tool_name}")
            if step.observation:
                print(f"    Observation: {step.observation.strip()[:200]}")
            if step.error:
                print(f"    Error: {step.error}")

    print()


# ======================================================================
# Example 2: DAG Planner + Concurrent Executor + Analyzer
# ======================================================================


async def dag_planner_example() -> None:
    """Plan a multi-step goal as a DAG, execute it, then analyze results."""
    print("=" * 60)
    print("Example 2: DAG Planner")
    print("=" * 60)

    llm = build_llm()
    tools = build_tool_registry()

    # --- Step A: Plan ---
    planner = DAGPlanner(llm=llm)
    goal = (
        "Compare the performance of bubble sort and Python's built-in "
        "sorted() on a list of 5000 random integers. Report the time "
        "taken by each approach."
    )
    print(f"\nGoal: {goal}\n")

    plan = await planner.plan(goal)

    print("Execution plan:")
    for step in plan.steps:
        deps = ", ".join(step.dependencies) if step.dependencies else "none"
        hint = step.tool_hint or "-"
        print(f"  [{step.id}] {step.task}")
        print(f"         deps: {deps} | tool_hint: {hint}")
    print()

    # --- Step B: Execute ---
    agent = ReActAgent(llm=llm, tools=tools, max_iterations=15)
    executor = DAGExecutor(agent=agent, max_concurrency=3)

    plan = await executor.execute(plan)

    print("Execution results:")
    for step in plan.steps:
        status = step.status
        result_preview = (step.result or "(no result)")[:200]
        print(f"  [{step.id}] status={status}")
        print(f"         result: {result_preview}")
    print()

    # --- Step C: Analyze ---
    analyzer = PlanAnalyzer(llm=llm)
    analysis = await analyzer.analyze(goal, plan)

    print("Analysis:")
    print(f"  Achieved:   {analysis.achieved}")
    print(f"  Confidence: {analysis.confidence:.2f}")
    print(f"  Reasoning:  {analysis.reasoning[:300]}")
    if analysis.final_answer:
        print(f"  Answer:     {analysis.final_answer[:300]}")
    print()


# ======================================================================
# Main
# ======================================================================


async def main() -> None:
    """Run all examples sequentially."""
    await react_agent_example()
    await dag_planner_example()


if __name__ == "__main__":
    asyncio.run(main())
